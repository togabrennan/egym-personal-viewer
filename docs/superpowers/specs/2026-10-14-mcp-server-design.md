# MCP server: expose workout data to Claude

**Date:** 2026-10-14
**Status:** Draft — ready for implementation

## Problem

The viewer is great for looking at charts, but there's no way to ask Claude
questions about the data ("how has my leg press 1RM moved since June?",
"what did I do last Tuesday?") without manually exporting Markdown and
pasting it in.

## Goal

A small MCP server that reads `data/workouts.json`, exposes a handful of
read-only query tools plus a refresh tool, and runs 24/7 on a home server
(called `egym-server` in the examples) so any Claude client (Claude Code, Claude Desktop, claude.ai web
and mobile) can connect to it as a remote connector.

Simple is the priority. One new Python file, one systemd unit, one tunnel.

## Non-goals

- No changes to `index.html`, the viewer UI, or `fetch.py`'s output format.
- No OAuth server. Auth is a secret URL path (see Security).
- No database, caching layer, or write tools beyond `refresh`.
- No Docker (unless egym-server turns out to have no usable Python — see Open
  questions).

## Architecture

```
claude.ai / Claude Code ──HTTPS──▶ Tailscale Funnel (egym-server) ──▶ 127.0.0.1:8000
                                                                     mcp_server.py
                                                                       ├─ reads data/workouts.json
                                                                       └─ background refetch loop → fetch.py
```

One process on egym-server, `mcp_server.py`, does two things:

1. Serves MCP over **Streamable HTTP** on `127.0.0.1:8000`.
2. Runs the existing 2-hour refetch loop from `serve.py` in a daemon thread
   so data stays fresh without the viewer running.

`serve.py` keeps working for the browser viewer. Its only change is a
cross-process fetch lock (see "Concurrency"), so both can run on egym-server at
the same time.

## Dependencies

The project is stdlib-only and must stay that way for `fetch.py`, `serve.py`,
and `generate_demo.py`. The MCP SDK is confined to `mcp_server.py` using
**PEP 723 inline script metadata**, so no `requirements.txt` and no venv
management:

```python
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==<pin current stable at implementation time>"]
# ///
```

Run with `uv run mcp_server.py`. `uv` is the only thing to install on
egym-server.

**SDK version note:** the official Python SDK (`modelcontextprotocol/python-sdk`)
renamed `FastMCP` (`mcp.server.fastmcp`) to `MCPServer`
(`mcp.server.mcpserver`) in v2, and moved transport options from the
constructor to `run()`. Pin an exact version and use the API that version
provides. Do not mix v1 and v2 examples.

## File: `mcp_server.py`

### Reuse from `serve.py`

Import, do not copy: `DATA`, `FETCH_LOCK`, `run_fetch`, `should_refetch`,
`should_fetch_on_start`, `refetch_loop`. `serve.py` has no import-time side
effects (its `main()` is behind `__main__`), so `import serve` is safe.

### Data loading

- `load() -> dict`: read and `json.loads` `serve.DATA` on **every tool
  call**. The file is ~1 MB; re-reading is cheap and means a refresh is
  picked up immediately with no cache invalidation.
- If the file is missing, tools return a clear error string:
  `"No data yet — call refresh first."`

### Data shape (`data/workouts.json`)

Top-level keys: `generated_at`, `user_id`, `brand`, `workout_count`,
`rows`, `strength`, `bio_age`.

- `rows[]` — one per set. Relevant fields: `workout_completed_at`,
  `workout_timezone`, `exercise_name`, `exercise_completed_at`, `set_index`,
  `set_total`, `reps`, `weight` (**always kg**), `calories`,
  `activity_points`, `duration`, `distance`, `avg_heart_rate`.
- `strength[]` — one per 1RM measurement: `exercise_name`, `body_region`
  (`UPPER`/`CORE`/`LOWER`), `one_rep_max_kg`, `measured_at`.
- `bio_age` — nested dict with `totalDetails`, `muscleDetails`,
  `metabolicDetails`, `cardioDetails`, `flexibilityDetails`.

Machine names in `rows` and `strength` are already canonicalized by
`fetch.py`; no renaming is needed here.

### Units

Every tool that returns weight takes `unit: Literal["lb", "kg"] = "lb"`.
Convert from kg with `kg * 2.20462`, round to 1 decimal. Always label the
unit in the output.

### Dates

