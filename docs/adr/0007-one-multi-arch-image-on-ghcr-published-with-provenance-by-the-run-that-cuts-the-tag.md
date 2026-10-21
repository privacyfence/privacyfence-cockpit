# ADR 0007: One multi-arch image on GHCR, published with provenance by the run that cuts the tag

## Status

Accepted — 2026-10-20. The decision is accepted. The build half of the pipeline is proven: `image.yml` built the image for
amd64 and arm64 and passed its smoke test in CI. The publishing half is not: `release.yml` (tag, push to GHCR,
provenance) has not run yet.

## Context

The cockpit is distributed only as a container image. Users must be able to pull one reference on
amd64 and arm64 hosts and check where it came from.

## Decision

- One multi-arch image (linux/amd64, linux/arm64) on GHCR, built from a digest-pinned base image
  with hash-locked dependencies (`Dockerfile`).
- The image is smoke-tested before it is published.
- `.github/workflows/release.yml` publishes it with build provenance attestation. The dispatch
  that cuts the tag publishes the image in the same run; a tag pushed by hand publishes through the
  `push: tags` trigger.

## Alternatives considered

- **A two-workflow release with a long-lived `RELEASE_TAG_TOKEN`** — that chain exists because a tag
  pushed with `GITHUB_TOKEN` starts no workflow. Here the same run publishes, so no long-lived secret is needed.
- **`latest`-only tags, an unpinned base image, unhashed dependencies** — not reproducible or verifiable.

## Consequences

No release secret has to be stored. Pinned digests and hashes need regular updates. Until the
workflows have run successfully, nothing about the published image or its provenance is verified.

## Related

- `Dockerfile`, `.github/workflows/release.yml`
- [ADR 0001](0001-the-cockpit-is-a-separate-repository-and-a-plain-client-of-privacyfence-sync.md)
