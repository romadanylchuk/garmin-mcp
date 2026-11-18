import json

import pytest

from garmin_mcp import strength, workouts


def _set(kind, start, dur, cat=None, name=None, reps=None, grams=None, idx=0):
    s = {"setType": kind, "startTime": start, "duration": dur, "messageIndex": idx,
         "repetitionCount": reps, "weight": grams,
         "exercises": [{"category": cat, "name": name, "probability": 60.0}] if cat else []}
    return s


@pytest.fixture
def deadlift_payload():
    # Mirrors activity 25116699521 as the watch recorded it.
    return {"activityId": 25116699521, "exerciseSets": [
        _set("ACTIVE", "2026-11-18T07:33:20.0", 30.2, "DEADLIFT", "BARBELL_DEADLIFT", 5, 80000.0, 0),
        _set("REST", "2026-11-18T07:33:50.0", 131.0, idx=1),
        _set("ACTIVE", "2026-11-18T07:36:01.0", 25.5, "ROW", None, 4, 85000.0, 2),
        _set("REST", "2026-11-18T07:36:27.0", 140.1, idx=3),
        _set("ACTIVE", "2026-11-18T07:38:47.0", 23.6, "ROW", None, 4, 85000.0, 4),
        _set("REST", "2026-11-18T07:39:11.0", 122.5, idx=5),
        _set("ACTIVE", "2026-11-18T07:41:13.0", 27.2, "DEADLIFT", "BARBELL_DEADLIFT", 4, 85000.0, 6),
        _set("REST", "2026-11-18T07:41:40.0", 122.0, idx=7),
        _set("ACTIVE", "2026-11-18T07:43:42.0", 30.7, "DEADLIFT", "BARBELL_DEADLIFT", 4, 85000.0, 8),
    ]}


def test_resolve_variants():
    assert strength.resolve_exercise("Barbell Front Squat")["exercise"] == "BARBELL_FRONT_SQUAT"
    assert strength.resolve_exercise("barbell front squat")["category"] == "SQUAT"
    assert strength.resolve_exercise("SQUAT/BARBELL_FRONT_SQUAT")["name"] == "Barbell Front Squat"
    assert strength.resolve_exercise("deadlift")["category"] == "DEADLIFT"
    with pytest.raises(strength.ExerciseNotFound) as e:
        strength.resolve_exercise("frnt sqat")
    assert "Closest" in str(e.value)


def test_summarize(deadlift_payload):
    rows = strength.summarize_sets(deadlift_payload)
    assert [r["reps"] for r in rows] == [5, 4, 4, 4, 4]
    assert rows[0]["weight_kg"] == 80
    assert rows[0]["exercise"] == "Barbell Deadlift"


def test_fix_today(deadlift_payload):
    before = json.dumps(deadlift_payload)
    after = strength.apply_set_edits(
        deadlift_payload, [{"reps": 4}, {}, {}, {}, {}], default_exercise="Barbell Deadlift")
    assert json.dumps(deadlift_payload) == before  # input untouched
    rows = strength.summarize_sets(after)
    assert all(r["exercise"] == "Barbell Deadlift" for r in rows)
    assert [r["reps"] for r in rows] == [4] * 5
    assert [r["weight_kg"] for r in rows] == [80, 85, 85, 85, 85]
    assert len(after["exerciseSets"]) == 9


def test_shrink_and_grow(deadlift_payload):
    small = strength.apply_set_edits(deadlift_payload, [{"weight_kg": 100}, {}])
    assert len(strength.summarize_sets(small)) == 2
    assert small["exerciseSets"][-1]["setType"] == "REST" or small["exerciseSets"][-1]["setType"] == "ACTIVE"
    assert [s["messageIndex"] for s in small["exerciseSets"]] == list(range(len(small["exerciseSets"])))
    big = strength.apply_set_edits(deadlift_payload, [{}] * 7, default_exercise="Barbell Deadlift")
    rows = strength.summarize_sets(big)
    assert len(rows) == 7
    kinds = [s["setType"] for s in big["exerciseSets"]]
    assert all(kinds[i] != kinds[i + 1] for i in range(len(kinds) - 1)), kinds  # alternating
    starts = [s["startTime"] for s in big["exerciseSets"]]
    assert starts == sorted(starts)


def test_weight_validation(deadlift_payload):
    with pytest.raises(ValueError):
        strength.apply_set_edits(deadlift_payload, [{"weight_kg": -5}])


def test_run_workout():
    w = workouts.build_run_workout("2x15 TH", [
        {"type": "warmup", "duration_s": 900, "hr_zone": 2},
        {"type": "repeat", "times": 2, "skip_last_rest": True, "steps": [
            {"type": "interval", "duration_s": 900, "pace": "4:10-4:20"},
            {"type": "recovery", "duration_s": 180},
        ]},
        {"type": "cooldown", "distance_m": 2000},
    ])
    steps = w["workoutSegments"][0]["workoutSteps"]
    assert w["sportType"]["sportTypeKey"] == "running"
    assert [s["stepOrder"] for s in steps] == [1, 2, 5]
    rep = steps[1]
    assert rep["numberOfIterations"] == 2 and rep["workoutSteps"][0]["stepOrder"] == 3
    iv = rep["workoutSteps"][0]
    assert iv["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert iv["targetValueOne"] < iv["targetValueTwo"]  # slower pace = lower m/s
    assert round(1000 / iv["targetValueOne"]) == 260
    assert steps[2]["endCondition"]["conditionTypeKey"] == "distance"
    assert w["estimatedDurationInSecs"] > 900 + 2 * 1080


def test_strength_workout():
    w = workouts.build_strength_workout("Legs", [
        {"exercise": "Barbell Front Squat", "sets": 4, "reps": 4, "weight_kg": 90, "rest_s": 150},
        {"exercise": "Barbell Deadlift", "sets": 5, "reps": 4, "weight_kg": 85},
    ])
    steps = w["workoutSegments"][0]["workoutSteps"]
    assert len(steps) == 2 and steps[1]["stepOrder"] == 4
    ex = steps[0]["workoutSteps"][0]
    assert ex["category"] == "SQUAT" and ex["exerciseName"] == "BARBELL_FRONT_SQUAT"
    assert ex["weightValue"] == 90000.0
