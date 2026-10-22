# Architecture Decision Records

An **Architecture Decision Record (ADR)** is a short, numbered, permanent note of one significant
decision: the situation that forced it, what was decided, what was rejected and why, and what
follows from it. ADRs answer the question a reference doc cannot: *why is it this way, and why
didn't we just…?*

## Where each kind of document lives

| Kind | Answers | Home | Lifecycle |
|---|---|---|---|
| **Plan** | What are we about to do, and in what order? | A GitHub issue, or a `docs/*-plan.md` while its work is open | Temporary. Deleted when its work lands, **after** its decisions have been extracted here |
| **ADR** | Why is it this way, and what was rejected? | `docs/adr/NNNN-*.md` | Permanent. After acceptance only its Status changes |
| **Reference** | How does it work today? | `docs/*.md`, `CONTRIBUTING.md`, code docstrings | Living. Edited in the same PR as the behavior |

## When a decision needs an ADR

Write one when a decision meets **any** of these:

- it is expensive or disruptive to reverse;
- it sets or moves a trust or security boundary, or decides who holds a credential;
- it changes how the image is built, released or distributed;
- it rejects an alternative a reasonable newcomer would propose, for a reason that isn't obvious;
- someone will plausibly ask "why didn't we just…?" in six months.

Implementation detail, naming and task sequencing don't need one. A 40-line ADR is fine.

## Rules

1. **One decision per ADR.**
2. **Frozen once accepted.** The only permitted edits are the Status section, fixing a broken link,
   and typos. Implementation progress belongs in the tracking issue, not here.
3. **Change your mind with a new ADR** that says `Supersedes NNNN` (or `Amends NNNN`); the old one's
   Status gains one line pointing forward.
4. **Link only to things that last:** issues, PRs, commit SHAs, source files, other ADRs. Never a
   plan document, which will be deleted.
5. **Retiring a plan is gated on this directory.** The PR that deletes a plan adds or amends the
   ADRs for every decision the plan contained, or states in its description that it contained none.
6. **Numbers are never reused.** Take the next free number when you open the PR; renumber yours if
   a parallel branch takes it first.
7. **Update the index below** in the same PR.

### Status values

| Status | Meaning |
|---|---|
| `Proposed` | Under discussion; decides nothing yet |
| `Accepted` | In force. Say whether it is implemented if that isn't obvious |
| `Rejected` | Considered and declined; kept so it isn't re-proposed from scratch |
| `Superseded by NNNN` | Replaced; kept for the reasoning trail |
| `Deprecated` | No longer relevant, with no replacement |

## Template

Copy this into `docs/adr/NNNN-short-kebab-title.md`. The title states the decision, not the topic.

```markdown
# ADR NNNN: <the decision, stated as a short sentence>

## Status

Accepted — YYYY-MM-DD. <One line on implementation state if not obvious.>
<Supersedes / Amends / Superseded by lines, one each, if any.>

## Context

What forced a decision: the problem, the constraints, the facts that mattered. Short.

## Decision

What was decided, stated so it can be checked against the code.

## Alternatives considered

- **<Alternative>** — why it was rejected.

## Consequences

What becomes easier, what becomes harder, what is now ruled out, what risk is accepted.

## Related

Issues, PRs, commits, other ADRs.
```

## Index

| # | Decision | Status |
|---|---|---|
| [0001](0001-the-cockpit-is-a-separate-repository-and-a-plain-client-of-privacyfence-sync.md) | The cockpit is a separate repository and a plain client of privacyfence-sync | Accepted |
| [0002](0002-one-owner-per-container-with-a-local-password-bootstrapped-by-a-one-time-setup-code.md) | One owner per container, with a local password bootstrapped by a one-time setup code | Accepted |
| [0003](0003-secrets-are-encrypted-on-the-volume-with-a-key-from-a-docker-secret-and-only-one-recipe-token-reaches-a-subprocess.md) | Secrets are encrypted on the volume with a key from a Docker secret, and only one recipe token reaches a subprocess | Accepted |
| [0004](0004-schedules-live-in-cockpit-yaml-in-the-recipe-repository.md) | Schedules live in cockpit.yaml in the recipe repository | Accepted |
| [0005](0005-missed-scheduled-runs-are-recorded-and-notified-never-caught-up.md) | Missed scheduled runs are recorded and notified, never caught up | Accepted |
| [0006](0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md) | The recipe checkout changes only on the owner's request, fast-forward only, and is never committed to | Accepted |
| [0007](0007-one-multi-arch-image-on-ghcr-published-with-provenance-by-the-run-that-cuts-the-tag.md) | One multi-arch image on GHCR, published with provenance by the run that cuts the tag | Accepted |
| [0008](0008-schedules-can-also-be-made-in-the-dashboard-and-are-stored-in-the-cockpit-database.md) | Schedules can also be made in the dashboard and are stored in the cockpit database | Accepted |
| [0009](0009-manual-runs-can-use-another-branch-in-its-own-worktree-schedules-always-use-the-configured-checkout.md) | Manual runs can use another branch of the recipe repository, in its own worktree and environment; schedules always use the configured checkout | Accepted |
