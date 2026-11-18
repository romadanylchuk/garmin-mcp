"""Garmin Connect MCP server (stdio).

Read tools are safe. Write tools (anything that changes Garmin data) follow a
two-step pattern: first call returns a preview with confirm=False (default);
call again with confirm=True to apply. Every activity edit backs up the
original payload to ~/.garmin-mcp/backups/ first.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import strength, workouts
from .auth import NotLoggedInError, get_client, reset_client

BACKUP_DIR = Path("~/.garmin-mcp/backups").expanduser()

RO = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
RW = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
DEL = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

mcp = MCPServer(
    name="garmin",
    instructions=(
        "Personal Garmin Connect access for one athlete. Dates are YYYY-MM-DD in the "
        "athlete's local time. Distances in km, paces in min/km, weights in kg. "
        "Write tools return a preview unless confirm=true; show the preview to the user "
        "and only confirm after they agree. Garmin strength auto-detection is often wrong: "
        "the athlete's own description of sets is authoritative."
    ),
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _g():
    try:
        return get_client()
    except NotLoggedInError:
        raise
    except Exception:
        reset_client()
        return get_client()


def _today() -> str:
    return date.today().isoformat()


def _pace(mps: float | None) -> str | None:
    if not mps:
        return None
    sec = 1000 / mps
    return f"{int(sec // 60)}:{int(round(sec % 60)):02d}"


def _r(x: Any, n: int = 1) -> Any:
    return round(x, n) if isinstance(x, (int, float)) else x


def _activity_row(a: dict[str, Any]) -> dict[str, Any]:
    typ = (a.get("activityType") or {}).get("typeKey")
    row = {
        "id": a.get("activityId"),
        "date": (a.get("startTimeLocal") or "")[:16],
        "type": typ,
        "name": a.get("activityName"),
        "duration_min": _r((a.get("duration") or 0) / 60),
        "distance_km": _r((a.get("distance") or 0) / 1000, 2) or None,
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "elev_gain_m": _r(a.get("elevationGain"), 0),
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "anaerobic_te": a.get("anaerobicTrainingEffect"),
        "load": _r(a.get("activityTrainingLoad"), 0),
    }
    if typ and "run" in typ:
        row["pace"] = _pace(a.get("averageSpeed"))
        row["cadence"] = _r(a.get("averageRunningCadenceInStepsPerMinute"), 0)
    if a.get("avgPower"):
        row["avg_power"] = a.get("avgPower")
    return {k: v for k, v in row.items() if v not in (None, "", 0)}


def _backup(kind: str, ident: Any, payload: Any) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    p = BACKUP_DIR / f"{kind}_{ident}_{int(time.time())}.json"
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return str(p)


# --------------------------------------------------------------------------- #
# READ: activities
# --------------------------------------------------------------------------- #
@mcp.tool(annotations=RO)
def list_activities(start_date: str, end_date: str | None = None, activity_type: str | None = None) -> list[dict]:
    """Activities between dates (inclusive), newest first, compact rows.
    activity_type: running, cycling, swimming, strength_training, walking, ... (optional)."""
    acts = _g().get_activities_by_date(start_date, end_date or _today(), activitytype=activity_type, sortorder="desc")
    return [_activity_row(a) for a in acts]


@mcp.tool(annotations=RO)
def get_activity(activity_id: int, include_splits: bool = True) -> dict:
    """One activity: summary, laps/splits (pace, HR, cadence per lap) and HR time in zones."""
    g = _g()
    a = g.get_activity(str(activity_id))
    s = a.get("summaryDTO") or {}
    out: dict[str, Any] = {
        "id": activity_id,
        "name": a.get("activityName"),
        "type": (a.get("activityTypeDTO") or {}).get("typeKey"),
        "start": s.get("startTimeLocal"),
        "duration_min": _r((s.get("duration") or 0) / 60),
        "moving_min": _r((s.get("movingDuration") or 0) / 60),
        "distance_km": _r((s.get("distance") or 0) / 1000, 2),
        "pace": _pace(s.get("averageSpeed")),
        "gap_pace": _pace(s.get("avgGradeAdjustedSpeed")),
        "avg_hr": s.get("averageHR"),
        "max_hr": s.get("maxHR"),
        "cadence": _r(s.get("averageRunCadence"), 0),
        "elev_gain_m": _r(s.get("elevationGain"), 0),
        "elev_loss_m": _r(s.get("elevationLoss"), 0),
        "avg_power": s.get("averagePower"),
        "np": s.get("normalizedPower"),
        "aerobic_te": s.get("trainingEffect"),
        "anaerobic_te": s.get("anaerobicTrainingEffect"),
        "te_label": s.get("trainingEffectLabel"),
        "load": _r(s.get("activityTrainingLoad"), 0),
        "calories": s.get("calories"),
        "stamina_end": s.get("endStamina"),
        "description": a.get("description"),
    }
    try:
        zones = g.get_activity_hr_in_timezones(str(activity_id))
        out["hr_zones_min"] = {f"z{z['zoneNumber']}": _r(z["secsInZone"] / 60) for z in zones}
    except Exception:
        pass
    if include_splits:
        try:
            laps = (g.get_activity_splits(str(activity_id)) or {}).get("lapDTOs") or []
            out["laps"] = [
                {
                    "lap": i + 1,
                    "km": _r((l.get("distance") or 0) / 1000, 2),
                    "time": f"{int(l.get('duration', 0) // 60)}:{int(l.get('duration', 0) % 60):02d}",
                    "pace": _pace(l.get("averageSpeed")),
                    "hr": _r(l.get("averageHR"), 0),
                    "max_hr": _r(l.get("maxHR"), 0),
                    "cad": _r(l.get("averageRunCadence"), 0),
                    "elev": _r(l.get("elevationGain"), 0),
                    "power": l.get("averagePower"),
                    "intensity": l.get("intensityType"),
                }
                for i, l in enumerate(laps)
            ]
        except Exception:
            pass
    return {k: v for k, v in out.items() if v not in (None, "", [])}


@mcp.tool(annotations=RO)
def get_activity_timeseries(activity_id: int, every_s: int = 10) -> dict:
    """Per-sample metrics (HR, pace, cadence, power, elevation...) downsampled to ~every_s seconds.
    Use for drift/decoupling or interval analysis."""
    d = _g().get_activity_details(str(activity_id), maxchart=4000, maxpoly=0)
    keys = [m["key"] for m in sorted(d.get("metricDescriptors", []), key=lambda m: m["metricsIndex"])]
    wanted = {
        "sumElapsedDuration": "t", "directHeartRate": "hr", "directSpeed": "mps",
        "directRunCadence": "cad", "directPower": "w", "directElevation": "elev",
        "sumDistance": "dist_m", "directGradeAdjustedSpeed": "gap_mps",
    }
    idx = {wanted[k]: i for i, k in enumerate(keys) if k in wanted}
    rows, last_t = [], -1e9
    for m in d.get("activityDetailMetrics", []):
        v = m.get("metrics") or []
        t = v[idx["t"]] if "t" in idx and idx["t"] < len(v) else None
        if t is None or t - last_t < every_s:
            continue
        last_t = t
        rows.append({k: _r(v[i], 2) for k, i in idx.items() if i < len(v) and v[i] is not None})
    return {"columns": list(idx), "rows": rows, "note": "mps = m/s; pace min/km = 16.667/mps"}


@mcp.tool(annotations=RO)
def get_strength_sets(activity_id: int) -> dict:
    """Strength activity sets as recorded (exercise guess, reps, kg). 'alternatives' = other exercises the watch considered."""
    payload = _g().get_activity_exercise_sets(activity_id)
    return {"activity_id": activity_id, "sets": strength.summarize_sets(payload)}


@mcp.tool(annotations=RO)
def search_exercises(term: str) -> list[dict]:
    """Search Garmin's strength exercise catalog, e.g. 'front squat', 'romanian deadlift'."""
    return strength.search_exercises(term)


