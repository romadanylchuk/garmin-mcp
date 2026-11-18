"""Pure helpers for strength-activity exercise sets (no network, unit-tested).

Garmin's `/activity-service/activity/{id}/exerciseSets` payload looks like:

    {"activityId": 1, "exerciseSets": [
        {"setType": "ACTIVE", "repetitionCount": 4, "weight": 90000.0,
         "duration": 30.1, "startTime": "2026-11-18T07:17:53.0",
         "exercises": [{"category": "SQUAT", "name": "BARBELL_FRONT_SQUAT",
                        "probability": 100.0}], ...},
        {"setType": "REST", "duration": 120.5, ...}, ...]}

`weight` is stored in GRAMS (same convention as workout `weightValue`).
PUT replaces the whole array, so we always edit a copy of what GET returned.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any

from garminconnect import exercises as catalog

GRAMS_PER_KG = 1000.0


class ExerciseNotFound(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Exercise catalog
# --------------------------------------------------------------------------- #
def resolve_exercise(spec: str) -> dict[str, str]:
    """Resolve a human name or CATEGORY[/NAME] key to {category, exercise, name}.

    Accepts: "Barbell Front Squat", "barbell front squat",
    "SQUAT/BARBELL_FRONT_SQUAT", "SQUAT".
    """
    s = spec.strip()
    hit = catalog.resolve(s)
    if hit:
        return hit
    low = s.lower()
    for e in catalog.EXERCISES:
        if e["name"].lower() == low:
            return e
    key = s.upper().replace(" ", "_")
    if "/" in key:
        cat, ex = key.split("/", 1)
        for e in catalog.EXERCISES:
            if e["category"] == cat and e["exercise"] == ex:
                return e
        if cat in catalog.CATEGORIES:
            raise ExerciseNotFound(f"Unknown exercise '{ex}' in category {cat}.")
    elif key in catalog.CATEGORIES:
        return {"name": key.title(), "category": key, "exercise": ""}
    suggestions = [e["name"] for e in search_exercises(s, limit=8)]
    raise ExerciseNotFound(
        f"Exercise '{spec}' not found. Closest: {', '.join(suggestions) or 'none'}"
    )


def search_exercises(term: str, limit: int = 20) -> list[dict[str, str]]:
    words = [w for w in term.lower().split() if w]
    out = [e for e in catalog.EXERCISES if all(w in e["name"].lower() for w in words)]
    out.sort(key=lambda e: len(e["name"]))
    return out[:limit]


def _display_name(category: str | None, name: str | None) -> str:
    if not category:
        return "Unknown"
    for e in catalog.EXERCISES:
        if e["category"] == category and (e["exercise"] or None) == (name or None):
            return e["name"]
    return f"{category}/{name}" if name else category


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def summarize_sets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Compact view of active sets: index, exercise, reps, weight_kg, duration."""
    rows = []
    n = 0
    for s in payload.get("exerciseSets") or []:
        if s.get("setType") != "ACTIVE":
            continue
        n += 1
        ex = (s.get("exercises") or [{}])
        top = max(ex, key=lambda e: e.get("probability") or 0) if ex else {}
        w = s.get("weight")
        rows.append(
            {
                "set": n,
                "exercise": _display_name(top.get("category"), top.get("name")),
                "key": f"{top.get('category')}/{top.get('name') or ''}".rstrip("/"),
                "reps": s.get("repetitionCount"),
                "weight_kg": round(w / GRAMS_PER_KG, 2) if w is not None else None,
                "duration_s": round(s["duration"], 1) if s.get("duration") else None,
                "alternatives": [
                    _display_name(e.get("category"), e.get("name"))
                    for e in ex
                    if e is not top
                ][:3],
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Editing
# --------------------------------------------------------------------------- #
def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return None


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.0")


def apply_set_edits(
    payload: dict[str, Any],
    sets: list[dict[str, Any]],
    default_exercise: str | None = None,
    new_set_rest_s: float = 120.0,
) -> dict[str, Any]:
    """Return a NEW payload with active sets replaced by `sets` (positional).

    Each item: {"exercise"?: str, "reps"?: int, "weight_kg"?: float}.
    Missing fields keep what the watch recorded; `default_exercise` fills any
    item without "exercise".
    - Fewer items than recorded active sets: trailing recorded sets (and the
      rest that follows each) are removed.
    - More items: extra sets are appended after the last one, each preceded by
      a rest of `new_set_rest_s`, timed right after the previous set.
    """
    if not sets:
        raise ValueError("sets must contain at least one set")
    new = copy.deepcopy(payload)
    all_sets: list[dict[str, Any]] = new.get("exerciseSets") or []
    active_idx = [i for i, s in enumerate(all_sets) if s.get("setType") == "ACTIVE"]
    if not active_idx:
        raise ValueError("Activity has no recorded active sets to edit")

    # Grow: append rest + cloned active sets.
    while len(active_idx) < len(sets):
        last = all_sets[active_idx[-1]]
        # Insert after the last set and its following rest (if any).
        insert_at = active_idx[-1] + 1
        if insert_at < len(all_sets) and all_sets[insert_at].get("setType") == "REST":
            prev_end_obj = all_sets[insert_at]
            insert_at += 1
            start = _parse_ts(prev_end_obj.get("startTime"))
            end = start + timedelta(seconds=prev_end_obj.get("duration") or 0) if start else None
            seq: list[dict[str, Any]] = []
        else:
            start = _parse_ts(last.get("startTime"))
            end = start + timedelta(seconds=last.get("duration") or 0) if start else None
            rest = {
                "setType": "REST",
                "duration": float(new_set_rest_s),
                "exercises": [],
                "repetitionCount": None,
                "weight": None,
            }
            if end:
                rest["startTime"] = _fmt_ts(end)
                end = end + timedelta(seconds=new_set_rest_s)
            seq = [rest]
        clone = copy.deepcopy(last)
        if end:
            clone["startTime"] = _fmt_ts(end)
        seq.append(clone)
        all_sets[insert_at:insert_at] = seq
        active_idx = [i for i, s in enumerate(all_sets) if s.get("setType") == "ACTIVE"]

    # Shrink: drop trailing active sets and the rest that directly follows each.
    drop: set[int] = set()
    for i in active_idx[len(sets):]:
        drop.add(i)
        if i + 1 < len(all_sets) and all_sets[i + 1].get("setType") == "REST":
            drop.add(i + 1)
    if drop:
        all_sets = [s for i, s in enumerate(all_sets) if i not in drop]
        active_idx = [i for i, s in enumerate(all_sets) if s.get("setType") == "ACTIVE"]

    for item, i in zip(sets, active_idx):
        s = all_sets[i]
        ex_spec = item.get("exercise") or default_exercise
        if ex_spec:
            e = resolve_exercise(ex_spec)
            s["exercises"] = [
                {"category": e["category"], "name": e["exercise"] or None, "probability": 100.0}
            ]
        if item.get("reps") is not None:
            reps = int(item["reps"])
            if reps < 0:
                raise ValueError("reps must be >= 0")
            s["repetitionCount"] = reps
        if item.get("weight_kg") is not None:
            kg = float(item["weight_kg"])
            if kg < 0 or kg > 1000:
                raise ValueError("weight_kg must be between 0 and 1000")
            s["weight"] = kg * GRAMS_PER_KG

    # Renumber messageIndex if present so it stays sequential.
    for n, s in enumerate(all_sets):
        if "messageIndex" in s:
            s["messageIndex"] = n
    new["exerciseSets"] = all_sets
    return new


def diff_summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {"before": summarize_sets(before), "after": summarize_sets(after)}
