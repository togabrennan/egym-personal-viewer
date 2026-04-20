#!/usr/bin/env python3
"""Convenience launcher: auto-fetch if data/workouts.json is stale, then serve.

Fetches fresh data only if `data/workouts.json` is missing or its
`generated_at` timestamp is from a day earlier than today (local time).
Pass `--force` to always fetch, or `--no-fetch` to skip the staleness
check entirely.

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
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "workouts.json"


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
            import fetch  # local import so --no-fetch works without credentials
            # Simulate a bare argparse.Namespace so resolve_credentials uses settings/env.
            fetch.main.__globals__["sys"].argv = ["fetch.py"]
            try:
                fetch.main()
            except SystemExit as e:
                # fetch.main() calls sys.exit() on missing creds — surface that clearly.
                if e.code:
                    print("[serve] fetch failed; starting server with whatever data exists", file=sys.stderr)
        else:
            print(f"[serve] skipping fetch: {reason}", file=sys.stderr)

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
