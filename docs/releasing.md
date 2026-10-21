# Releasing

Releases are cut by the maintainer from the **Release** workflow
(`.github/workflows/release.yml`). Nothing is bumped in the repository: the version comes from the
git tag (setuptools_scm), and the image is built with `VERSION` set to it.

## Steps

1. Open a release PR that moves the entries under `## [Unreleased]` in `CHANGELOG.md` to a new
   heading `## [X.Y.Z] - YYYY-MM-DD`, leaving `## [Unreleased]` empty above it. Merge it into
   `main`.
2. Run **Release** (Actions → Release → Run workflow) on `main` with `version` = `X.Y.Z` and
   `dry_run: true`. It runs every check and stops before tagging.
3. If it is green, run it again with `dry_run: false`.

The `prepare` job fails, with one `::error::` line per problem, unless:

- the version has the form `X.Y.Z`;
- `HEAD` is the tip of `origin/main`;
- tag `vX.Y.Z` does not exist and the version is higher than every existing `vX.Y.Z` tag
  (compared as numbers, so `1.10.0` is after `1.9.0`; other tags are ignored);
- `CHANGELOG.md` has a `## [X.Y.Z]` heading below an empty `## [Unreleased]` section.

With `dry_run: false` it then pushes the annotated tag `vX.Y.Z` with the workflow's
`GITHUB_TOKEN`. A tag pushed that way starts no other workflow, so the same run publishes the image.
Pushing a `v*` tag by hand also runs the workflow: it checks the tag form and that the tagged
commit is on `main`, then publishes.

## What is published

`ghcr.io/privacyfence/privacyfence-cockpit` for `linux/amd64` and `linux/arm64`, tagged `X.Y.Z`
and `X.Y`, plus `latest` only when `X.Y.Z` is the highest release (a patch for an older line does
not move `latest`). The amd64 image is smoke-tested (`scripts/smoke_image.sh`) before anything is
pushed, and a build provenance attestation is stored with the image in the registry.

## Verifying provenance

```bash
gh attestation verify oci://ghcr.io/privacyfence/privacyfence-cockpit:X.Y.Z --owner privacyfence
```
