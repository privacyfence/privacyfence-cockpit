# ADR 0006: The recipe checkout changes only on the owner's request, fast-forward only, and is never committed to

## Status

Accepted — 2026-10-20. Implemented on feature/cockpit-v1. Amended by [ADR 0009](0009-manual-runs-can-use-another-branch-in-its-own-worktree-schedules-always-use-the-configured-checkout.md): manual runs can use another branch.

## Context

The recipe repository is code that runs unattended with the recipes' tokens. New commits must not
start running without the owner having seen them.

## Decision

The cockpit fetches and shows the new commits; the checkout moves only when the owner asks (or
before scheduled runs, if the owner has turned that on), by `git merge --ff-only`, and never over
tracked changes. It never commits, pushes or overwrites tracked changes
(`src/privacyfence_cockpit/gitrepo.py`). Repository URLs with embedded credentials are rejected and
git error text is scrubbed (see [ADR 0003](0003-secrets-are-encrypted-on-the-volume-with-a-key-from-a-docker-secret-and-only-one-recipe-token-reaches-a-subprocess.md)).

## Alternatives considered

- **Auto-pull on every fetch** — untrusted code would run unattended without the owner looking at it.
- **A webhook from GitHub** — the same problem, and it needs an inbound URL.

## Consequences

A push to the recipe repository takes effect only after the owner's update, so there is a delay and
a deliberate step. A diverged or locally modified checkout stops the update instead of being repaired.

## Related

- `src/privacyfence_cockpit/gitrepo.py`, `src/privacyfence_cockpit/gitauth.py`
- [ADR 0001](0001-the-cockpit-is-a-separate-repository-and-a-plain-client-of-privacyfence-sync.md)