Group by **local date in the workout's `workout_timezone`** (fall back to
the server's local tz if missing). Day boundaries must match the viewer's
"Gym Days" table.

### Tools

All tools return **plain Markdown strings** (compact tables). That's the
most token-efficient format for Claude and matches the existing Markdown
export. Every tool's docstring is its description for Claude, so write
those for an LLM reader: what it returns and when to use it.

| Tool | Args | Returns |
|---|---|---|
| `get_summary` | — | `generated_at` (as "synced N h ago"), total workouts, date of first/last gym day, gym days in last 7/30/90 days, list of machines used. Cheap overview, so Claude should call it first. |
| `get_gym_days` | `days: int = 30`, `unit` | One row per gym day, newest first: date, machines hit, total sets, total volume (Σ weight × reps), activity points. |
| `get_day` | `date: str` (YYYY-MM-DD), `unit` | Every set on that day grouped by machine: set #, reps, weight. Error string if no workout that day. |
| `get_strength` | `machine: str \| None = None`, `unit` | No machine: one row per machine with latest 1RM + date, all-time max + date, change vs previous test (Δ and %), number of tests, sorted by latest 1RM desc. With machine: full chronological 1RM history for it. Case-insensitive substring match on machine name; if ambiguous, list the candidates. |
| `get_machine_history` | `machine: str`, `days: int = 90`, `unit` | Per-day working sets for one machine: date, sets × reps @ weight, top-set weight, day volume. Same matching rules as `get_strength`. |
| `get_bio_age` | — | Total bio age plus each sub-age (cardio, metabolic, muscle upper/core/lower, flexibility) with value, progress direction, and date. Include RHR, VO₂max, BMI, body fat where present. Skip null fields. |
| `refresh` | — | Runs `serve.run_fetch()` under `serve.FETCH_LOCK` (non-blocking acquire). Returns `"Refreshed — synced at <ts>"`, `"A fetch is already running"`, or the failure message. Must not raise. |

`refresh` is blocking. `fetch.py` takes seconds to tens of seconds, which
is fine for an MCP tool call. Mark the read tools with
`readOnlyHint=True` annotations if the pinned SDK supports tool annotations.

### Startup

```
uv run mcp_server.py [--host 127.0.0.1] [--port 8000] [--no-fetch]
```

1. Unless `--no-fetch`: call `should_fetch_on_start()`, fetch if needed,
   then start `serve.refetch_loop` in a daemon thread (same behavior and log
   lines as `serve.py`).
2. Read the secret path token from env `EGYM_MCP_TOKEN` (required unless
   `--host` is loopback **and** `--insecure-no-token` is passed; see
   Security). Mount the MCP endpoint at `/<token>/mcp`.
3. Run Streamable HTTP with `stateless_http=True` and `json_response=True`.
   We don't need sessions, progress, or server→client callbacks, and
   stateless avoids session-loss problems across restarts.
4. `os.chdir(HERE)` like `serve.py`, so `fetch.py` resolves
   `settings.json` and `data/` correctly.

Log to stderr (journald picks it up). **Never log the token** or the full
request path.

## Security

The data is personal (workouts, body metrics), and the server holds eGym
credentials, so it must not be publicly open.

- **Secret path auth.** `EGYM_MCP_TOKEN` is a 32+ byte random URL-safe
  string (`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`).
  The endpoint is `https://egym-server.<tailnet>.ts.net/<token>/mcp`. Any other
  path returns 404. This is the "authless connector with an unguessable URL"
  pattern. It's adequate for read-mostly personal data, not real auth. Upgrade
  path if ever needed: Cloudflare Access or OAuth. Out of scope now.
- **Bind to loopback only** (`127.0.0.1`). Funnel is the only way in.
- **DNS-rebinding / Host checks:** the SDK's `transport_security` may reject
  requests whose `Host` is the Funnel hostname. Configure allowed hosts to
  include the `*.ts.net` name (pass via env `EGYM_MCP_ALLOWED_HOSTS`, comma
  separated), rather than disabling the protection outright.
- Only `refresh` has side effects, and it's rate-limited by `FETCH_LOCK`.
  No tool accepts file paths or shells out with user input.
- Credentials stay where they are today: `settings.json` (chmod 600) on
  egym-server, or env vars from the systemd `EnvironmentFile`.

## Concurrency

