# ADR 0001: The cockpit is a separate repository and a plain client of privacyfence-sync

## Status

Accepted — 2026-10-20. Not yet implemented; this repository holds only its skeleton.

## Context

The maintainer's existing cockpit runs on a Mac and only works while that laptop is switched on.
Org-mode PrivacyFence runs on a server, so a scheduler that lives next to it can run unattended.

PrivacyFence [ADR 0161](https://github.com/privacyfence/privacyfence/blob/main/docs/adr/0161-privacyfence-sync-is-a-separate-plain-mcp-client.md)
keeps `privacyfence-sync` a plain MCP client of the daemon and rejects running user transforms
inside the daemon.

## Decision

The cockpit lives in its own repository, `privacyfence/privacyfence-cockpit`, and ships as a
container image. It runs `privacyfence-sync` as a subprocess.

- It holds only PrivacyFence scoped tokens (`pfst_`), never connector credentials.
- Recipe scripts run inside the cockpit container, never in the PrivacyFence daemon.

## Alternatives considered

- **A `cockpit/` folder inside the PrivacyFence repository** — rejected by the maintainer, who wants
  the companion released and versioned on its own.
- **Scheduling inside the PrivacyFence daemon** — rejected per PrivacyFence ADR 0161, which keeps
  user transforms out of the daemon.

## Consequences

The cockpit has its own release cadence, tags and CI, and depends on PrivacyFence only through
`privacyfence-sync` and the scoped tokens it is given. A compromised recipe can reach only what
those tokens allow, and nothing in the cockpit can approve a request or change PrivacyFence rules.

## Related

- PrivacyFence ADR 0161, linked above.
