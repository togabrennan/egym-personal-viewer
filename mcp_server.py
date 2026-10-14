#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==2.3.0"]
# ///
"""Read-only eGym queries over Streamable HTTP, with an optional data refresh."""
from __future__ import annotations

import argparse
import functools
import ipaddress
import json
import logging
import os
import re
import sys
import threading
from collections import defaultdict
from datetime import date as Date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import serve

Unit = Literal["lb", "kg"]
NO_DATA = "No data yet — call refresh first."


def load() -> dict:
    """Read the latest file on every call; serve.DATA also supports test fixtures."""
    return json.loads(serve.DATA.read_text())


def _read_tool(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FileNotFoundError:
            return NO_DATA
        except (OSError, ValueError) as exc:
            return f"Could not read workout data: {exc}"
    return wrapped


def _now() -> datetime:
    return datetime.now().astimezone()


def _timestamp(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError):
        return None


def _local_date(value, timezone=None) -> str | None:
    ts = _timestamp(value)
    if ts is None:
        return None
    if timezone:
        try:
            ts = ts.astimezone(ZoneInfo(timezone))
        except (ZoneInfoNotFoundError, ValueError):
            pass  # Missing/invalid workout timezone falls back to server local time.
    return ts.date().isoformat()


def _gym_days(rows) -> dict:
    grouped = defaultdict(list)
    for row in rows:
        day = _local_date(row.get("exercise_completed_at") or row.get("workout_completed_at"),
                          row.get("workout_timezone"))
        if day:
            grouped[day].append(row)
    return grouped


def _recent(grouped, days):
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("days must be a positive integer")
    today = _now().date()
    cutoff = (today - timedelta(days=days - 1)).isoformat()
    return {day: rows for day, rows in grouped.items() if cutoff <= day <= today.isoformat()}


def _weight(kg, unit):
    if unit not in ("lb", "kg"):
        raise ValueError("unit must be lb or kg")
    return "—" if kg is None else f"{float(kg) * (2.20462 if unit == 'lb' else 1):.1f} {unit}"


def _cell(value):
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _table(headers, rows):
    return "\n".join("| " + " | ".join(map(_cell, row)) + " |"
                     for row in [headers, ["---"] * len(headers), *rows])


def _volume(rows):
    return sum(float(r.get("weight") or 0) * float(r.get("reps") or 0) for r in rows)


def _points(rows):
    # fetch.py repeats per-exercise activity points on every set.
    seen = set()
    total = 0
    for r in rows:
        key = (r.get("workout_code"), r.get("exercise_code"))
        if key == (None, None):
            key = (r.get("exercise_name"), r.get("exercise_completed_at"))
        if key not in seen:
            seen.add(key)
            total += float(r.get("activity_points") or 0)
    return f"{total:g}"


def _match(machine, names):
    names = sorted(set(names))
    query = machine.strip().casefold()
    matches = [n for n in names if n.casefold() == query]
    if not matches and query:
        matches = [n for n in names if query in n.casefold()]
    if len(matches) == 1:
        return matches[0], None
    if matches:
        return None, "Ambiguous machine; choose one: " + ", ".join(matches)
    return None, "No matching machine. Available machines: " + (", ".join(names) or "none")


@_read_tool
def get_summary() -> str:
    """Call first for sync age, workout totals, gym-day frequency, and machines used."""
    data = load()
    grouped = _gym_days(data.get("rows") or [])
    dates = sorted(grouped)
    synced = _timestamp(data.get("generated_at"))
    age = f"synced {max(0, (_now() - synced).total_seconds() / 3600):.1f} h ago" if synced else "sync time unknown"
    machines = sorted({r['exercise_name'] for r in data.get('rows') or [] if r.get('exercise_name')})
    return _table(["Metric", "Value"], [
        ["Sync", f"{age} ({data.get('generated_at') or '—'})"],
        ["Total workouts", data.get("workout_count", 0)],
        ["First gym day", dates[0] if dates else "—"],
        ["Last gym day", dates[-1] if dates else "—"],
        *[[f"Gym days in last {n} days", len(_recent(grouped, n))] for n in (7, 30, 90)],
        ["Machines used", ", ".join(machines) or "none"],
    ])


@_read_tool
def get_gym_days(days: int = 30, unit: Unit = "lb") -> str:
    """List recent local gym days, newest first, with machines, sets, volume, and activity points."""
    data = load()
    _weight(0, unit)
    grouped = _recent(_gym_days(data.get("rows") or []), days)
    return _table(["Date", "Machines", "Sets", f"Volume ({unit} × reps)", "Activity points"], [
        [day, ", ".join(sorted({r.get('exercise_name') or 'Unknown' for r in rows})),
         len(rows), _weight(_volume(rows), unit), _points(rows)]
        for day, rows in sorted(grouped.items(), reverse=True)
    ])


@_read_tool
def get_day(date: str, unit: Unit = "lb") -> str:
    """Show every set grouped by machine for a local gym date (YYYY-MM-DD)."""
    data = load()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return "Use a date in YYYY-MM-DD format."
    try:
        Date.fromisoformat(date)
    except ValueError:
        return "Use a valid date in YYYY-MM-DD format."
    _weight(0, unit)
    rows = _gym_days(data.get("rows") or []).get(date)
    if not rows:
        return f"No workout on {date}."
    machines = defaultdict(list)
    for row in rows:
        machines[row.get("exercise_name") or "Unknown"].append(row)
    return f"## {date}\n\n" + "\n\n".join(
        f"### {_cell(name)}\n" + _table(["Set", "Reps", f"Weight ({unit})"], [
            [r.get("set_index"), r.get("reps"), _weight(r.get("weight"), unit)]
            for r in sorted(sets, key=lambda r: (r.get("exercise_completed_at") or "", r.get("set_index") or 0))
        ]) for name, sets in sorted(machines.items()))


@_read_tool
def get_strength(machine: str | None = None, unit: Unit = "lb") -> str:
    """Get latest and best tested 1RMs and change from the previous test; supply a machine for its full history. Names match case-insensitively, exact first then substring."""
    data = load()
    _weight(0, unit)
    grouped = defaultdict(list)
    for row in data.get("strength") or []:
        if row.get("exercise_name") and row.get("one_rep_max_kg") is not None and _timestamp(row.get("measured_at")):
            grouped[row["exercise_name"]].append(row)
    for tests in grouped.values():
        tests.sort(key=lambda r: _timestamp(r["measured_at"]))
    if machine is not None:
        name, error = _match(machine, grouped)
        if error:
            return error
        return f"## {_cell(name)} — 1RM history\n" + _table(["Measured at", f"1RM ({unit})"], [
            [r["measured_at"], _weight(r["one_rep_max_kg"], unit)] for r in grouped[name]])
    result = []
    for name, tests in sorted(grouped.items(), key=lambda item: item[1][-1]["one_rep_max_kg"], reverse=True):
        latest = tests[-1]
        best = max(tests, key=lambda r: r["one_rep_max_kg"])
        change = "—"
        if len(tests) > 1:
            prev = tests[-2]["one_rep_max_kg"]
            delta = latest["one_rep_max_kg"] - prev
            change = ("+" if delta > 0 else "") + _weight(delta, unit)
            change += f" ({delta / prev * 100:+.1f}%)" if prev else " (—%)"
        result.append([name, _weight(latest["one_rep_max_kg"], unit), _local_date(latest["measured_at"]),
                       _weight(best["one_rep_max_kg"], unit), _local_date(best["measured_at"]), change, len(tests)])
    return _table(["Machine", f"Latest 1RM ({unit})", "Date", f"All-time max ({unit})", "Max date", "Change", "Tests"], result)


@_read_tool
def get_machine_history(machine: str, days: int = 90, unit: Unit = "lb") -> str:
    """Show recent working sets, top weight, and volume per local day for a machine. Uses case-insensitive exact or substring matching."""
    data = load()
    _weight(0, unit)
    rows = data.get("rows") or []
    name, error = _match(machine, [r["exercise_name"] for r in rows if r.get("exercise_name")])
    if error:
        return error
    grouped = _recent(_gym_days([r for r in rows if r.get("exercise_name") == name]), days)
    result = []
    for day, sets in sorted(grouped.items(), reverse=True):
        working = defaultdict(int)
        for r in sets:
            working[(r.get("reps"), r.get("weight"))] += 1
        desc = "; ".join(f"{n} × {reps if reps is not None else '—'} @ {_weight(weight, unit)}"
                         for (reps, weight), n in working.items())
        weights = [r["weight"] for r in sets if r.get("weight") is not None]
        result.append([day, desc, _weight(max(weights) if weights else None, unit), _weight(_volume(sets), unit)])
    return f"## {_cell(name)} — last {days} days\n" + _table(
        ["Date", "Sets × reps @ weight", f"Top weight ({unit})", f"Volume ({unit} × reps)"], result)


@_read_tool
def get_bio_age() -> str:
    """Get latest total and component bio ages, trends, dates, and available RHR, VO₂max, BMI, and body fat. Missing metrics are omitted."""
    bio = load().get("bio_age") or {}
    fields = [
        ("totalDetails", "totalBioAge", "Total bio age", "years"),
        ("cardioDetails", "cardioAge", "Cardio age", "years"),
        ("metabolicDetails", "metabolicAge", "Metabolic age", "years"),
        ("muscleDetails", "muscleBioAge", "Muscle age", "years"),
        ("muscleDetails", "upperBodyAge", "Muscle upper", "years"),
        ("muscleDetails", "coreAge", "Muscle core", "years"),
        ("muscleDetails", "lowerBodyAge", "Muscle lower", "years"),
        ("flexibilityDetails", "flexibilityAge", "Flexibility age", "years"),
        ("cardioDetails", "restingHeartRate", "RHR", "bpm"),
        ("cardioDetails", "vo2max", "VO₂max", "ml/kg/min"),
        ("metabolicDetails", "bmi", "BMI", ""),
        ("metabolicDetails", "bodyFat", "Body fat", "%"),
    ]
    result = []
    for section, key, label, unit in fields:
        entry = (bio.get(section) or {}).get(key)
        if isinstance(entry, dict) and entry.get("value") is not None:
            result.append([label, f"{entry['value']} {unit}".strip(), entry.get("progress"),
                           _local_date(entry.get("createdAt"), entry.get("timezone"))])
    return _table(["Metric", "Value", "Progress", "Date"], result) if result else "No bio-age measurements yet."


def refresh() -> str:
    """Fetch fresh eGym data now. Takes seconds; returns busy if another fetch is running."""
    if not serve.FETCH_LOCK.acquire(blocking=False):
        return "A fetch is already running"
    try:
        failure = serve.run_fetch()
        if failure:
            return failure
        return f"Refreshed — synced at {load().get('generated_at') or 'unknown'}"
    except Exception as exc:
        return f"Refresh failed: {exc}"
    finally:
        serve.FETCH_LOCK.release()


def endpoint_path(host: str, insecure: bool) -> str:
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback:
        raise ValueError("--host must be a loopback address; use a tunnel for remote access")
    token = os.environ.get("EGYM_MCP_TOKEN", "")
    if not token:
        if insecure:
            return "/mcp"
        raise ValueError("EGYM_MCP_TOKEN is required (or use --insecure-no-token on loopback)")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,}", token):
        raise ValueError("EGYM_MCP_TOKEN must be a URL-safe token of at least 43 characters; generate with secrets.token_urlsafe(32)")
    return f"/{token}/mcp"


