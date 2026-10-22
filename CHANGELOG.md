# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

User-visible changes go under `## [Unreleased]` and nowhere else; only the PR that cuts a release
turns it into a version heading. The version itself comes from the git tag (setuptools_scm), never
from this file.

## [Unreleased]

## [0.2.0] - 2026-10-22

### Added

- Manual runs (the dashboard's **Run selected** and `--branch NAME` on the CLI) can use another branch of the
  recipe repository, in a separate worktree with its own recipe environment; schedules keep using the configured
  branch ([ADR 0009](docs/adr/0009-manual-runs-can-use-another-branch-in-its-own-worktree-schedules-always-use-the-configured-checkout.md)).
  The database schema is now version 3 (a `branch` column on runs); an existing database is upgraded on start.
- The Overview page has a checkbox on each group that selects all of its recipes, so a whole group can be run with "Run selected".

## [0.1.0] - 2026-10-21

### Added

- Schedules can be created, edited and deleted on the Schedules page. They are stored in the cockpit's
  database next to the schedules of `cockpit.yaml`, which stay read-only there
  ([ADR 0008](docs/adr/0008-schedules-can-also-be-made-in-the-dashboard-and-are-stored-in-the-cockpit-database.md)).
  The database schema is now version 2; an existing database is upgraded on start.

## [0.0.4] - 2026-10-21

### Fixed

- **Fetch now** before the first clone no longer stores a fetch failure and shows a banner: the button appears
  only once the repository is cloned, and the server answers `The repository has not been cloned.` instead.
- The Code page says `The remote has no branch '<name>'. Check the branch name, or push a first commit to it.`
  when the configured branch does not exist on the remote, instead of a raw `git log` error.

## [0.0.3] - 2026-10-21

### Fixed

- Setting the password on first start (and logging in) failed with "Invalid or missing CSRF token" when the
  browser asked for `/favicon.ico` while the form was open: the icon request was redirected to the form page,
  which replaced the CSRF cookie. The icon is now answered with an empty response, and the form pages keep an
  existing CSRF cookie.

## [0.0.2] - 2026-10-21

### Changed

- Republished with build provenance. Version 0.0.1 was published without it, because attestations were not
  available while the repository was private; there are no code changes since 0.0.1.

## [0.0.1] - 2026-10-21

### Added

- The container image and a Docker Compose example.
- A web dashboard: overview, runs with live logs, history and `runs.csv`, schedules, code browser, tokens,
  permissions, notifications and settings.
- Scheduling from `cockpit.yaml` in the recipe repository; missed runs are recorded and notified, not caught up.
- A git-backed recipe repository, read with a deploy key or an access token and updated only on request.
- Recipe environments, with the `privacyfence-sync` feature gate.
- Encrypted secrets, protected by a master key supplied as a Docker secret.
- Webhook notifications: generic JSON, Slack and ntfy.
- The `cockpit` command line.
- A warning banner and a failure notification when a schedule's selectors match no recipe.
- The release workflow, publishing a multi-arch image with build provenance.
- Architecture decision records 0002 to 0007.
