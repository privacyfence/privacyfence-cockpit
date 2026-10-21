# PrivacyFence Cockpit

PrivacyFence Cockpit is a Docker image that runs [privacyfence-sync](https://github.com/privacyfence/privacyfence)
recipes on its own schedule and shows their state in a web dashboard. It is a companion to PrivacyFence, released
and versioned separately, and it holds only PrivacyFence scoped tokens, never connector credentials. The reasoning is
in the [architecture decision records](docs/adr/README.md). See also [0002](docs/adr/0002-one-owner-per-container-with-a-local-password-bootstrapped-by-a-one-time-setup-code.md), [0003](docs/adr/0003-secrets-are-encrypted-on-the-volume-with-a-key-from-a-docker-secret-and-only-one-recipe-token-reaches-a-subprocess.md), [0004](docs/adr/0004-schedules-live-in-cockpit-yaml-in-the-recipe-repository.md), [0005](docs/adr/0005-missed-scheduled-runs-are-recorded-and-notified-never-caught-up.md), [0006](docs/adr/0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md) and [0007](docs/adr/0007-one-multi-arch-image-on-ghcr-published-with-provenance-by-the-run-that-cuts-the-tag.md).

- One container, one owner, one private recipe repository (deploy key or read-only access token).
- Schedules live in the recipe repository's `cockpit.yaml`. Runs are serial, logged, recorded in SQLite, and failures
  go to webhooks (generic JSON, Slack or ntfy).
- The checkout changes only when you ask (or before scheduled runs, if you turn that on), and only by fast-forward.
- A `cockpit` command line in the same image for everything the dashboard does, and more.

> **Status.** The image builds and passes its smoke test in CI (`image.yml`), but no release has been cut yet, so
> `release.yml` has not run, and the image has not been tried against a real PrivacyFence server. The checks that still
> need a real host are listed under [Verification status](#verification-status).

## Quick start

You need Docker with Compose, a private Git repository of recipes (see
[the recipe repository guide](docs/recipe-repository.md)), and the URL of your organisation-mode PrivacyFence
server's MCP endpoint.

1. Create the master key, in the folder holding `docker-compose.yml`. The container runs as user 10001, so the file
   must be readable by it:

   ```bash
   openssl rand -base64 32 > cockpit_master_key && chmod 400 cockpit_master_key && sudo chown 10001:10001 cockpit_master_key
   ```

2. Use the [`docker-compose.yml`](docker-compose.yml) from this repository. Set `COCKPIT_SERVER_URL` to your
   PrivacyFence server (for example `https://privacyfence.example.com/mcp`).
3. Start it and read the one-time setup code from the log:

   ```bash
   docker compose up -d
   docker compose logs cockpit
   ```

   The log line reads `No password is set. Setup code: XXXX-XXXX-XXXX.`
4. Open <http://127.0.0.1:8080/setup>, enter the setup code and choose a password of at least 12 characters.
5. Continue with the [user guide](docs/user-guide.md): connect the repository on the Code page, add tokens on the Tokens
   page, run `cockpit preflight`, and add a webhook.

## Master key

The master key encrypts everything the cockpit stores for you: tokens, the deploy key or access token for the recipe
repository, and webhook addresses. It lives outside the data volume, as the Docker secret `cockpit_master_key`.

- **Losing it loses the stored secrets.** Without the original key the cockpit refuses to start with `The master key
  does not match the one that encrypted /data/secrets.` You can then re-enter the secrets by moving
  `/data/secrets` away and starting again; there is no recovery.
- **Back it up apart from the volume.** A backup of `/data` together with the key in the same place is a backup of your
  tokens.
- Without a key file, `cockpit serve` prints `No master key at /run/secrets/cockpit_master_key
  (COCKPIT_MASTER_KEY_FILE).` with the commands to create one, and exits with status 2.

## Remote access

The compose file publishes the dashboard on `127.0.0.1:8080` only. The cockpit does not terminate TLS. To reach it from
elsewhere, put your own TLS reverse proxy in front, for example Caddy:

```
cockpit.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

and tell the cockpit its public address, so session cookies are marked `Secure` and notification links point at it:

```yaml
    environment:
      COCKPIT_PUBLIC_URL: "https://cockpit.example.com"
```

Do not publish port 8080 on a public interface without TLS: the password and the session cookie would travel in the
clear. The login throttles repeated failures, but there is one owner and no other protection.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `COCKPIT_DATA_DIR` | `/data` | The data volume (database, secrets, checkout, environments, logs). |
| `COCKPIT_MASTER_KEY_FILE` | `/run/secrets/cockpit_master_key` | The master key: 32 random bytes, base64-encoded. |
| `COCKPIT_PUBLIC_URL` | unset | The address you browse to, `http://` or `https://`. A trailing `/` is dropped. `https://` turns on secure cookies; it is also the base of the links in notifications. |
| `COCKPIT_SERVER_URL` | unset | The PrivacyFence server's MCP URL. Lowest-priority default; see [the user guide](docs/user-guide.md#settings). |
| `COCKPIT_LISTEN_HOST` | `0.0.0.0` | Address the server binds inside the container. |
| `COCKPIT_LISTEN_PORT` | `8080` | Port the server binds inside the container, 1 to 65535. |
| `COCKPIT_LOG_LEVEL` | `INFO` | Python log level of the server. |
| `PRIVACYFENCE_TOKEN` | unset | A `pfst_…` scoped token that overrides every stored token for runs. See [Tokens](docs/user-guide.md#tokens). |

A wrong value stops the start with a one-line message such as `COCKPIT_LISTEN_PORT must be a number between 1 and
65535`; the value of `PRIVACYFENCE_TOKEN` is never printed.

## Documentation

- [User guide](docs/user-guide.md): first start, connecting the repository, tokens, runs, schedules, updates,
  notifications, settings, the `cockpit` command line, backups and troubleshooting.
- [Recipe repository](docs/recipe-repository.md): the layout the cockpit expects, `requirements.txt`, and every key of
  `cockpit.yaml`.
- [Migrating from the Mac cockpit](docs/migrating-from-the-mac-cockpit.md).
- [Releasing](docs/releasing.md) (maintainers).
- [Architecture decision records](docs/adr/README.md): why it is built this way.
- [Contributing](CONTRIBUTING.md) and the [coding guidelines](docs/coding-guidelines.md).

## Verification status

What is covered by automated tests: everything in the Python package, against a fake `privacyfence-sync` and an
in-process fake MCP server.

What has been proven in CI: the `image.yml` workflow built the image for amd64 and arm64, and for amd64 it started the
image and ran `cockpit check` on a fixture repository (`scripts/smoke_image.sh`). The run on 2026-10-20, dispatched on
the `feature/cockpit-v1` branch before it was merged, passed.

What is not yet proven, because the container this was written in had no Docker and the release has not been cut:

- The compose file has not been started by the project's workflows, and the image has not been run against a real
  PrivacyFence server.
- The release process in [Releasing](docs/releasing.md) (`release.yml`: tag, multi-arch push to GHCR, provenance) has
  not run.
- The token check assumes that `privacyfence_status` returns a `token` object with `label`, `expires_at`, `days_left`
  and `expiring_soon`. That is an assumption not yet verified against a real PrivacyFence server.
- Still to be done by hand after the first release: make the GHCR package public, have the PrivacyFence administrator
  enable scoped tokens and create one for a test recipe, and run the released image on a real host from password
  setup to a webhook for a deliberately denied step.

## License

See [LICENSE](LICENSE).
