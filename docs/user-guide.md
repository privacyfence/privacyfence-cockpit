# User guide

This guide describes PrivacyFence Cockpit as it behaves today. For installation, the master key and the environment
variables, see the [README](../README.md). For the layout of the recipe repository and `cockpit.yaml`, see
[the recipe repository guide](recipe-repository.md). The reasoning behind the design is in the
[architecture decision records](adr/README.md). See also [0002](adr/0002-one-owner-per-container-with-a-local-password-bootstrapped-by-a-one-time-setup-code.md), [0003](adr/0003-secrets-are-encrypted-on-the-volume-with-a-key-from-a-docker-secret-and-only-one-recipe-token-reaches-a-subprocess.md), [0004](adr/0004-schedules-live-in-cockpit-yaml-in-the-recipe-repository.md), [0005](adr/0005-missed-scheduled-runs-are-recorded-and-notified-never-caught-up.md), [0006](adr/0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md) and [0007](adr/0007-one-multi-arch-image-on-ghcr-published-with-provenance-by-the-run-that-cuts-the-tag.md).

The commands below assume the compose service is called `cockpit`, as in [`docker-compose.yml`](../docker-compose.yml).
Run the `cockpit` command line inside the running container:

```bash
docker compose exec cockpit cockpit list
```

## First start

1. `docker compose up -d`, then `docker compose logs cockpit`. With no password set, the cockpit writes one warning
   line with a one-time setup code: `No password is set. Setup code: XXXX-XXXX-XXXX. Open <your public URL or "the
   cockpit">/setup to set one.` This is the only log line that carries a credential.
2. Open `/setup` (<http://127.0.0.1:8080/setup> with the default compose file), enter the code and a password of at
   least 12 characters twice. The code works once. You are logged in and taken to the Overview.
3. Until a password exists every page redirects to `/setup`; afterwards, to `/login`. Sessions last 7 days. After five
   failed logins in a row the login is refused for a growing time (30 seconds, doubling, at most 15 minutes) with `Too
   many failed attempts. Try again in N seconds.`
4. The pages are Overview, History, Schedules, Code, Tokens, Permissions, Notifications and Settings.

Change the password on the Settings page (other sessions are logged out). If you have lost it, run
`docker compose exec cockpit cockpit user reset-password`: it clears the password and every session and prints a new
setup code (`Password cleared. Setup code: …`), which you use at `/setup`.

The setup code protects a fresh container: whoever can read the container's log or run `cockpit` in it is the owner.

## Connecting the recipe repository

On the **Code** page, enter the repository URL, the branch (default `main`) and the access method, then **Save**.

| Access | Use for | What you provide |
|---|---|---|
| `ssh (deploy key)` | `git@host:org/repo`, `ssh://git@host/org/repo` | A deploy key the cockpit generates |
| `https (access token)` | `https://host/org/repo` | A token you paste into the page |

Accepted URLs start with `https://`, `ssh://` or `user@host:path` (the scp-style form, such as
`git@github.com:org/repo.git`); `ssh://git@host/...` is fine too.

**Credentials in the URL are refused.** A URL with a user name and password or token (`https://user:secret@host/...`,
any `http(s)` URL with `userinfo@`, or an `ssh://` URL with a password) is rejected with `Do not put credentials in
the repository URL; use the access token field instead.` The submitted URL is not echoed back. Git error messages
shown by the cockpit have any `userinfo@` part of a URL replaced by `***@`.

### Deploy key (ssh)

1. Press **Generate a deploy key**. The page shows the public half (an Ed25519 key, comment `privacyfence-cockpit`).
   The private half is encrypted with the master key and is never shown.
2. For a GitHub repository the page links to the repository's *Add deploy key* form. Paste the public key there and
   leave **Allow write access** off. The cockpit only reads from the repository.
3. **Host key.** For `github.com` the host's published SSH keys are built in. For any other host the page shows `The
   server's identity is not trusted yet.`: press **Scan host keys**, compare the shown fingerprints with the ones your
   Git server publishes, then press **Trust these host keys**. Clones and fetches use strict host key checking, so
   nothing is trusted without this step.
4. Press **Clone**. The button stays disabled until the key exists and the host is trusted.

Generating a new key replaces the old one and needs the **Replace the existing key** box ticked; fetching stops until the
new key is added on the Git server.

### Access token (https)

Create a fine-grained token with read-only *Contents* access to this one repository, paste it into **Access token**
and press **Save token**. The page shows only `present` or `missing`, never the value. The token reaches git through a
short-lived file, never through the command line or the environment.

## Tokens

The cockpit stores **PrivacyFence scoped tokens only**: values starting with `pfst_`, issued by your PrivacyFence
administrator. Anything else is refused with `Only PrivacyFence scoped tokens (pfst_…) are supported.` A token's value is
never displayed after saving, in the dashboard or on the command line.

### Names and the lookup chain

A token is stored under a name: a recipe path (`imports/10-sf-milestones`), a group name (`imports`) or `default`. For
each run the cockpit uses the first of these that exists:

1. `PRIVACYFENCE_TOKEN` in the container's environment, if set. It overrides every stored token for **runs**, and the
   Tokens page and every page's banner say so. Use it for a one-off or a CI-style setup.
2. the token named after the recipe path,
3. the token named after the recipe's group,
4. the token named `default`.

With none of these, the run starts without a token. The run's page shows where the token came from (`recipe:<path>`,
`group:<name>`, `default`, `env` or `none`), never its value. Only the one chosen token reaches the recipe process.

