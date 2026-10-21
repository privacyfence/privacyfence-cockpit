# ADR 0002: One owner per container, with a local password bootstrapped by a one-time setup code

## Status

Accepted — 2026-10-20. Implemented on feature/cockpit-v1.

## Context

The dashboard can start runs, change schedules and show run output, so it needs authentication. The
target operators run one container on a host they control and do not run an identity provider.
A published port with no authentication is a full takeover of the recipes' tokens.

## Decision

A container has exactly one owner. The first visit asks for a one-time setup code printed in the
container log (only its hash is stored); the owner then chooses a password, kept as a scrypt hash.
Login is throttled and sessions and CSRF are handled by the middlewares in
`src/privacyfence_cockpit/web/auth.py`. Nothing outside the container is needed.

## Alternatives considered

- **Accounts with roles** — needs a user model and permissions for a tool that has one operator.
- **OIDC** — needs an identity provider the target owners do not run.
- **No authentication, relying on a reverse proxy** — fails open when the port is published by mistake.

## Consequences

Setup is a single log line and a password. There is no second user and no password-reset email: a
lost password is recovered from the container, not through the web UI. Sharing access means sharing
the owner's password.

## Related

- `src/privacyfence_cockpit/web/auth.py`
- [ADR 0001](0001-the-cockpit-is-a-separate-repository-and-a-plain-client-of-privacyfence-sync.md)
