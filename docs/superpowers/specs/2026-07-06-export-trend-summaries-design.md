# Export: historical strength & volume trend summaries

**Date:** 2026-07-06
**Status:** Approved

## Problem

The Markdown export ("↓ Export MD" in the viewer) already dumps complete raw
history — every 1RM measurement per machine, every working set, and a daily
log. But nothing in it summarizes that history as *trends*: no week-by-week
volume, no first-vs-latest strength progression, no body-region breakdown.
An LLM ingesting the export must reconstruct trends from ~900 table rows.

## Goal

Add derived trend sections to the export so strength levels and workout
volume are legible as time series, without changing the raw sections or the
data pipeline.

## Approach

All changes live in `exportMarkdown()` in `index.html` (Approach A). The
export function already has everything it needs client-side: `ALL_ROWS`
(machine set rows), `STRENGTH_HISTORY` (per-machine chronological 1RM
readings keyed by `normName`), unit handling (`convert`, `unitLabel`), and
date helpers. No changes to `fetch.py`, `serve.py`, or the data files.

Rejected alternatives:

- **Precompute in `fetch.py`** — duplicates unit handling, bloats
  `workouts.json`, requires a refetch to change a summary. Pure derivations
  belong where the rest of the derivations are.
- **Minimal (no per-machine rollup)** — leaves per-machine trends buried in
  the raw set tables.

## New sections

Inserted between the existing "Snapshot (all-time)" / "Bio Age" sections and
the existing raw-history sections ("1RM Strength — Latest" onward), in this
order:

### 1. Strength Progression Summary

One table, one row per machine, sorted by latest 1RM descending (matching
the existing "1RM Strength — Latest" ordering):

```
| Machine | Region | First 1RM | Latest 1RM | Change | Best Ever | Tests |
```

- Source: `STRENGTH_HISTORY` (already sorted chronologically per machine).
- First/Latest cells include the measurement date, e.g. `242.5 (Mar 18)`.
- Change = latest − first, shown as `+Δ unit (+x.x%)`; `—` when only one
  measurement exists.
- Best Ever = max reading across the history (with its date).
- Tests = number of measurements.

### 2. Weekly Volume Summary

One table, one row per ISO week that has at least one set, oldest first:

```
| Week of | Gym Days | Sets | Volume | Upper | Lower | Core | Activity Pts |
```

- "Week of" = the Monday of the ISO week, formatted like other dates.
- Volume = Σ weight × reps over that week's sets (machine rows with
  weight ≠ null, same filter the snapshot uses).
- Upper/Lower/Core = the same volume split by body region. Region comes
  from the strength records: build `normName(exercise_name) → body_region`
  from `data.strength`; join set rows through `normName`. Rows whose
  machine has no strength record fall into an `Other` column, which is
  emitted only if nonzero volume ever lands there.
- Activity Pts deduped by `workout_code|exercise_code`, same as the
  snapshot's AP total.
- Weeks with no training are omitted (no zero rows).
- Consistency invariant: the Volume column must sum to the snapshot's
  "Total volume lifted", and per-region columns (plus Other) must sum to
  Volume per row.

### 3. Per-Machine Weekly Progression

One subsection per machine (same machine set and ordering as the existing
"Per-Machine Training History": machines with weighted sets, sorted by set
count descending). One row per ISO week the machine was trained,
oldest first:

```
| Week of | Sets | Top Weight | Est. 1RM | Volume |
```

- Top Weight = heaviest working-set weight that week.
- Est. 1RM = Epley formula `w × (1 + reps/30)` evaluated per set, best of
  the week. The section preamble labels this clearly as an estimate derived
  from working sets, distinct from the machine-measured 1RM in the strength
  sections.
- Volume = Σ weight × reps for that machine that week.

## Cross-cutting details

- All weights respect the current lb/kg unit toggle via `convert()`;
  headers use `unitLabel()`.
- Week bucketing uses a shared helper (`isoWeekStart(dateStr)` → Monday
  `YYYY-MM-DD` key) so sections 2 and 3 agree on week boundaries.
  Timestamps are bucketed the same way existing code buckets days
  (`dayKey`), i.e. based on the recorded timestamp's date.
- Existing sections and their ordering are unchanged; the export remains a
  single self-contained Markdown string.

## Error handling

- Machines present in set rows but absent from strength data: appear in
  sections 2 (as Other region) and 3; simply absent from section 1.
- `STRENGTH_HISTORY` empty → section 1 omitted. No weighted sets →
  sections 2 and 3 omitted. Matches the existing pattern of conditional
  sections.
- Null/non-numeric weight or reps are treated as 0 via the existing
  `Number(x) || 0` idiom.

## Testing

Manual verification against live data (no test harness exists in this
project):

1. Serve the viewer, export, and check the new sections render as valid
   Markdown tables.
2. Verify the Weekly Volume Summary's Volume column sums to the snapshot's
   all-time total volume, and region columns sum to the weekly volume.
3. Spot-check one machine's weekly Top Weight / Est. 1RM / Volume against
   its raw per-set table in the same export.
4. Toggle lb/kg and confirm the new sections convert.
