"""Build Garmin workout JSON from a compact spec (no network, unit-tested)."""

from __future__ import annotations

import re
from typing import Any

from garminconnect.workout import (
    RunningWorkout,
    StrengthWorkout,
    WorkoutSegment,
    create_strength_set,
)

from .strength import resolve_exercise

STEP_TYPES = {
    "warmup": (1, "warmup", 1),
    "cooldown": (2, "cooldown", 2),
    "interval": (3, "interval", 3),
    "recovery": (4, "recovery", 4),
    "rest": (5, "rest", 5),
}
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}

_PACE_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*(?:-\s*(\d{1,2}):(\d{2}))?\s*$")


def _pace_to_mps(m: int, s: int) -> float:
    return 1000.0 / (m * 60 + s)


def parse_pace_range(text: str) -> tuple[float, float]:
    """'4:30-4:40' (min/km) -> (lower_mps, upper_mps); slower pace is lower."""
    m = _PACE_RE.match(text)
    if not m:
        raise ValueError(f"Bad pace '{text}', expected like '4:30-4:40' (min/km)")
    a = _pace_to_mps(int(m[1]), int(m[2]))
    b = _pace_to_mps(int(m[3]), int(m[4])) if m[3] else a
    return (min(a, b), max(a, b))


def _target(step: dict[str, Any]) -> dict[str, Any]:
    if step.get("pace"):
        lo, hi = parse_pace_range(step["pace"])
        return {
            "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6},
            "targetValueOne": lo,
            "targetValueTwo": hi,
        }
    if step.get("hr_zone"):
        z = int(step["hr_zone"])
        if not 1 <= z <= 5:
            raise ValueError("hr_zone must be 1..5")
        return {
            "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4},
            "zoneNumber": z,
        }
    if step.get("hr"):
        lo, hi = sorted(int(x) for x in step["hr"])
        return {
            "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4},
            "targetValueOne": float(lo),
            "targetValueTwo": float(hi),
        }
    return {"targetType": dict(NO_TARGET)}


def _end(step: dict[str, Any]) -> dict[str, Any]:
    if step.get("duration_s"):
        return {
            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True},
            "endConditionValue": float(step["duration_s"]),
        }
    if step.get("distance_m"):
        return {
            "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True},
            "endConditionValue": float(step["distance_m"]),
        }
    return {
        "endCondition": {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True},
    }


class _Order:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def _estimate(step: dict[str, Any]) -> float:
    if step.get("type") == "repeat":
        return int(step.get("times", 1)) * sum(_estimate(s) for s in step.get("steps", []))
    if step.get("duration_s"):
        return float(step["duration_s"])
    if step.get("distance_m"):
        pace_mps = 1000 / 330  # ~5:30/km fallback
        if step.get("pace"):
            lo, hi = parse_pace_range(step["pace"])
            pace_mps = (lo + hi) / 2
        return float(step["distance_m"]) / pace_mps
    return 0.0


def _build_steps(steps: list[dict[str, Any]], order: _Order) -> list[dict[str, Any]]:
    out = []
    for st in steps:
        kind = (st.get("type") or "interval").lower()
        if kind == "repeat":
            times = int(st.get("times") or st.get("iterations") or 0)
            if times < 1 or not st.get("steps"):
                raise ValueError("repeat needs times>=1 and steps")
            so = order.next()
            out.append(
                {
                    "type": "RepeatGroupDTO",
                    "stepOrder": so,
                    "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
                    "numberOfIterations": times,
                    "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False},
                    "endConditionValue": float(times),
                    "smartRepeat": False,
                    "skipLastRestStep": bool(st.get("skip_last_rest", False)),
                    "workoutSteps": _build_steps(st["steps"], order),
                }
            )
            continue
        if kind not in STEP_TYPES:
            raise ValueError(f"Unknown step type '{kind}'")
        tid, tkey, disp = STEP_TYPES[kind]
        d = {
            "type": "ExecutableStepDTO",
            "stepOrder": order.next(),
            "stepType": {"stepTypeId": tid, "stepTypeKey": tkey, "displayOrder": disp},
            **_end(st),
            **_target(st),
        }
        if st.get("note"):
            d["description"] = str(st["note"])
        out.append(d)
    return out


def build_run_workout(name: str, steps: list[dict[str, Any]], description: str | None = None) -> dict[str, Any]:
    order = _Order()
    built = _build_steps(steps, order)
    w = RunningWorkout(
        workoutName=name,
        description=description,
        estimatedDurationInSecs=int(sum(_estimate(s) for s in steps)),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
                workoutSteps=[],
            )
        ],
    )
    d = w.to_dict()
    d["workoutSegments"][0]["workoutSteps"] = built
    return d


def build_strength_workout(name: str, blocks: list[dict[str, Any]], description: str | None = None) -> dict[str, Any]:
    """blocks: [{"exercise": "Barbell Deadlift", "sets": 4, "reps": 4, "weight_kg": 85, "rest_s": 120}]"""
    steps = []
    order = 1
    est = 0.0
    for b in blocks:
        e = resolve_exercise(b["exercise"])
        sets, reps = int(b["sets"]), int(b["reps"])
        rest = float(b.get("rest_s", 120))
        steps.append(
            create_strength_set(
                e["category"], order, sets, reps, rest,
                exercise_name=e["exercise"], weight_kg=b.get("weight_kg"), description=b.get("note"),
            )
        )
        order += 3
        est += sets * (reps * 4 + rest)
    w = StrengthWorkout(
        workoutName=name,
        description=description,
        estimatedDurationInSecs=int(est),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5},
                workoutSteps=steps,
            )
        ],
    )
    return w.to_dict()
