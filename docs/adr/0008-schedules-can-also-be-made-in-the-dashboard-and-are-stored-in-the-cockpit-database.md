# ADR 0008: Schedules can also be made in the dashboard and are stored in the cockpit database

## Status

Accepted — 2026-10-21. Amends [ADR 0004](0004-schedules-live-in-cockpit-yaml-in-the-recipe-repository.md).

## Context

Editing `cockpit.yaml` means a commit in the recipe repository and an owner-requested update, which is
too much for a schedule the owner only wants to try or change quickly. Writing the file from the
dashboard is not an option: the cockpit never commits or pushes and never overwrites tracked changes
([ADR 0006](0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md)),
and a locally edited `cockpit.yaml` would stop every fast-forward update.

## Decision

The owner can create, edit and delete schedules on the Schedules page. They are stored in the
`ui_schedules` table of the cockpit database (`src/privacyfence_cockpit/schedule_store.py`) and
validated by the same rules as `cockpit.yaml`, with each selector also checked against the recipes
at save time. `Cockpit.config()` merges them with the schedules of `cockpit.yaml`, so the scheduler,
the overview and the CLI treat both alike. The schedules of `cockpit.yaml` stay read-only in the
dashboard, and on a name clash the file wins and the dashboard schedule is reported as unused.

## Alternatives considered

- **Write `cockpit.yaml` in the checkout** — leaves the checkout dirty, blocks updates and loses the file's comments.
- **Commit and push from the cockpit** — needs write credentials and overturns ADR 0006.

## Consequences

Dashboard schedules are not reviewed with the recipes and are lost with the volume, which is the
trade-off ADR 0004 chose to avoid; schedules that must be reviewed or survive the volume belong in
`cockpit.yaml`. While `cockpit.yaml` is invalid, no schedule fires, dashboard ones included.

## Related

- `src/privacyfence_cockpit/schedule_store.py`, `src/privacyfence_cockpit/web/routes_schedules.py`
- [ADR 0004](0004-schedules-live-in-cockpit-yaml-in-the-recipe-repository.md)