# --------------------------------------------------------------------------- #
# READ: health & performance
# --------------------------------------------------------------------------- #
@mcp.tool(annotations=RO)
def get_daily_summary(day: str | None = None) -> dict:
    """Day overview: steps, resting HR, stress, body battery, sleep score, HRV, training readiness."""
    g = _g()
    d = day or _today()
    s = g.get_user_summary(d)
    out: dict[str, Any] = {
        "date": d,
        "steps": s.get("totalSteps"),
        "resting_hr": s.get("restingHeartRate"),
        "rhr_7d": s.get("lastSevenDaysAvgRestingHeartRate"),
        "avg_stress": s.get("averageStressLevel"),
        "body_battery": {
            "wake": s.get("bodyBatteryAtWakeTime"),
            "high": s.get("bodyBatteryHighestValue"),
            "low": s.get("bodyBatteryLowestValue"),
            "now": s.get("bodyBatteryMostRecentValue"),
        },
        "intensity_min": (s.get("moderateIntensityMinutes") or 0) + 2 * (s.get("vigorousIntensityMinutes") or 0),
        "active_kcal": s.get("activeKilocalories"),
    }
    for name, fn in (("sleep", _sleep_compact), ("hrv", _hrv_compact), ("readiness", _readiness_compact)):
        try:
            out[name] = fn(g, d)
        except Exception as e:  # keep partial results
            out[name] = f"unavailable: {e.__class__.__name__}"
    return out