A misspelt name is refused, with a suggestion (`unknown token name 'imports/10-sf-milestone'; did you mean
'imports/10-sf-milestones'? Token names are recipe paths, group names or default.`).

### Setting tokens

On the dashboard, use **Set a token** on the Tokens page (name, token, **Save and check**). On the command line:

```bash
docker compose exec cockpit cockpit token set default
```

prompts `Token: ` without echoing. A single line on standard input also works (`docker compose exec -T cockpit cockpit
token set default < token.txt`); the value is never read from an argument. `cockpit token show` lists every valid name
with `present` or `missing`; `cockpit token delete NAME` removes one.

### Checks

After each save, once a day, and when you press **Check all now**, the cockpit connects to the PrivacyFence server and
calls its `privacyfence_status` tool with each stored token. The server used is the
[effective server URL](#settings), whichever recipe the token belongs to. The check always uses the stored tokens, not
`PRIVACYFENCE_TOKEN`.

> **Not yet verified.** The check expects `privacyfence_status` to return a `token` object with `label`, `expires_at`,
> `days_left` and `expiring_soon`. That is an assumption not yet verified against a real PrivacyFence server. If the
> real response differs, tokens will show as `not scoped`.

| State | Meaning |
|---|---|
| ok | The server accepted the token. |
| expiring | The server reports `expiring_soon`. |
| expired | `days_left` is 0. |
| refused | The server rejected the token: revoked or expired, or the server URL is wrong. |
| not scoped | The response has no `token` object: `not a scoped token; v1 supports PrivacyFence scoped tokens only`. |
| unreachable | `cannot reach <server URL>`. |
| no server | No PrivacyFence server URL is configured. |

Webhook notifications go out when a token is refused or expired (once per streak) and when its days left first fall to
14, 7 and 1 (see [Notifications](#notifications)).

When each token was last used and last denied is shown by PrivacyFence itself, on its own Settings page, not by the
cockpit. The Tokens page links to it (`<scheme>://<your server host>/settings`).

## Runs

### From the Overview

The Overview lists the recipes by group, with each recipe's last run, the next scheduled run that includes it, and a
**Run** button. Tick several and use **Run selected** with a mode:

| Mode | What it does |
|---|---|
| `unattended` | `privacyfence-sync run`, no approvals: a step that needs an approval card is denied. This is what schedules use. |
| `attended` | `privacyfence-sync run --attended`: waits for you to approve steps in PrivacyFence. |
| `preflight` | `privacyfence-sync preflight`: reports which steps the token would be denied; runs nothing. |
| `check` | `privacyfence-sync check` plus the cockpit's own check that the recipe's `work_dir` is git-ignored. |
| `replay` | Live replay of the recipe's leading read-only steps; see [Live replay](recipe-repository.md#live-replay). |

**Branch and Also run.** When the remote has branches besides the configured one, **Run selected** also shows a
**Branch** select and an **Also run** box (one recipe or group per line, for recipes that exist only on the branch).
Every mode works on a branch. The run uses `/data/branches/<branch>` at the branch's newest commit after a fetch, with
its own `work/` folders and recipe environment, and the server, timezone and timeout from the configured
`cockpit.yaml`. The per-recipe **Run** button, **Run now** and schedules always use the configured checkout, and branch
runs do not change a recipe's **Last run**. Running a branch runs its code with the same access as any recipe, so only
run branches you have looked at.

A recipe that sets its own `server:` talks to that server whatever the cockpit's effective server is; the Overview shows
`server: <url>` under its name.

Runs are **serial**. Starting one while another is running queues it; the page says `Nothing is running.` or `Running:
…`, and how many jobs are queued. A queued job can be cancelled from its job page until it starts. A run's page shows its
live log, an exit code, the commit it ran at and the token source, and has **Stop** and **Download log**. Stopping
terminates the recipe's whole process group (SIGTERM, then SIGKILL after 10 seconds) and records `stopped`.

### Outcomes

| Outcome | Meaning |
|---|---|
| ok | The recipe finished and changed something. |
| no change | The recipe finished and found nothing to do. Counts as success. |
| recipe error | The recipe is invalid or a step is mis-specified (also: a failed `check`, such as a `work_dir` that is not git-ignored, and a schedule selector that matches nothing: a warning banner on every page links to the Schedules page, and a scheduled firing sends a failure notification). |
| failed | A step or the sync failed, or the recipe environment is not ready, or the cockpit stopped during the run. |
| denied | PrivacyFence denied a step. Treat it as a missing rule, not a bug: see the [Permissions](#permissions) page. |
| not signed in | No usable token. |
| stopped | You stopped it, or the cockpit was shut down during the run (the note says `the cockpit was shut down`). |
| timed out | It exceeded `timeout_minutes` (60 by default; set in `cockpit.yaml`). |
| missed | A scheduled fire passed while the cockpit was not running. See [Schedules](#schedules). |

For `run`, `attended` and `replay`, the outcome comes from the result file privacyfence-sync writes; in the other modes it
comes from the exit code (0 ok, 1 recipe error, 2 failed, 3 denied, 4 not signed in). A run that was still marked
running when the cockpit started again is closed as `failed` with the note `the cockpit stopped during this run`.

### History and logs

**History** lists every run and filters by recipe, outcome, mode, trigger (`manual` or `schedule:<name>`) and dates.
**Download runs.csv** exports the filtered rows in order with the columns `id, started_at, finished_at, trigger, recipe,
mode, outcome, exit_code, duration_seconds, commit, token_source, log_path, note, branch`.

Each run's log is a file under `/data/logs/<date>/`, with a header (command, token *source*, commit, and a `# branch:` line for branch runs) and a footer
(exit code, outcome, duration). Log folders older than the retention (90 days by default, see [Settings](#settings))
are deleted daily; the history rows stay and the run page then shows `log deleted after the retention period`.

## Schedules

Schedules come from two places. Those in `cockpit.yaml` in the recipe repository are reviewed with the recipes and are
read-only in the dashboard (see [the `cockpit.yaml` reference](recipe-repository.md#cockpityaml)). Those made on the
Schedules page with **New schedule** are stored in the cockpit's database, can be edited and deleted there, and are lost
with the volume; put a schedule in `cockpit.yaml` if it must be reviewed or survive that. Both kinds have a name, a cron
expression in the effective timezone, a list of recipes or groups to run (one per line), an optional repeat count and an
optional stop-on-error. A name cannot be changed after it is saved, and every recipe or group must exist when you save.
If a dashboard schedule has the name of one in `cockpit.yaml`, the file wins and the page says the dashboard one is unused.

The **Schedules** page shows each schedule's next run, whether it is active or paused, and its last run, with **Pause**,
**Resume** and **Run now**. Pausing is stored in the cockpit's database, not in `cockpit.yaml`. **Run now** queues the
schedule's recipes as a manual job.

- A schedule that is still queued or running when its next fire arrives skips that fire.
- Scheduled runs are unattended.
- **Missed fires are recorded, never caught up.** If the cockpit was down when a fire was due (more than 90 seconds
  late), one `missed` row per schedule says how many runs were missed and between when, the Schedules page lists them
  under **Missed runs**, and a notification goes out. The cockpit does not run them afterwards.
- A problem in `cockpit.yaml` shows as an error banner and pauses all scheduling, dashboard schedules included, until it is fixed.

## The Code page and the update model

The recipe checkout changes **only when you ask**, by fast-forward only, and is never committed to.

- The cockpit fetches the remote every 15 minutes (a setting, 5 to 1440). A fetch downloads commits but does not touch
  the checkout. The page shows the commit checked out, the remote tip and `New commits`.
- The fetch downloads every branch of the remote, but only the configured one is checked out. Branch runs use their own
  worktrees under `/data/branches`. A worktree with changed tracked files blocks branch runs of that branch (the error
  names the folder) until you clean it with `docker exec <container> git -C /data/branches/<folder> checkout -- .`,
  the branch is deleted on the remote, or you use **Re-clone**, which also deletes the branch worktrees and their
  environments.
- **Update to latest** fast-forwards the checkout to the remote tip, then rebuilds the recipe environment if
  `requirements.txt` changed. It refuses when tracked files in the checkout have changed (`The checkout has changes to
  tracked files and the cockpit never overwrites them.`) and when the remote branch was rewritten.
- **Update before each scheduled run** (Settings) does the same before scheduled runs only; manual runs use whatever is
  checked out.
- **Check out this commit** on a commit in the list pins the checkout to it (detached). While pinned, schedules run
  the pinned commit, and a banner says so; **Update to latest** refuses until you press **Return to tracking**.
  Clicking a commit shows its message and per-file diffs.
- **Re-clone** deletes the checkout and clones the repository again, **with no confirmation step**. Use it after
  changing the repository URL. Anything inside `/data/repo` that is not in the remote, such as a recipe's `work/`
  folder, is lost.
- If fetching has failed for more than 24 hours, a notification goes out once.

**Errors from update, check-out, return-to-tracking and clone are not shown in the page.** Those actions are queued
jobs; when one fails, the page just stays as it was. The reason is in the job's summary and in the server log
(`docker compose logs cockpit`, a line such as `git_update failed: …`). To see the message directly, run the
command-line equivalent, `docker compose exec cockpit cockpit git update`, which prints it. (A failed **Fetch now** is
different: its error is shown as `Fetch error` on the Code page.)

## Permissions

The **Permissions** page shows the PrivacyFence rules each recipe needs, as reported by `privacyfence-sync
permissions --json`, per recipe, as one union, or per group. **Refresh** collects them (it runs in the background and
needs the recipe environment). Add the rules to the matching token in PrivacyFence instead of widening the token. The
same information is available with `cockpit permissions [--union | --by-group] [--json]`.

## Notifications

On the **Notifications** page, add a webhook with a name, a format and a URL. The URL is a secret: it is stored
encrypted and never shown again. **Send test** posts a test notification and shows the HTTP status; the last status
and time of each webhook are listed.

| Event | When |
|---|---|
| Run failed | A **scheduled** run ends as recipe error, failed, denied, not signed in or timed out (once per recipe and outcome per scheduled job). Manual runs never notify. |
| Schedule missed | Scheduled runs were missed while the cockpit was not running. |
| Token expiring | A token's days left first fall to 14, 7 or 1. |
| Token invalid | PrivacyFence refuses a token or it has expired (once per streak). |
| Environment failed | The recipe environment could not be built (once per distinct failure). |
| Git fetch failing | Fetching has failed for more than 24 hours. |

Formats:

- **json**: `POST` with `Content-Type: application/json` and this body (`null` where a field does not apply):

  ```json
  {
    "source": "privacyfence-cockpit",
    "event": "run_failed",
    "message": "imports/10-sf-milestones: denied (exit 3) in schedule nightly",
    "recipe": "imports/10-sf-milestones",
    "schedule": "nightly",
    "outcome": "denied",
    "exit_code": 3,
    "time": "2026-10-20T06:15:00Z",
    "url": "https://cockpit.example.com/runs/42"
  }
  ```

  `event` is one of `run_failed`, `schedule_missed`, `token_expiring`, `token_invalid`, `venv_failed`,
  `git_fetch_failing`, `test`. `url` is `null` unless `COCKPIT_PUBLIC_URL` is set.
- **slack**: `{"text": "PrivacyFence Cockpit: <message> <link|Open>"}` for a Slack incoming webhook.
- **ntfy**: the message as the body, with the headers `Title: PrivacyFence Cockpit`, `Tags` and, when a public URL is
  set, `Click`.

Messages and payloads never contain tokens, log contents or recipe data. Delivery has a 10-second timeout and is not
retried. Notifications raised by a CLI command are delivered before the command exits.

## Settings

The **Settings** page holds owner overrides. Each setting shows its effective value and where it comes from.

| Setting | Default | Precedence |
|---|---|---|
| PrivacyFence server URL | none | `cockpit.yaml` `server`, then this setting, then `COCKPIT_SERVER_URL` |
| Timezone | `UTC` | `cockpit.yaml` `timezone`, then this setting |
| Log retention (days) | 90 | this setting (1 to 3650) |
| Fetch interval (minutes) | 15 | this setting (5 to 1440) |
| Update before each scheduled run | off | this setting |

The effective server is passed to recipes as `PRIVACYFENCE_MCP_URL`. privacyfence-sync ranks that below a recipe's own
`server:`, so a recipe with `server:` talks to that server, while token checks always use the effective server. The run
timeout is `timeout_minutes` in `cockpit.yaml`; it is not editable here.

## The command line

Run `cockpit` inside the container (`docker compose exec cockpit cockpit …`). A `PATH` is a recipe (`imports/10-sf-milestones`,
or with the group number, `10-imports/10-sf-milestones`) or a group (`imports`); a leading `./` or `workflows/` and a
trailing `/recipe.yaml` are ignored. With no `PATH`, every recipe is selected, in run order.

| Command | What it does |
|---|---|
| `cockpit serve` | Runs the dashboard and the scheduler (the container's default command). |
| `cockpit user reset-password` | Clears the password and all sessions; prints a new setup code. |
| `cockpit list` | Lists each group's recipes with the last outcome and time. |
| `cockpit check [PATH…] [--branch NAME]` | Mode `check` for each recipe; prints `check: N recipe(s), K failed`. |
| `cockpit preflight [PATH…] [--attended] [--branch NAME]` | Mode `preflight`. |
| `cockpit run [PATH…] [--attended] [-n] [--stop-on-error] [--repeat N] [--branch NAME]` | Runs recipes now (`-n` / `--dry-run` only prints `would run: <path>`). `--repeat N` runs each recipe up to N times while it ends `ok`. |
| `cockpit permissions [PATH…] [--union \| --by-group] [--json]` | The rules the recipes need. |
| `cockpit test [--branch NAME] [-- ARGS]` | Runs the recipe repository's own tests: `python -m pytest -q ARGS` in the checkout, with the recipe environment and no token. Add `pytest` to `requirements.txt` first (`Add pytest to requirements.txt to use cockpit test.`). |
| `cockpit test --live [PATH…] [--branch NAME]` | Live replay of the read-only steps. |
| `cockpit token set NAME` / `show` / `delete NAME` | Manage stored tokens. |
| `cockpit schedule list` | Lists the schedules with their next run. |
| `cockpit git status` / `git update` | Shows the checkout; fast-forwards it and rebuilds the environment. |

`--branch NAME` runs from another branch of the recipe repository instead of the configured one (see [From the
Overview](#from-the-overview)). The command fetches first and exits 1 with `There is no branch NAME on the remote.` for an
unknown branch; `PATH`s are then matched against that branch's recipes. `-n` / `--dry-run` cannot be combined with
`--branch` (`--dry-run cannot be combined with --branch.`, exit 1).

Commands that run recipes or change the checkout take the **run lock**, shared with the server: while the server (or
another command) runs something, they print `Waiting for the cockpit's current run to finish…` and continue after it. The
server's worker polls for the lock, so stopping the container never hangs behind a CLI run. Runs from the command line
are recorded in History (trigger `manual`) and stream their log lines to the terminal.

**Exit codes.** 0 is success.

| Code | Meaning |
|---|---|
| 1 | A usage, selection, recipe or validation error (including an unknown `PATH`), and `cockpit test` when pytest fails or is missing. |
| 2 | An environment error: master key, configuration, git, the recipe environment, an undecryptable stored secret. |
| 2, 3, 4 | For `run`, `check`, `preflight` and `test --live`: the exit code of the first recipe that did not succeed: recipe error 1, failed 2, denied 3, not signed in 4; stopped and timed out are 2. |
| 130 | Ctrl-C during a run. The run is stopped and recorded as `stopped`. |

`cockpit test` exits 1 when pytest fails (pytest's own code 2 would collide with the environment-error code 2).

## Backups

Everything the cockpit keeps is in the `/data` volume: `cockpit.db` (history, settings, sessions), `secrets/`
(encrypted), `repo/` (the checkout), `branches/` (worktrees for branch runs), `venvs/` and `branch-venvs/` (recipe environments), `logs/` and `home/` (SSH host keys). Back up the
volume **and, separately, the master key**; one without the other cannot restore the secrets. `repo/`, `branches/`, `venvs/` and `branch-venvs/` can
be recreated and may be left out. Stop the container for a consistent copy:

```bash
docker compose stop cockpit
docker volume ls    # find the volume, usually <project>_cockpit-data
docker run --rm -v <volume>:/data:ro -v "$PWD":/backup busybox tar czf /backup/cockpit-data.tar.gz -C /data .
docker compose start cockpit
```

To restore, put the files back into an empty volume and start the container with the same master key.

## Troubleshooting

- **Cockpit does not start: `No master key at …`** — create the key and the secret as in the README. **`The master key
  does not match the one that encrypted /data/secrets.`** — you are using a different key from the one that created
  the volume; use the original, or move `/data/secrets` away to start without the stored secrets. Either way the start
  exits with status 2.
- **`The master key in … must be 32 random bytes, base64-encoded (openssl rand -base64 32).`** — the key file is wrong
  or unreadable by user 10001; check `chown 10001:10001` and `chmod 400`.
- **Banner `The recipe environment could not be built: privacyfence-sync in requirements.txt is too old for the cockpit:
  pin the first version with run --result-file and permissions`** — the cockpit builds an environment from your
  `requirements.txt` and checks that `privacyfence-sync run` accepts `--result-file` and `privacyfence-sync permissions`
  accepts `--json`. Pin a privacyfence-sync version that does. Other messages in that banner: `requirements.txt must pin
  privacyfence-sync (privacyfence-sync==X.Y.Z)`, `the recipe repository has no requirements.txt`, and `pip install
  failed with exit code N; see logs/venv/<hash>.log` (the log is in `/data/logs/venv/`). Until it is fixed, runs keep using the
  previous environment if one was built earlier; if there is none, they end as `failed` with `the recipe environment is
  not ready`. A webhook notification is sent once per failure.
- **Banner `Fetching the recipe repository fails: …`** — look at `Fetch error` on the Code page: a wrong deploy key, a
  host that is not trusted yet, an expired token.
- **Banner `cockpit.yaml: …`** — fix the file; the message names the key. Scheduling resumes at the next tick.
- **A schedule never fires** — check that it is not paused, that the cron is in the effective timezone, and that the
  cockpit was running (see *Missed runs*).
- **A run ends `denied`** — read the run's log for the step, then use `cockpit permissions` or the Permissions page to
  see the rule the recipe needs on the token, and add that rule in PrivacyFence.
- **A run ends `not signed in`** — the token is missing, expired or refused. Check the Tokens page.
- **Exit codes** are in the table above.
- **`database is locked`** — the server and the command line share one SQLite file; a second failure means something
  holds it open. Retry; report it if it persists.
- **Forgot the password** — `cockpit user reset-password`.
