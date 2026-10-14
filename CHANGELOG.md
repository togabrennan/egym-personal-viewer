# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Daily body-weight history in regular syncs, cached raw/flattened JSON,
  viewer charts and Markdown exports. New `get_body_weight` MCP tool;
  strength histories include prior weigh-ins and 1RM/body-weight ratios.
- Optional MCP server with eight Markdown query/refresh tools, local workout
  dates, lb/kg units, pinned SDK via uv, secret-path access, and Host checks.
- systemd user service and Tailscale Funnel deployment instructions in `deploy/`.
- Cross-process fetch locking shared by the viewer and MCP server, with tests.
  Uses `fcntl` on Unix and `msvcrt` on Windows to keep the viewer portable.
- `egym.op.env` — 1Password `op://` references for loading credentials
  with `op run --env-file egym.op.env -- python3 serve.py`, so no secrets
  are stored on disk.

## [0.1.0] - 2026-04-20

### Added
- Initial release.
- `fetch.py` — pulls workout history, strength-test history, and bio-age
  snapshot from the NetpulseFitness / eGym member APIs using the member's
  own credentials. Writes `data/workouts.json`, `data/workouts.csv`, and
  `data/workouts_raw.json`.
- `index.html` — static dark/light-mode viewer with:
  - Summary cards (gym days, machines used, total sets, volume, activity points)
  - Bio Age panel (Total / Cardio / Metabolic / Muscle / Flexibility)
  - Activity Points bar chart
  - Per-machine Weight-per-Rep line chart with 1RM test overlay
  - Per-machine Volume-per-Day bar chart
  - 1RM Trend table (sorted by 1RM desc, with direction + % change)
  - Volume Records table (ranked by best-day volume)
  - Grouped Gym Days table
  - Range toggle: 7d / 30d / 90d / All (persisted)
  - Units toggle: lb / kg (persisted)
  - Theme toggle: light / dark (persisted, respects system preference by default)
- `settings.json` configuration with env-var and CLI-flag overrides.
- MIT License, `.gitignore` protecting credentials and personal data.

### Known limitations
- EGYM training-plan / Genius-phase endpoints are gated behind a trainer
  role and return 403 to member credentials. Workout-type (Normal / Negative
  / Basic / Isometric) metadata is not available.
- Bio-age history endpoint (`/bioage/history`) requires a `granularity` enum
  whose valid values we haven't discovered; current viewer shows only the
  latest snapshot.
- `strength/latest` endpoint returns API-rounded integer-kg deltas; viewer
  recomputes deltas from consecutive readings in the strength-history
  payload for sub-kg precision.