def create_app(path: str, host: str = "127.0.0.1"):
    # Lazy imports keep plain tool tests and the existing viewer stdlib-only.
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import ToolAnnotations

    server = MCPServer("eGym", instructions="Call get_summary first. Weights default to lb; workout days use the recorded timezone.")
    for tool in (get_summary, get_gym_days, get_day, get_strength, get_machine_history, get_bio_age):
        server.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)(tool)
    server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
                structured_output=False)(refresh)
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    hosts.extend(h.strip() for h in os.environ.get("EGYM_MCP_ALLOWED_HOSTS", "").split(",") if h.strip())
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    origins.extend("https://" + h for h in hosts[3:])
    app = server.streamable_http_app(
        host=host, streamable_http_path=path, stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins))
    app.router.redirect_slashes = False
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--insecure-no-token", action="store_true")
    args = parser.parse_args()
    try:
        path = endpoint_path(args.host, args.insecure_no_token)
    except ValueError as exc:
        parser.error(str(exc))
    os.chdir(serve.HERE)
    app = create_app(path, args.host)
    if not args.no_fetch:
        needed, reason = serve.should_fetch_on_start()
        print(f"[serve] {'fetching' if needed else 'skipping fetch'}: {reason}", file=sys.stderr)
        if needed:
            refresh()
        threading.Thread(target=serve.refetch_loop, daemon=True).start()
        print("[serve] periodic refetch armed (every 2h while today's workout is incomplete)", file=sys.stderr)

    # The SDK's run() enables access logging with no override. Host its ASGI app
    # directly so secret request paths never enter the access log.
    import uvicorn

    class PrivateFormatter(logging.Formatter):
        def format(self, record):
            message = super().format(record)
            token = os.environ.get("EGYM_MCP_TOKEN")
            if token:
                message = message.replace(token, "[redacted]")
            return re.sub(r"https?://[^\s]+|/[^\s]*mcp[^\s]*", "[endpoint]", message)

    logging_config = {
        "version": 1, "disable_existing_loggers": False,
        "formatters": {"private": {"()": PrivateFormatter, "fmt": "%(levelname)s: %(message)s"}},
        "handlers": {"stderr": {"class": "logging.StreamHandler", "stream": "ext://sys.stderr", "formatter": "private"}},
        "root": {"handlers": ["stderr"], "level": "INFO"},
        "loggers": {"uvicorn": {"handlers": [], "propagate": True},
                    "uvicorn.error": {"handlers": [], "propagate": True},
                    "uvicorn.access": {"handlers": [], "propagate": False}},
    }
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_config=logging_config)


if __name__ == "__main__":
    main()
