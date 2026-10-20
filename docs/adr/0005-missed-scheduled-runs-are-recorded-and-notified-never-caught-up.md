# ADR 0005: Missed scheduled runs are recorded and notified, never caught up

## Status

Accepted — 2026-10-20. Implemented on feature/cockpit-v1.

## Context

If the cockpit is stopped across scheduled times, it must decide what to do with the fires it missed.

## Decision

Fires older than a short grace period when the scheduler looks are recorded as missed and notified,
and not run (`src/privacyfence_cockpit/scheduler.py`). The next regular fire does the work.

## Alternatives considered

- **Catching up missed runs** — after a long outage a burst of stale runs would hit the sources at
  once. Recipes are idempotent and diff against the target, so the next scheduled run does the work anyway.

## Consequences

An outage never causes a burst of runs, and the owner learns about the gap. A recipe that must
run at an exact time cannot rely on the cockpit having been up at that time.

## Related

- `src/privacyfence_cockpit/scheduler.py`
- [ADR 0004](0004-schedules-live-in-cockpit-yaml-in-the-recipe-repository.md)