`FETCH_LOCK` is a `threading.Lock`, so it's per process. If both `serve.py`
and `mcp_server.py` run on egym-server, two processes could fetch at once.
Simplest fix, in scope: in `serve.run_fetch()`, wrap the subprocess in an
**`data/.fetch.lock`** (non-blocking; skip with a log line if held).
Use stdlib `fcntl.flock` on Unix and `msvcrt.locking` on Windows, with
platform-specific imports so the viewer stays portable. Keep `FETCH_LOCK`
as-is for the in-process case. Test cross-process contention and the Windows
locking branch.

## Deployment on egym-server

Add `deploy/` with two files and a README section.

### `deploy/egym-mcp.service` (systemd user unit)

```ini
[Unit]
Description=eGym MCP server
After=network-online.target

[Service]
WorkingDirectory=%h/egym-personal-viewer
EnvironmentFile=%h/.config/egym-mcp.env
ExecStart=%h/.local/bin/uv run mcp_server.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

`~/.config/egym-mcp.env` (chmod 600, not in repo) holds `EGYM_MCP_TOKEN`,
`EGYM_MCP_ALLOWED_HOSTS`, and optionally `EGYM_BRAND/USERNAME/PASSWORD`
instead of `settings.json`. Run `loginctl enable-linger $USER` so the user
unit runs without a login session.

### `deploy/README.md`: setup steps

1. `git clone` the repo to `~/egym-personal-viewer` on egym-server. Copy
   `settings.json` over (`scp`), `chmod 600`.
2. Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
3. Create `~/.config/egym-mcp.env` with a fresh token.
4. `systemctl --user enable --now egym-mcp` (after copying the unit to
   `~/.config/systemd/user/`).
5. `tailscale funnel --bg 8000`. This gives a public HTTPS URL that forwards
   to `127.0.0.1:8000`.
6. Connect:
   - **claude.ai** (also syncs to mobile): Settings → Connectors → Add
     custom connector → `https://egym-server.<tailnet>.ts.net/<token>/mcp`.
   - **Claude Code:** `claude mcp add --transport http egym <same URL>`.
7. Update: `git pull && systemctl --user restart egym-mcp`.

## Testing

`test_mcp_server.py`, stdlib `unittest`, same style as `test_serve.py`
(point `serve.DATA` at a temp file in `setUp`). Test the tool functions
directly as plain Python functions; no HTTP needed. Keep tool logic in
plain functions that the decorated tools call, so they're testable without
the SDK running.

Cover at least:

- Missing data file → "No data yet" message from every read tool.
- kg → lb conversion and unit labeling.
- Day grouping uses `workout_timezone` (a set at 01:30 UTC on 2026-07-21 in
  `America/New_York` belongs to 2026-07-20).
- `get_strength` latest vs. all-time max vs. Δ%, single-test machine shows
  `—` for change.
- Machine matching: exact, case-insensitive substring, ambiguous → candidate
  list, no match → helpful error.
- `refresh` returns "already running" when the lock is held (patch
  `run_fetch`).
- Cross-process `flock` in `run_fetch` skips when the lock file is held.

Fixture data: generate with `generate_demo.py` logic or hand-write a small
JSON. **Never** commit real `data/workouts.json` content.

Manual check: `npx @modelcontextprotocol/inspector` against
`http://127.0.0.1:8000/<token>/mcp`, then call each tool.

## Docs

- README: new "Ask Claude about your data (MCP)" section linking to
  `deploy/README.md`. Mention the extra `uv` requirement applies only to
  the MCP server.
- CHANGELOG entry.

## Acceptance criteria

1. `uv run mcp_server.py --no-fetch` starts locally against demo data and
   all tools work in MCP Inspector.
2. `python3 -m unittest` passes (old and new tests).
3. `fetch.py`, `serve.py`, and `index.html` still have zero third-party
   imports.
4. On egym-server, the systemd unit survives a reboot and the Funnel URL works
   from claude.ai. A wrong token path returns 404.
5. Asking Claude "what's my latest leg press 1RM?" produces a correct
   answer via `get_strength`.

## Open questions (resolve before deploying, not before coding)

- **egym-server's OS and Python.** Assumed: Linux with systemd and Tailscale
  already installed. If Tailscale isn't on it, use Cloudflare Tunnel
  (`cloudflared`) instead; only step 5 of the deploy changes. If it's not
  systemd (e.g. a NAS), fall back to a Docker container with the same env
  file.
- **Credentials on egym-server:** plain `settings.json` (simplest) vs.
  1Password service account + `op run --env-file egym.op.env` (no secrets on
  disk, but more setup). Default: `settings.json`.
