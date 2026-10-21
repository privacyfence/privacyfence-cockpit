# ADR 0003: Secrets are encrypted on the volume with a key from a Docker secret, and only one recipe token reaches a subprocess

## Status

Accepted — 2026-10-20. Implemented on feature/cockpit-v1. Amended by [ADR 0009](0009-manual-runs-can-use-another-branch-in-its-own-worktree-schedules-always-use-the-configured-checkout.md): manual runs can use another branch.

## Context

The cockpit stores PrivacyFence scoped tokens (`pfst_`), a git deploy key or access token, and
webhook URLs. A copied volume or backup must not hand these over, and a recipe subprocess should be
given no more than its own token in its environment. Recipe code itself is trusted, not sandboxed:
see the Decision and Consequences. See [ADR 0001](0001-the-cockpit-is-a-separate-repository-and-a-plain-client-of-privacyfence-sync.md)
for why the cockpit holds only scoped tokens.

## Decision

- Secrets are AES-GCM encrypted into files under `/data/secrets/<kind>/*.bin` on the volume
  (`src/privacyfence_cockpit/secrets_store.py`, `crypto.py`).
- The 32-byte master key is read from a Docker secret file (`COCKPIT_MASTER_KEY_FILE`), never from
  the volume or an environment variable.
- A recipe subprocess gets the environment of that one recipe, with only that recipe's token. This
  limits the environment only. Recipe code runs as the same user (10001) as the cockpit, so it can
  read the master key file, `/data/secrets` and the temporary git credential files of a fetch that
  runs at the same time, and could therefore decrypt every stored secret. Recipe code is trusted;
  it is reviewed under [ADR 0006](0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md).
- Git credentials (`src/privacyfence_cockpit/gitauth.py`) are written to a 0600 file in the tmpfs
  `/tmp` for the length of one command and removed afterwards; they are not passed through the
  environment or argv.
- Repository URLs that embed credentials are rejected, and git's error text is scrubbed before it
  is shown or stored (`src/privacyfence_cockpit/gitrepo.py`).

## Alternatives considered

- **Plain-text secrets on the volume** — a copied volume or backup would expose every token.
- **Master key in an environment variable** — visible in `docker inspect` and inherited by every child process.
- **Git credential through environment or argv** — visible to other processes; rejected for the same reason.

## Consequences

Losing the master key makes stored secrets unrecoverable; the owner must keep it separately from
volume backups. The volume alone is not enough to read secrets. Isolating recipe code from the master key and the secret
files is not provided; a malicious recipe could read them. The token payload shape returned by
`privacyfence_status` that the token check relies on is an assumption not yet verified against a
real server.

## Related

- `src/privacyfence_cockpit/crypto.py`, `secrets_store.py`, `gitauth.py`, `gitrepo.py`
- [ADR 0001](0001-the-cockpit-is-a-separate-repository-and-a-plain-client-of-privacyfence-sync.md)
