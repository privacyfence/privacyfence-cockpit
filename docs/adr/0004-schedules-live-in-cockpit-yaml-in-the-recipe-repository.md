# ADR 0004: Schedules live in cockpit.yaml in the recipe repository

## Status

Accepted — 2026-10-20. Implemented on feature/cockpit-v1.

## Context

A schedule decides when a recipe runs unattended. It should be reviewed with the recipe it runs and
survive losing the cockpit's volume.

## Decision

Schedules are declared in `cockpit.yaml` at the root of the recipe repository and parsed by
`src/privacyfence_cockpit/cockpit_yaml.py`; `src/privacyfence_cockpit/scheduler.py` turns them into
queued jobs. Only the pause state, which is operational, is stored in the database.

## Alternatives considered

- **Schedules in the database or the UI** — not reviewed with the recipes and lost with the volume.

## Consequences

Changing a schedule is a commit in the recipe repository followed by an owner-requested update
([ADR 0006](0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md)).
A scheduled run whose selector matches no recipe is not caught by validation and ends as a recipe error.

## Related

- `src/privacyfence_cockpit/cockpit_yaml.py`, `src/privacyfence_cockpit/scheduler.py`
- [ADR 0001](0001-the-cockpit-is-a-separate-repository-and-a-plain-client-of-privacyfence-sync.md)
