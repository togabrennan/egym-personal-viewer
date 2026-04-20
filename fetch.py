#!/usr/bin/env python3
"""Fetch eGym / NetpulseFitness workout history and strength measurements.

Reads credentials from settings.json (or env vars / CLI flags). Writes
data files into ./data/ relative to this script.

Credentials precedence (highest to lowest):
  1. CLI flags (--brand / --username / --password)
  2. Environment variables (EGYM_BRAND / EGYM_USERNAME / EGYM_PASSWORD)
  3. settings.json (same directory as this script)

Usage:
    cp settings.json.example settings.json      # then edit
    python3 fetch.py
    python3 fetch.py --brand FOO --username me@x.com --password 'secret'
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
SETTINGS_PATH = HERE / "settings.json"

HEADERS_BASE = {
    "x-np-user-agent": (
        "clientType=MOBILE_DEVICE; devicePlatform=IOS; deviceUid=egym-viewer; "
        "applicationName=NetpulseFitness; applicationVersion=3.11"
    ),
    "user-agent": "NetpulseFitness/3.11",
    "x-np-app-version": "3.11",
    "Accept": "application/json",
}

EGYM_API = "https://mobile-api.int.api.egym.com"


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        with SETTINGS_PATH.open() as f:
            return json.load(f)
    return {}


def resolve_credentials(args: argparse.Namespace) -> tuple[str, str, str]:
    settings = load_settings()
    egym_cfg = settings.get("egym") or {}

    brand = args.brand or os.environ.get("EGYM_BRAND") or egym_cfg.get("brand")
    username = args.username or os.environ.get("EGYM_USERNAME") or egym_cfg.get("username")
    password = args.password or os.environ.get("EGYM_PASSWORD") or egym_cfg.get("password")

    missing = [
        name for name, val in (("brand", brand), ("username", username), ("password", password))
        if not val
    ]
    if missing:
        sys.exit(
            f"Missing credentials: {', '.join(missing)}. "
            f"Set them in {SETTINGS_PATH.name}, via env vars (EGYM_BRAND/EGYM_USERNAME/EGYM_PASSWORD), "
            f"or pass --brand / --username / --password."
        )
    return brand, username, password


def build_base(brand: str) -> str:
    return f"https://{brand.lower()}.netpulse.com"


def login(base: str, username: str, password: str) -> tuple[str, str]:
    data = urllib.parse.urlencode({
        "username": username,
        "password": password,
        "relogin": "false",
    }).encode()
    req = urllib.request.Request(
        f"{base}/np/exerciser/login",
        data=data,
        method="POST",
        headers={**HEADERS_BASE, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        cookies = resp.headers.get_all("Set-Cookie") or []
        body = json.loads(resp.read())
    cookie_header = "; ".join(c.split(";", 1)[0] for c in cookies)
    return body["uuid"], cookie_header


def fetch(url: str, cookie: str) -> dict:
    req = urllib.request.Request(url, headers={**HEADERS_BASE, "Cookie": cookie})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {url}\nbody: {body[:500]}") from None


def rfc3339(dt: datetime) -> str:
    # Match Go's time.RFC3339 — required by Netpulse. URL-unencoded.
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_workouts(base: str, user_id: str, cookie: str, start: datetime, end: datetime) -> dict:
    url = (
        f"{base}/workouts/api/workouts/v2.3/exercisers/{user_id}/workouts"
        f"?completedAfter={rfc3339(start)}&completedBefore={rfc3339(end)}"
    )
    return fetch(url, cookie)


def get_bio_age(user_id: str, cookie: str) -> dict:
    url = f"{EGYM_API}/analysis/api/v1.0/exercisers/{user_id}/bioage"
    try:
        return fetch(url, cookie)
    except Exception as e:
        print(f"  bio-age fetch failed: {e}", file=sys.stderr)
        return {}


def get_strength_history(user_id: str, cookie: str, start: datetime, end: datetime) -> dict:
    """All strength-test records in date range. Dates are LocalDate (YYYY-MM-DD), not RFC3339."""
    url = (
        f"{EGYM_API}/measurements/api/v1.0/exercisers/{user_id}/strength"
        f"?startDate={start.date().isoformat()}&endDate={end.date().isoformat()}"
    )
    try:
        return fetch(url, cookie)
    except Exception as e:
        print(f"  strength fetch failed: {e}", file=sys.stderr)
        return {"strengthMeasurements": []}


def _attr(attrs: dict | None, key: str) -> tuple[object, object]:
    v = attrs.get(key) if attrs else None
    if isinstance(v, dict):
        return v.get("value"), v.get("unit")
    return None, None


def flatten_workouts(workouts: list) -> list[dict]:
    """Flatten nested workout/exercise structure into one row per set (or per exercise if no sets)."""
    rows = []
    for w in workouts:
        w_base = {
            "workout_code": w.get("code"),
            "workout_completed_at": w.get("completedAt"),
            "workout_created_at": w.get("createdAt"),
            "workout_timezone": w.get("timezone"),
            "workout_plan_label": w.get("workoutPlanLabel"),
            "workout_plan_group_type": w.get("workoutPlanGroupType"),
        }
        exercises = w.get("exercises") or []
        if not exercises:
            rows.append(w_base)
            continue
        for ex in exercises:
            src = ex.get("source") or {}
            attrs = ex.get("attributes") or {}
            cal_v, cal_u = _attr(attrs, "calories")
            ap_v, _ = _attr(attrs, "activity_points")
            dist_v, dist_u = _attr(attrs, "distance")
            dur_v, dur_u = _attr(attrs, "duration")
            hr_v, hr_u = _attr(attrs, "average_heart_rate")
            speed_v, speed_u = _attr(attrs, "speed")

            ex_base = {
                **w_base,
                "exercise_code": ex.get("code"),
                "exercise_library_code": ex.get("exerciseCode"),
                "exercise_name": ex.get("name"),
                "exercise_library": ex.get("libraryCode"),
                "exercise_completed_at": ex.get("completedAt"),
                "exercise_source": src.get("code"),
                "exercise_source_label": src.get("label"),
                "calories": cal_v,
                "calories_unit": cal_u,
                "activity_points": ap_v,
                "distance": dist_v,
                "distance_unit": dist_u,
                "duration": dur_v,
                "duration_unit": dur_u,
                "avg_heart_rate": hr_v,
                "avg_heart_rate_unit": hr_u,
                "speed": speed_v,
                "speed_unit": speed_u,
            }

            sets = attrs.get("sets_of_reps_and_weight_or_duration_and_weight")
            if isinstance(sets, list) and sets:
                for i, s in enumerate(sets, 1):
                    reps_v, reps_u = _attr(s, "reps")
                    wt_v, wt_u = _attr(s, "weight")
                    sd_v, sd_u = _attr(s, "duration")
                    rows.append({
                        **ex_base,
                        "set_index": i,
                        "set_total": len(sets),
                        "reps": reps_v,
                        "reps_unit": reps_u,
                        "weight": wt_v,
                        "weight_unit": wt_u,
                        "set_duration": sd_v,
                        "set_duration_unit": sd_u,
                    })
            else:
                rows.append(ex_base)
    return rows


def flatten_strength(strength: list) -> list[dict]:
    out = []
    for s in strength:
        ex = s.get("exercise") or {}
        st = s.get("strength") or {}
        # History endpoint puts a sentinel "1970-01-01" in strength.createdAt;
        # the real measurement timestamp is on the outer record.
        measured_at = s.get("createdAt") or st.get("createdAt")
        if isinstance(measured_at, str) and measured_at.startswith("1970-"):
            measured_at = st.get("createdAt") or None
            if isinstance(measured_at, str) and measured_at.startswith("1970-"):
                measured_at = None
        out.append({
            "exercise_name": ex.get("label"),
            "exercise_code": ex.get("code"),
            "body_region": s.get("bodyRegion"),
            "source": s.get("source"),
            "one_rep_max_kg": st.get("value"),
            "progress": st.get("progress"),
            "percentage_diff": st.get("percentageDiff"),
            "amount_diff_kg": st.get("amountDiff"),
            "measured_at": measured_at,
        })
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    cols = sorted({k for r in rows for k in r.keys()})
    preferred = [
        "workout_completed_at", "workout_code", "workout_plan_label", "workout_plan_group_type", "workout_timezone",
        "exercise_name", "exercise_library_code", "exercise_code", "exercise_completed_at",
        "exercise_source", "exercise_source_label",
        "set_index", "set_total", "reps", "reps_unit", "weight", "weight_unit",
        "set_duration", "set_duration_unit",
        "activity_points", "calories", "calories_unit",
        "distance", "distance_unit", "duration", "duration_unit",
        "avg_heart_rate", "avg_heart_rate_unit", "speed", "speed_unit",
        "exercise_library", "workout_created_at",
    ]
    cols = [c for c in preferred if c in cols] + [c for c in cols if c not in preferred]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch eGym / NetpulseFitness workout history.")
    p.add_argument("--brand", help="Gym brand / Netpulse subdomain (e.g. CITYFITNESS)")
    p.add_argument("--username", help="Member email")
    p.add_argument("--password", help="Member password")
    p.add_argument("--years", type=int, default=10, help="How many years back to scan (default: 10)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    brand, username, password = resolve_credentials(args)
    base = build_base(brand)

    DATA_DIR.mkdir(exist_ok=True)

    print(f"Logging in as {username} at {base} ...", file=sys.stderr)
    user_id, cookie = login(base, username, password)
    print(f"Logged in. userId={user_id}", file=sys.stderr)

    # Fetch workouts in yearly chunks going back N years.
    end = datetime.now(timezone.utc) + timedelta(days=1)
    earliest = end - timedelta(days=365 * args.years)

    all_workouts: list = []
    seen_ids: set[str] = set()
    cur_end = end
    while cur_end > earliest:
        cur_start = max(cur_end - timedelta(days=365), earliest)
        print(f"Fetching {cur_start.date()} -> {cur_end.date()} ...", file=sys.stderr)
        try:
            resp = get_workouts(base, user_id, cookie, cur_start, cur_end)
        except Exception as e:
            print(f"  error: {e}", file=sys.stderr)
            cur_end = cur_start
            continue
        workouts = resp.get("workouts", []) if isinstance(resp, dict) else (resp or [])
        new = 0
        for w in workouts:
            wid = w.get("code")
            if wid in seen_ids:
                continue
            seen_ids.add(wid)
            all_workouts.append(w)
            new += 1
        print(f"  {len(workouts)} returned, {new} new (total {len(all_workouts)})", file=sys.stderr)
        cur_end = cur_start

    # Full strength-test history (every measurement per machine).
    print("Fetching strength history ...", file=sys.stderr)
    strength_resp = get_strength_history(user_id, cookie, earliest, end)
    strength = strength_resp.get("strengthMeasurements", [])
    print(f"  {len(strength)} strength records", file=sys.stderr)

    # Bio-age snapshot (latest).
    print("Fetching bio-age ...", file=sys.stderr)
    bio_age = get_bio_age(user_id, cookie)

    # Raw payload for debugging / reference.
    (DATA_DIR / "workouts_raw.json").write_text(
        json.dumps({"workouts": all_workouts, "strength": strength, "bio_age": bio_age}, indent=2)
    )

    # Flattened forms.
    rows = flatten_workouts(all_workouts)
    strength_rows = flatten_strength(strength)

    write_csv(DATA_DIR / "workouts.csv", rows)

    (DATA_DIR / "workouts.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "brand": brand,
        "workout_count": len(all_workouts),
        "rows": rows,
        "strength": strength_rows,
        "bio_age": bio_age,
    }, indent=2, default=str))

    print(
        f"Wrote {len(rows)} rows ({len(all_workouts)} workouts, {len(strength_rows)} strength) "
        f"to {DATA_DIR.relative_to(HERE)}/",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
