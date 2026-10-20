# Contributing

## Branches and pull requests

1. Branch names are `<type>/<kebab-case>`, where the type is `feature`, `fix`, `chore` or `tests`.
2. Changes land through pull requests only. PRs merge with a merge commit, never a squash, so each
   commit message on your branch ends up in `main`'s history.
3. Keep PRs focused, and describe *why* the change is needed, not just what it does.
4. Before opening a PR, work through the
   [definition of done](docs/coding-guidelines.md#definition-of-done).

## Changelog

A user-visible change adds a line under `CHANGELOG.md`'s `## [Unreleased]` heading in the same PR.
Never open a concrete `## [X.Y.Z]` heading on a feature branch; only the PR that cuts a release does.

## Decisions, plans and ADRs

Three kinds of document, three lifecycles — the full rules, template and index are in
[`docs/adr/README.md`](docs/adr/README.md):

- **Plans** (what we are about to do) are temporary: a GitHub issue, or a `docs/*-plan.md` while
  its work is open. They are deleted when the work lands.
- **ADRs** (`docs/adr/NNNN-*.md` — why it is this way, what was rejected) are permanent and frozen
  once accepted. Change your mind with a new ADR that supersedes the old one; never rewrite an
  accepted ADR's body, and never put implementation progress in one.
- **Reference docs** (`docs/*.md`, this file) describe today's behavior and link to ADRs for the
  *why* rather than retelling it.

**Retiring a plan requires extracting its decisions first.** The PR that deletes a plan document
adds or amends an ADR for every decision the plan made — anything hard to reverse, touching a trust
boundary or the build/release path, or rejecting an alternative for a non-obvious reason — or says
in its description that the plan made none.

The same applies when a decision is made somewhere else — a PR thread, an issue, a reference doc:
if it meets the bar above, it gets an ADR in the same PR. ADRs link to issues, PRs, commits and
source files, never to a plan document, which will not outlive it.
