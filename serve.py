#!/usr/bin/env python3
"""Convenience launcher: auto-fetch if data/workouts.json needs refresh, then serve.

Fetches fresh data only if `data/workouts.json` is missing or its
`generated_at` timestamp is from a day earlier than today (local time), or
if today's data still needs a scheduled retry.
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

# Serializes fetches across the periodic loop and the manual /refresh endpoint
# so two fetches can't run concurrently and trip over the same data files.
FETCH_LOCK = threading.Lock()


def is_stale(now: datetime | None = None) -> tuple[bool, str]:
    if not DATA.exists():
        return True, "no data/workouts.json yet"
    try:
        gen = json.loads(DATA.read_text()).get("generated_at")
        if not gen:
            return True, "workouts.json missing generated_at"
        # Compare date portion only (local time). Works for RFC3339/ISO 8601.
        gen_date = datetime.fromisoformat(gen.replace("Z", "+00:00")).astimezone().date()
        today = (now or datetime.now().astimezone()).astimezone().date()
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


def _latest_set_today(rows: list, now: datetime | None = None) -> datetime | None:
    """Most recent exercise_completed_at dated today (local), or None."""
    today = (now or datetime.now().astimezone()).astimezone().date()
    latest: datetime | None = None
    for row in rows or []:
        ts = _parse_ts(row.get("exercise_completed_at"))
        if ts is None:
            continue
        if ts.date() == today and (latest is None or ts > latest):
            latest = ts
    return latest


def _elapsed(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem = minutes % 60
    if rem:
        return f"{hours}h {rem}m"
    return f"{hours}h"


def should_refetch(now: datetime | None = None) -> tuple[bool, str]:
    """Refetch until a successful fetch has run at least one full interval
    after the most recent set — that's our signal that any late-syncing
    stragglers from the workout have been captured."""
    now = (now or datetime.now().astimezone()).astimezone()
    if not DATA.exists():
        return True, "no data/workouts.json yet"
    try:
        data = json.loads(DATA.read_text())
    except Exception as e:
        return True, f"could not read workouts.json: {e}"
    last_fetch = _parse_ts(data.get("generated_at"))
    if last_fetch is None:
        return True, "no prior fetch timestamp to compare against"
    since_fetch = now - last_fetch
    if since_fetch < timedelta(seconds=REFETCH_INTERVAL_SECONDS):
        wait_reason = f"last fetch was only {_elapsed(since_fetch)} ago"
    else:
        wait_reason = ""

    latest_set = _latest_set_today(data.get("rows") or [], now)
    if latest_set is None:
        if wait_reason:
            return False, f"no exercise recorded today yet, but {wait_reason}"
        return True, f"no exercise recorded today yet; last fetch was {_elapsed(since_fetch)} ago"

    gap = last_fetch - latest_set
    if gap >= timedelta(seconds=REFETCH_INTERVAL_SECONDS):
        return False, f"most recent set captured {int(gap.total_seconds() // 3600)}h+ before last fetch — today's workout looks complete"
    if wait_reason:
        mins = int(gap.total_seconds() // 60)
        return False, f"{wait_reason}; last fetch was only {mins}m after the most recent set"
    mins = int(gap.total_seconds() // 60)
    return True, f"last fetch was only {mins}m after the most recent set — workout may still be syncing"


def should_fetch_on_start(force: bool = False, now: datetime | None = None) -> tuple[bool, str]:
    if force:
        return True, "forced"
    stale, stale_reason = is_stale(now)
    if stale:
        return True, stale_reason
    refetch, refetch_reason = should_refetch(now)
    if refetch:
        return True, refetch_reason
    return False, f"{stale_reason}; {refetch_reason}"


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
            if not ok:
                print(f"[serve] skipping scheduled refetch: {reason}", file=sys.stderr)
                continue
            if not FETCH_LOCK.acquire(blocking=False):
                print("[serve] skipping scheduled refetch: another fetch is running", file=sys.stderr)
                continue
            try:
                print(f"[serve] refetching: {reason}", file=sys.stderr)
                run_fetch()
            finally:
                FETCH_LOCK.release()
        except Exception as e:
            # Never let a bad tick kill the loop — we'll try again next interval.
            print(f"[serve] refetch tick failed: {e!r}; will retry next interval", file=sys.stderr)


class ViewerHandler(http.server.SimpleHTTPRequestHandler):
    """Static file server plus a POST /refresh endpoint.

    /refresh runs fetch.py synchronously and returns 200 on success or 409 if
    a fetch is already in flight. Auth is intentionally not handled here —
    front the server with a reverse proxy (nginx/caddy basic-auth, Tailscale,
    etc.) before exposing it beyond localhost.
    """

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        if self.path.split("?", 1)[0] != "/refresh":
            self.send_error(404, "Not found")
            return
        if not FETCH_LOCK.acquire(blocking=False):
            self._json(409, {"status": "busy", "message": "a fetch is already running"})
            return
        try:
            print("[serve] refresh requested via /refresh", file=sys.stderr)
            run_fetch()
        finally:
            FETCH_LOCK.release()
        self._json(200, {"status": "ok"})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--force", action="store_true", help="always fetch before serving")
    p.add_argument("--no-fetch", action="store_true", help="never fetch")
    args = p.parse_args()

    if not args.no_fetch:
        should_fetch, reason = should_fetch_on_start(args.force)
        if should_fetch:
            print(f"[serve] fetching: {reason}", file=sys.stderr)
            run_fetch()
        else:
            print(f"[serve] skipping fetch: {reason}", file=sys.stderr)
        threading.Thread(target=refetch_loop, daemon=True).start()
        hours = REFETCH_INTERVAL_SECONDS / 3600
        print(f"[serve] periodic refetch armed (every {hours:g}h while today's workout is incomplete)", file=sys.stderr)

    # Serve from the project root so index.html can read data/workouts.json.
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", args.port), ViewerHandler) as httpd:
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