def _sleep_compact(g, d: str) -> dict:
    s = (g.get_sleep_data(d) or {}).get("dailySleepDTO") or {}
    h = lambda x: _r((x or 0) / 3600, 2)
    scores = s.get("sleepScores") or {}
    return {
        "total_h": h(s.get("sleepTimeSeconds")),
        "deep_h": h(s.get("deepSleepSeconds")),
        "light_h": h(s.get("lightSleepSeconds")),
        "rem_h": h(s.get("remSleepSeconds")),
        "awake_h": h(s.get("awakeSleepSeconds")),
        "score": (scores.get("overall") or {}).get("value"),
        "avg_resp": s.get("averageRespirationValue"),
        "avg_spo2": s.get("averageSpO2Value"),
    }


def _hrv_compact(g, d: str) -> dict | None:
    h = g.get_hrv_data(d)
    if not h:
        return None
    s = h.get("hrvSummary") or {}
    b = s.get("baseline") or {}
    return {
        "last_night_avg": s.get("lastNightAvg"),
        "weekly_avg": s.get("weeklyAvg"),
        "status": s.get("status"),
        "baseline": [b.get("balancedLow"), b.get("balancedUpper")],
    }


def _readiness_compact(g, d: str) -> dict | None:
    r = g.get_training_readiness(d)
    if not r:
        return None
    r = r[0] if isinstance(r, list) else r
    return {
        "score": r.get("score"),
        "level": r.get("level"),
        "feedback": r.get("feedbackShort"),
        "recovery_time_h": _r((r.get("recoveryTime") or 0) / 60),
        "acute_load": r.get("acuteLoad"),
    }


@mcp.tool(annotations=RO)
def get_sleep(day: str) -> dict:
    """Sleep for the night ending on `day`: stages, score, respiration, SpO2."""
    return _sleep_compact(_g(), day)


@mcp.tool(annotations=RO)
def get_wellness_range(start_date: str, end_date: str | None = None) -> list[dict]:
    """Per-day resting HR, HRV, sleep, body battery, stress over a range (max 60 days)."""
    g = _g()
    a = datetime.fromisoformat(start_date).date()
    b = datetime.fromisoformat(end_date).date() if end_date else date.today()
    if (b - a).days > 60:
        raise ValueError("Range too long (max 60 days)")
    rows = []
    d = a
    while d <= b:
        ds = d.isoformat()
        row: dict[str, Any] = {"date": ds}
        try:
            s = g.get_user_summary(ds)
            row |= {"rhr": s.get("restingHeartRate"), "stress": s.get("averageStressLevel"),
                    "bb_wake": s.get("bodyBatteryAtWakeTime"), "steps": s.get("totalSteps")}
        except Exception:
            pass
        try:
            hv = _hrv_compact(g, ds) or {}
            row |= {"hrv": hv.get("last_night_avg"), "hrv_status": hv.get("status")}
        except Exception:
            pass
        try:
            sl = _sleep_compact(g, ds)
            row |= {"sleep_h": sl["total_h"], "sleep_score": sl["score"]}
        except Exception:
            pass
        rows.append({k: v for k, v in row.items() if v is not None})
        d += timedelta(days=1)
    return rows


