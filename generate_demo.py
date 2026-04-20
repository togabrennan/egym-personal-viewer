#!/usr/bin/env python3
"""Generate deterministic mock data for screenshots / demos.

Writes a synthetic `data/workouts.json` that looks like a real export but
contains no personal information. Run `python3 fetch.py` to replace with
your real data.

Usage:
    python3 generate_demo.py
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "workouts.json"

# Deterministic — always produces the same data for repeatable screenshots.
random.seed(42)

BRAND = "DEMO_GYM"
USER_ID = "00000000-demo-0000-demo-000000000000"

# (display name for workout log, strength endpoint name, body region,
#  starting 1RM in kg, per-test growth in kg, starting working weight in kg)
MACHINES = [
    ("EGYM Leg Press",        "EGYM Leg press",         "LOWER", 200.0, 6.0,  90.0),
    ("EGYM Chest Press",      "EGYM Chest press",       "UPPER", 100.0, 5.0,  40.0),
    ("EGYM Lat Pulldown",     "EGYM Lat pulldown",      "UPPER", 130.0, 4.0,  55.0),
    ("EGYM Seated Row",       "EGYM Seated row",        "UPPER", 115.0, 4.0,  50.0),
    ("EGYM Shoulder Press",   "EGYM Shoulder press",    "UPPER",  85.0, 5.0,  35.0),
    ("EGYM Leg Extension",    "EGYM Leg extension",     "LOWER", 125.0, 3.0,  55.0),
    ("EGYM Leg Curl",         "EGYM Leg curl",          "LOWER",  90.0, 2.0,  40.0),
    ("EGYM Back Extension",   "EGYM Back Extension",    "CORE",  115.0, 4.0,  50.0),
    ("EGYM Abdominal Crunch", "EGYM Abdominal crunch",  "CORE",   75.0, 5.0,  32.0),
    ("EGYM Triceps",          "EGYM Triceps",           "UPPER", 105.0, 5.0,  45.0),
    ("EGYM Bicep Curl",       "EGYM Bicep curl",        "UPPER",  45.0, 1.5, 18.0),
]

NOW = datetime(2026, 4, 20, 15, 0, tzinfo=timezone.utc)
WINDOW_DAYS = 63  # 9 weeks of history

STRENGTH_TEST_DAYS = [56, 28, 0]  # three tests across the window

def pick_workout_days(window_days: int, required: list[int]) -> list[int]:
    """Pick workout days with >=2-day gaps globally — never two days in a row,
    averaging 2-3 workouts per week. Always includes `required` days
    (strength-test days) and drops any adjacent picks around them so we keep
    the no-consecutive-days invariant."""
    required_set = {d for d in required if 0 <= d < window_days}
    days = []
    cursor = random.choice([0, 1, 2])
    while cursor < window_days:
        days.append(cursor)
        cursor += random.choice([2, 2, 3, 3, 3, 4])
    combined = sorted(set(days) | required_set)
    # Walk forward and drop any day adjacent to the previous kept day. Required
    # days win ties so we never drop them.
    result = []
    for d in combined:
        if result and d - result[-1] < 2:
            # conflict: prefer the required day if either is required.
            if d in required_set and result[-1] not in required_set:
                result[-1] = d
            # else keep existing result[-1] and skip d
        else:
            result.append(d)
    return sorted(result, reverse=True)  # most-recent first

WORKOUT_DAYS = pick_workout_days(WINDOW_DAYS, STRENGTH_TEST_DAYS)

# Machines that should show a DOWN trend at the latest test.
DECLINING_MACHINES = {"EGYM Leg extension"}

def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def day_at(days_ago: int, hour: int = 15, minute: int = 0) -> datetime:
    return (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0, microsecond=0)


# Build strength test history. Most machines trend up; DECLINING_MACHINES regress
# at the latest test so the 1RM Trend table shows both ▲ and ▼.
# STRENGTH_TEST_DAYS = [56, 28, 0] is already oldest→newest (days-ago descending).
test_order = STRENGTH_TEST_DAYS
strength_rows = []
for mi, (_, strength_name, region, one_rm, growth, _) in enumerate(MACHINES):
    for test_idx, days_ago in enumerate(test_order):
        when = day_at(days_ago, hour=17, minute=40) + timedelta(minutes=mi * 2)
        # Standard up-trend: baseline + growth * test_idx.
        value = one_rm + growth * test_idx + random.uniform(-1.5, 1.5)
        # For declining machines, flip the latest test into a regression.
        if strength_name in DECLINING_MACHINES and test_idx == len(test_order) - 1:
            value = one_rm + growth * (test_idx - 1) - growth * 2 + random.uniform(-1.5, 1.5)
        strength_rows.append({
            "exercise_name": strength_name,
            "exercise_code": str(990 + mi),
            "body_region": region,
            "source": "fitness_machine",
            "one_rep_max_kg": round(value),
            "progress": None,
            "percentage_diff": None,
            "amount_diff_kg": None,
            "measured_at": iso(when),
        })


# Build workout log. Each gym day = full 11-machine circuit, 1 set each.
# Working weight ramps linearly over the window.
workout_rows = []
for workout_idx, days_ago in enumerate(WORKOUT_DAYS):
    day = day_at(days_ago, hour=15, minute=0)
    workout_code_base = f"demo-{days_ago:03d}"
    # Progress 0 → 1 from oldest to newest. WORKOUT_DAYS is sorted desc (oldest first),
    # so max days_ago = oldest = progress 0; min days_ago = newest = progress 1.
    span = max(WORKOUT_DAYS) - min(WORKOUT_DAYS) or 1
    progress = (max(WORKOUT_DAYS) - days_ago) / span
    for mi, (log_name, _, _, _, growth, start_working) in enumerate(MACHINES):
        # Slight per-workout noise, but kept well below per-step growth so the trend is clear.
        noise = random.uniform(-0.25, 0.25)
        working_weight = round(start_working + growth * progress + noise, 1)
        reps = random.choice([12, 15, 15, 15, 18, 20])
        when = day.replace(minute=mi * 3)
        workout_rows.append({
            "workout_code": f"{workout_code_base}-m{mi}",
            "workout_completed_at": iso(when),
            "workout_created_at": iso(when + timedelta(minutes=2)),
            "workout_timezone": "America/New_York",
            "workout_plan_label": None,
            "workout_plan_group_type": None,
            "exercise_code": f"demo-ex-{mi}-{days_ago}",
            "exercise_library_code": str(580 + mi),
            "exercise_name": log_name,
            "exercise_library": "egym",
            "exercise_completed_at": iso(when),
            "exercise_source": "fitness_machine",
            "exercise_source_label": "Fitness Machine",
            "calories": random.randint(25, 75),
            "calories_unit": "kcal",
            "activity_points": random.randint(12, 45),
            "distance": None,
            "distance_unit": None,
            "duration": None,
            "duration_unit": None,
            "avg_heart_rate": None,
            "avg_heart_rate_unit": None,
            "speed": None,
            "speed_unit": None,
            "set_index": 1,
            "set_total": 1,
            "reps": reps,
            "reps_unit": "unit",
            "weight": working_weight,
            "weight_unit": "kg",
            "set_duration": None,
            "set_duration_unit": None,
        })


# Build bio-age snapshot (latest-only).
def bio_entry(value, diff=None, pct=None, dir_=None, state=None):
    e = {
        "value": value,
        "progress": dir_,
        "percentageDiff": pct,
        "amountDiff": diff,
        "createdAt": iso(day_at(0, hour=17)),
        "timezone": "America/New_York",
    }
    if state is not None:
        e["musclesState"] = state
    return e

bio_age = {
    "totalDetails": {
        "totalBioAge": bio_entry(34, diff=-3, pct=-8.1, dir_="down"),
        "quote": {"text": "Great progress this quarter!", "id": "demo.quote.total"},
    },
    "muscleDetails": {
        "upperBodyAge": bio_entry(32, state="BALANCED"),
        "coreAge": bio_entry(30, state="BALANCED"),
        "lowerBodyAge": bio_entry(35, state="BALANCED"),
        "muscleBioAge": bio_entry(32),
        "quote": {"text": "Balanced development.", "id": "demo.quote.muscle"},
    },
    "metabolicDetails": {
        "metabolicAge": bio_entry(36, diff=-2, pct=-5.3, dir_="down"),
        "bodyFat": bio_entry(18.5),
        "bmi": bio_entry(24.1),
        "waistToHipRatio": None,
        "quote": {"text": "Keep it up.", "id": "demo.quote.metabolic"},
    },
    "cardioDetails": {
        "cardioAge": bio_entry(29, diff=-6, pct=-17.1, dir_="down"),
        "restingHeartRate": bio_entry(54),
        "systolicPressure": None,
        "diastolicPressure": None,
        "vo2max": bio_entry(48.2),
        "quote": {"text": "Your cardio is ahead of calendar age.", "id": "demo.quote.cardio"},
    },
    "flexibilityDetails": None,
}


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": iso(NOW),
        "user_id": USER_ID,
        "brand": BRAND,
        "workout_count": len(WORKOUT_DAYS),
        "rows": workout_rows,
        "strength": strength_rows,
        "bio_age": bio_age,
    }, indent=2))
    print(
        f"Wrote demo data to {OUT.relative_to(HERE)}:\n"
        f"  {len(WORKOUT_DAYS)} gym days, {len(workout_rows)} sets, "
        f"{len(strength_rows)} strength records.\n"
        f"Run `python3 fetch.py` to replace with your real data."
    )


if __name__ == "__main__":
    main()
