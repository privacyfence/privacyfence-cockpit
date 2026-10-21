# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

User-visible changes go under `## [Unreleased]` and nowhere else; only the PR that cuts a release
turns it into a version heading. The version itself comes from the git tag (setuptools_scm), never
from this file.

## [Unreleased]

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