@mcp.tool(annotations=RO)
def get_training_status(day: str | None = None) -> dict:
    """Garmin Training Status, acute/chronic load, load focus, VO2max, recovery."""
    d = day or _today()
    g = _g()
    ts = g.get_training_status(d) or {}
    out: dict[str, Any] = {"date": d}
    try:
        latest = (ts.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
        dev = next(iter(latest.values()), {})
        acwr = dev.get("acuteTrainingLoadDTO") or {}
        out |= {
            "status": dev.get("trainingStatusFeedbackPhrase"),
            "acute_load": acwr.get("dailyTrainingLoadAcute"),
            "chronic_load": acwr.get("dailyTrainingLoadChronic"),
            "acwr": acwr.get("dailyAcuteChronicWorkloadRatio"),
            "acwr_status": acwr.get("acwrStatus"),
        }
    except Exception:
        pass
    try:
        bal = (ts.get("mostRecentTrainingLoadBalance") or {}).get("metricsTrainingLoadBalanceDTOMap") or {}
        b = next(iter(bal.values()), {})
        out["load_focus"] = {
            "low_aerobic": b.get("monthlyLoadAerobicLow"),
            "high_aerobic": b.get("monthlyLoadAerobicHigh"),
            "anaerobic": b.get("monthlyLoadAnaerobic"),
            "feedback": b.get("trainingBalanceFeedbackPhrase"),
        }
    except Exception:
        pass
    try:
        v = (ts.get("mostRecentVO2Max") or {}).get("generic") or {}
        out["vo2max_run"] = v.get("vo2MaxPreciseValue") or v.get("vo2MaxValue")
    except Exception:
        pass
    return out


@mcp.tool(annotations=RO)
def get_performance(day: str | None = None) -> dict:
    """Race predictions, lactate threshold (HR & pace), endurance score, personal records."""
    g = _g()
    d = day or _today()
    out: dict[str, Any] = {}
    try:
        rp = g.get_race_predictions()
        rp = rp[0] if isinstance(rp, list) and rp else rp
        fmt = lambda s: (f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}" if s else None)
        out["race_predictions"] = {k: fmt(rp.get(f"time{k}")) for k in ("5K", "10K", "HalfMarathon", "Marathon")}
    except Exception:
        pass
    try:
        lt = g.get_lactate_threshold(latest=True) or {}
        sp = lt.get("speed_and_heart_rate") or {}
        spd = sp.get("speed")
        # Garmin often reports LT speed as (m/s)/10, e.g. 0.36 for 3.6 m/s.
        mps = (spd * 10 if spd and spd < 1 else spd) if spd else None
        out["lactate_threshold"] = {"hr": sp.get("heartRate"), "pace": _pace(mps), "raw_speed": spd,
                                    "date": sp.get("calendarDate")}
    except Exception:
        pass
    try:
        es = g.get_endurance_score(d) or {}
        out["endurance_score"] = es.get("overallScore") or (es.get("enduranceScoreDTO") or {}).get("overallScore")
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------- #
# READ: workouts / calendar
# --------------------------------------------------------------------------- #
@mcp.tool(annotations=RO)
def list_workouts(limit: int = 30) -> list[dict]:
    """Saved workouts in Garmin Connect library."""
    ws = _g().get_workouts(0, limit)
    return [
        {"id": w.get("workoutId"), "name": w.get("workoutName"),
         "sport": (w.get("sportType") or {}).get("sportTypeKey"), "updated": (w.get("updateDate") or "")[:10]}
        for w in ws
    ]


@mcp.tool(annotations=RO)
def get_workout(workout_id: int) -> dict:
    """Full workout definition (steps, targets)."""
    return _g().get_workout_by_id(workout_id)


@mcp.tool(annotations=RO)
def get_calendar(year: int, month: int) -> list[dict]:
    """Scheduled workouts and activities on the Garmin calendar for a month (month 1-12)."""
    cal = _g().get_scheduled_workouts(year, month) or {}
    return [
        {"date": i.get("date"), "type": i.get("itemType"), "title": i.get("title"),
         "workout_id": i.get("workoutId"), "scheduled_id": i.get("id") if i.get("itemType") == "workout" else None,
         "activity_id": i.get("activityId") if i.get("itemType") == "activity" else None}
        for i in cal.get("calendarItems", [])
    ]


@mcp.tool(annotations=RO)
def garmin_get(method: str, args: dict | None = None) -> Any:
    """Escape hatch: call any read-only python-garminconnect `get_*` method by name,
    e.g. method='get_max_metrics', args={'cdate': '2026-11-18'}. Output may be large."""
    if not method.startswith("get_"):
        raise ValueError("Only get_* methods are allowed here")
    fn = getattr(_g(), method, None)
    if not callable(fn):
        raise ValueError(f"No such method: {method}")
    return fn(**(args or {}))


# --------------------------------------------------------------------------- #
# WRITE: activities
# --------------------------------------------------------------------------- #
@mcp.tool(annotations=RW)
def edit_strength_sets(
    activity_id: int,
    sets: list[dict],
    default_exercise: str | None = None,
    confirm: bool = False,
) -> dict:
    """Fix a strength activity's sets. `sets` = ordered list of ACTIVE sets, each
    {"exercise"?: "Barbell Front Squat", "reps"?: 4, "weight_kg"?: 90}; omitted fields keep the
    recorded value; `default_exercise` applies to items without exercise. Fewer items than recorded
    removes trailing sets; more items appends sets. confirm=false returns a before/after preview."""
    g = _g()
    before = g.get_activity_exercise_sets(activity_id)
    after = strength.apply_set_edits(before, sets, default_exercise=default_exercise)
    preview = strength.diff_summary(before, after)
    if not confirm:
        return {"preview": preview, "applied": False, "next": "Call again with confirm=true to save."}
    backup = _backup("exercise_sets", activity_id, before)
    g.set_activity_exercise_sets(activity_id, after)
    check = strength.summarize_sets(g.get_activity_exercise_sets(activity_id))
    return {"applied": True, "now": check, "backup": backup}


@mcp.tool(annotations=RW)
def update_activity_info(
    activity_id: int, name: str | None = None, description: str | None = None, confirm: bool = False
) -> dict:
    """Rename an activity and/or set its description/notes."""
    if not (name or description is not None):
        raise ValueError("Nothing to change")
    g = _g()
    if not confirm:
        cur = g.get_activity(str(activity_id))
        return {"current": {"name": cur.get("activityName"), "description": cur.get("description")},
                "new": {"name": name, "description": description}, "applied": False}
    if name:
        g.set_activity_name(str(activity_id), name)
    if description is not None:
        g.set_activity_description(str(activity_id), description)
    return {"applied": True}


# --------------------------------------------------------------------------- #
# WRITE: workouts
# --------------------------------------------------------------------------- #
def _finish_workout(payload: dict, schedule_date: str | None, confirm: bool) -> dict:
    if not confirm:
        return {"preview": payload, "applied": False, "next": "Call again with confirm=true to create."}
    g = _g()
    res = g.upload_workout(payload)
    wid = res.get("workoutId")
    out = {"applied": True, "workout_id": wid, "name": res.get("workoutName")}
    if schedule_date and wid:
        out["scheduled"] = g.schedule_workout(wid, schedule_date)
        out["note"] = "Sync the watch (Garmin Connect app) to get it on the device."
    return out


@mcp.tool(annotations=RW)
def create_run_workout(
    name: str, steps: list[dict], schedule_date: str | None = None, description: str | None = None, confirm: bool = False
) -> dict:
    """Create a structured run in Garmin Connect (optionally put it on the calendar).
    Step: {"type": warmup|interval|recovery|rest|cooldown|repeat,
           "duration_s"|"distance_m" (none = lap button),
           target "pace": "4:30-4:40" (min/km) | "hr_zone": 1-5 | "hr": [150,160], "note"?}
    Repeat: {"type": "repeat", "times": 2, "steps": [...], "skip_last_rest"?: true}"""
    return _finish_workout(workouts.build_run_workout(name, steps, description), schedule_date, confirm)


@mcp.tool(annotations=RW)
def create_strength_workout(
    name: str, blocks: list[dict], schedule_date: str | None = None, description: str | None = None, confirm: bool = False
) -> dict:
    """Create a strength workout. blocks: [{"exercise": "Barbell Deadlift", "sets": 4, "reps": 4,
    "weight_kg": 85, "rest_s": 120, "note"?: str}]. Exercise names from search_exercises."""
    return _finish_workout(workouts.build_strength_workout(name, blocks, description), schedule_date, confirm)


@mcp.tool(annotations=RW)
def schedule_workout(workout_id: int, day: str) -> dict:
    """Put an existing library workout on the calendar for `day`."""
    return _g().schedule_workout(workout_id, day)


@mcp.tool(annotations=DEL)
def unschedule_workout(scheduled_id: int, confirm: bool = False) -> dict:
    """Remove a scheduled workout from the calendar (scheduled_id from get_calendar). Library copy stays."""
    if not confirm:
        return {"applied": False, "next": "Call again with confirm=true."}
    _g().unschedule_workout(scheduled_id)
    return {"applied": True}


@mcp.tool(annotations=DEL)
def delete_workout(workout_id: int, confirm: bool = False) -> dict:
    """Delete a workout from the Garmin Connect library (backs it up first)."""
    g = _g()
    w = g.get_workout_by_id(workout_id)
    if not confirm:
        return {"applied": False, "workout": w.get("workoutName"), "next": "Call again with confirm=true."}
    backup = _backup("workout", workout_id, w)
    g.delete_workout(workout_id)
    return {"applied": True, "backup": backup}


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
