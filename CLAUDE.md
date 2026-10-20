# CLAUDE.md

Notes for Claude Code sessions on PrivacyFence Cockpit. Everything a human contributor also needs
lives in the ordinary docs:

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branch naming, pull requests, changelog, and the plan/ADR
  lifecycle.
- [`docs/coding-guidelines.md`](docs/coding-guidelines.md) — code and test conventions, and the
  definition of done.
- [`docs/adr/README.md`](docs/adr/README.md) — the ADR rules and index.

## Commands and skills

`/make-plan`, `/implement` and `/dod` come from the claude-toolkit plugins (devflow). The project
facts they use are in [`.claude/toolkit.yaml`](.claude/toolkit.yaml).

## Cloud sessions

Docker is not available in a cloud session. Anything that needs `docker build` or a running
container is dispatched to the `image.yml` workflow once that workflow exists.

A cloud checkout arrives shallow and without tags, so setuptools_scm would bake in the fallback
version. Before the definition of done, run `git fetch --unshallow --tags origin` and
`python3 -m pip install -e ".[dev,test]"`.
