#!/usr/bin/env python3
"""Convenience launcher: auto-fetch if data/workouts.json is stale, then serve.

Fetches fresh data only if `data/workouts.json` is missing or its
`generated_at` timestamp is from a day earlier than today (local time).
Pass `--force` to always fetch, or `--no-fetch` to skip the staleness
check entirely.

While serving, a background loop refetches every 2 hours as long as no
exercise has been recorded for today yet (or the most recent one is
within the refetch interval — a workout might still be in progress).

Usage:
    python3 serve.py                  # auto-fetch if stale, then serve on :8765
    python3 serve.py --port 9000      # pick a port
    python3 serve.py --force          # always fetch first
    python3 serve.py --no-fetch       # serve only, never fetch
"""
from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "workouts.json"
REFETCH_INTERVAL_SECONDS = 2 * 60 * 60


def is_stale() -> tuple[bool, str]:
    if not DATA.exists():
        return True, "no data/workouts.json yet"
    try:
        gen = json.loads(DATA.read_text()).get("generated_at")
        if not gen:
            return True, "workouts.json missing generated_at"
        # Compare date portion only (local time). Works for RFC3339/ISO 8601.
        gen_date = datetime.fromisoformat(gen.replace("Z", "+00:00")).astimezone().date()
        today = datetime.now().astimezone().date()
        if gen_date < today:
            return True, f"last fetched {gen_date} (today is {today})"
        return False, f"already fresh (synced {gen_date})"
    except Exception as e:
        return True, f"could not parse timestamp: {e}"


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


def _latest_set_today(rows: list) -> datetime | None:
    """Most recent exercise_completed_at dated today (local), or None."""
    today = datetime.now().astimezone().date()
    latest: datetime | None = None
    for row in rows or []:
        ts = _parse_ts(row.get("exercise_completed_at"))
        if ts is None:
            continue
        if ts.date() == today and (latest is None or ts > latest):
            latest = ts
    return latest


def should_refetch() -> tuple[bool, str]:
    """Refetch until a successful fetch has run at least one full interval
    after the most recent set — that's our signal that any late-syncing
    stragglers from the workout have been captured."""
    if not DATA.exists():
        return True, "no data/workouts.json yet"
    try:
        data = json.loads(DATA.read_text())
    except Exception as e:
        return True, f"could not read workouts.json: {e}"
    latest_set = _latest_set_today(data.get("rows") or [])
    if latest_set is None:
        return True, "no exercise recorded today yet"
    last_fetch = _parse_ts(data.get("generated_at"))
    if last_fetch is None:
        return True, "no prior fetch timestamp to compare against"
    gap = last_fetch - latest_set
    if gap < timedelta(seconds=REFETCH_INTERVAL_SECONDS):
        mins = int(gap.total_seconds() // 60)
        return True, f"last fetch was only {mins}m after the most recent set — workout may still be syncing"
    return False, f"most recent set captured {int(gap.total_seconds() // 3600)}h+ before last fetch — today's workout looks complete"


def run_fetch() -> None:
    """Run fetch.py as a subprocess so it has its own argv/globals and its
    failures can't take down the serve process."""
    result = subprocess.run(
        [sys.executable, str(HERE / "fetch.py")],
        cwd=HERE,
        check=False,
    )
    if result.returncode != 0:
        print("[serve] fetch failed; continuing with existing data", file=sys.stderr)


def refetch_loop() -> None:
    while True:
        time.sleep(REFETCH_INTERVAL_SECONDS)
        try:
            ok, reason = should_refetch()
            if ok:
                print(f"[serve] refetching: {reason}", file=sys.stderr)
                run_fetch()
            else:
                print(f"[serve] skipping scheduled refetch: {reason}", file=sys.stderr)
        except Exception as e:
            # Never let a bad tick kill the loop — we'll try again next interval.
            print(f"[serve] refetch tick failed: {e!r}; will retry next interval", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--force", action="store_true", help="always fetch before serving")
    p.add_argument("--no-fetch", action="store_true", help="never fetch")
    args = p.parse_args()

    if not args.no_fetch:
        should_fetch, reason = (True, "forced") if args.force else is_stale()
        if should_fetch:
            print(f"[serve] fetching: {reason}", file=sys.stderr)
            run_fetch()
        else:
            print(f"[serve] skipping fetch: {reason}", file=sys.stderr)
        threading.Thread(target=refetch_loop, daemon=True).start()
        hours = REFETCH_INTERVAL_SECONDS / 3600
        print(f"[serve] periodic refetch armed (every {hours:g}h while today's workout is incomplete)", file=sys.stderr)

    # Serve from the project root so index.html can read data/workouts.json.
    handler = http.server.SimpleHTTPRequestHandler
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", args.port), handler) as httpd:
        url = f"http://localhost:{args.port}/"
        print(f"[serve] viewer at {url}  (Ctrl+C to stop)", file=sys.stderr)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[serve] bye", file=sys.stderr)


if __name__ == "__main__":
    # Serve from the directory this script lives in, not the caller's cwd.
    import os
    os.chdir(HERE)
    main()
