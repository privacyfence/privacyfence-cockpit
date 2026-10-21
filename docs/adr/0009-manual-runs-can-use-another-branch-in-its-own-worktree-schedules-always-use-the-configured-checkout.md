# ADR 0009: Manual runs can use another branch of the recipe repository, in its own worktree and environment; schedules always use the configured checkout

## Status

Accepted — 2026-10-21. Amends [ADR 0003](0003-secrets-are-encrypted-on-the-volume-with-a-key-from-a-docker-secret-and-only-one-recipe-token-reaches-a-subprocess.md) and [ADR 0006](0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md).

## Context

The owner wants to try recipes from a work-in-progress branch without merging it first. Until now every
run, manual or scheduled, used the one checkout of the configured branch, which moves only on the owner's
request ([ADR 0006](0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md)).

## Decision

A manual run can name another branch of the recipe repository: the dashboard's **Run selected** and
`--branch NAME` on the CLI's `run`, `check`, `preflight` and `test`. Schedules, the per-recipe **Run**
button and **Run now** always use the configured checkout.

- The fetch downloads every branch of the remote (`+refs/heads/*:refs/remotes/origin/*`). The configured
  checkout is still moved only by an owner-requested update.
- Each branch gets its own git worktree under `/data/branches/<branch>` (a `/` in the name is written
  `%2F`). At every run the worktree is moved to the branch's newest fetched commit, detached. Git-ignored
  files, such as a recipe's `work/` folder, are kept between runs of the same branch and start empty the
  first time. A worktree with changed tracked files is never overwritten; the run fails with the command
  to inspect it. A worktree is removed with its contents when its branch no longer exists on the remote,
  and all of them are removed by **Re-clone**.
- The recipe environment is reused from `/data/venvs` when the branch's requirements hash to an existing
  environment and nothing is installed from the checkout (no local packages). Otherwise one is built in
  `/data/branch-venvs`, of which the two most recently used are kept. Branch runs never change the
  configured checkout's current environment.
- Every mode is allowed on a branch, with the same token rules as any manual run.
- Server, timezone and timeout come from the configured checkout's `cockpit.yaml`; the branch's own
  `cockpit.yaml` is not read.
- The run's history row records the branch (database schema version 3), and the run page, the log header
  and the CSV export show it. Branch runs are left out of a recipe's "last run".
- Trust: a branch run executes whatever is on the branch's newest fetched commit, including
  `pip install` of its `requirements.txt` and, with `cockpit test --branch`, its tests, without the update
  step of ADR 0006. The owner's choice of branch is the review. Nothing runs from a branch on a schedule
  or automatically.

## Alternatives considered

- **Switch the configured checkout to the branch for the run and back** — a crash between the two
  checkouts leaves schedules on the wrong code, and it breaks ADR 0006's rule that the checkout moves only
  by fast-forward on the owner's request.
- **Share each recipe's `work/` with the configured checkout** — a branch run could corrupt the state
  scheduled runs depend on; the owner chose separate, initially empty folders.
- **Only non-writing modes on a branch** — the owner picks the branch deliberately and the token rules are
  the same.
- **`git ls-remote` on every Overview render to list branches** — a network call per page view; fetching
  all heads in the existing fetch gives the list for free.
- **Read the branch's `cockpit.yaml`** — two configurations at once would be confusing, and schedules from
  a branch are out of scope.
- **Build branch environments in `/data/venvs`** — that folder is pruned for the configured checkout only.
- **Reuse an environment when the repository installs local packages** — it would point into, or hold a
  copy of, another checkout's library code.

## Consequences

Branch code runs with recipe-code reach (the master key and `/data/secrets`, see ADR 0003) as soon as the
owner picks the branch, so only run branches you have looked at. Worktrees and environments use disk space.
A branch's `work/` starts empty. The branch list is as fresh as the last fetch. Deleting a branch on the
remote loses any tracked changes in its worktree.

## Related

- `src/privacyfence_cockpit/branches.py`, `src/privacyfence_cockpit/gitrepo.py`,
  `src/privacyfence_cockpit/venvs.py`, `src/privacyfence_cockpit/jobs.py`
- [ADR 0003](0003-secrets-are-encrypted-on-the-volume-with-a-key-from-a-docker-secret-and-only-one-recipe-token-reaches-a-subprocess.md),
  [ADR 0006](0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md)
