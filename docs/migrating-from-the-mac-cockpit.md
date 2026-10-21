# Migrating from the Mac cockpit

This guide is for retiring the laptop-bound cockpit (a `cockpit` script, a launchd agent, the macOS Keychain and a
SwiftUI app; described in [the architecture reference](reference/cockpit-architecture.md)) in favour of the container.
Your recipes carry over unchanged; the way they are scheduled, authenticated and watched does not.

Do the steps in this order, so that nothing runs twice and no token is left behind.

## 1. Stand up the container first

Follow the [README](../README.md) quick start and the [user guide](user-guide.md) until the Overview lists your
recipes. If the recipes are not yet in their own Git repository, create one with the layout in
[the recipe repository guide](recipe-repository.md) (`workflows/<NN-group>/<NN-recipe>/recipe.yaml`, a
`requirements.txt` that pins privacyfence-sync, work folders git-ignored).

## 2. Retire the launchd agent

While both are active, a recipe may run on the laptop and in the container at once. Unload the agent and delete it:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/<your-agent>.plist
rm ~/Library/LaunchAgents/<your-agent>.plist
```

`<your-agent>` is the file you created for `./cockpit run --notify`. `launchctl list | grep -i cockpit` shows whether
anything is still loaded.

## 3. Replace the Keychain tokens with new scoped tokens

The Mac cockpit held PrivacyFence **AI-client tokens** in the login Keychain, under the service name
`privacyfence-sync`, one item per name (a recipe path, a group, or `default`). The container accepts **PrivacyFence scoped
tokens only** (`pfst_…`). An AI-client token is refused: `Only PrivacyFence scoped tokens (pfst_…) are supported.`

1. Ask your PrivacyFence administrator to enable scoped tokens and issue one scoped token per Keychain item you want to
   keep (or fewer, if you want a coarser split). Give each the rules that item's recipes need.
2. Store each as a **new** token in the container, under the same name the Keychain item had:

   ```bash
   docker compose exec cockpit cockpit token set default
   docker compose exec cockpit cockpit token set imports
   docker compose exec cockpit cockpit token set imports/10-sf-milestones
   ```

   Each command prompts for the value and checks it against the PrivacyFence server. The lookup chain is the same as on
   the Mac: a recipe's token, then its group's, then `default`. `cockpit token show` lists the names and whether each is
   stored.
3. After the first clean scheduled night (step 8), revoke the old AI-client tokens in PrivacyFence and delete the
   Keychain items (`security delete-generic-password -s privacyfence-sync -a <name>`, once per item).

Do not move the old token value into the container: it is not a scoped token, and a scoped token's expiry and
scope are what the daily token check and the Tokens page are built around.

## 4. Replace the hand-kept rule table with the Permissions page

On the Mac, the mapping from each tool a recipe calls to the PrivacyFence rule it needs was kept by hand. Open the
**Permissions** page (or run `cockpit permissions --by-group`), press **Refresh**, and use what it reports for each
token. Then run `cockpit preflight` (or the **preflight** mode on the Overview) before enabling schedules; treat `denied` as
a rule gap in PrivacyFence, not as a recipe bug.

## 5. Move the schedule into `cockpit.yaml`

What the launchd agent's calendar did is now `schedules:` in `cockpit.yaml` at the root of the recipe repository (see
[its reference](recipe-repository.md#cockpityaml)). For example, a nightly run of everything becomes:

```yaml
version: 1
timezone: Europe/Berlin
schedules:
  - name: nightly
    cron: "30 3 * * *"
    run: [imports, transformations, dashboards]
    stop_on_error: true
```

Failures no longer raise a macOS notification: add a webhook on the **Notifications** page (generic JSON, Slack or ntfy).
Unlike the laptop, the container needs no logged-in user, an unlocked Keychain or Full Disk Access.

## 6. What changes

| On the Mac | In the container |
|---|---|
| `./cockpit` script, bootstraps `.venv` | `cockpit` command in the image; the recipe environment is built from `requirements.txt` when you update |
| Keychain items | Encrypted files on the `/data` volume, with a master key from a Docker secret |
| AI-client tokens | PrivacyFence scoped tokens only |
| launchd agent | Schedules in `cockpit.yaml`; missed runs are recorded and notified, never caught up |
| SwiftUI app | The web dashboard. **The SwiftUI app is no longer needed.** |
| `logs/runs.csv`, `logs/<date>/…` | History in the dashboard (with a `runs.csv` download) and logs under `/data/logs` |
| `cockpit run --notify` | Webhook notifications for scheduled failures |
| The recipe folder on disk | A Git checkout that changes only when you update it |

**History does not move.** `logs/runs.csv` and the old logs stay on the laptop; the container starts a fresh history.
Keep them as an archive if you need them.

## 7. The `cockpit sync` shared-block convention

The Mac cockpit's `cockpit sync` (and `cockpit sync --check`) copied shared blocks from `workflows/_shared/<block>/` into
the dashboards and recipes that carry the marker comments. The container's `cockpit` command has no `sync`. Move the logic
into a script in the recipe repository (the Mac cockpit's `sync.py`, for instance) and run it before committing, for
example from a test that fails when a copy is stale, which `cockpit test` then runs (add `pytest` to
`requirements.txt`). `_shared` is skipped by discovery, so it can stay where it is.

## 8. First scheduled run

1. `cockpit list` shows every recipe; `cockpit check` should end `check: N recipe(s), 0 failed`.
2. `cockpit preflight` for each group, then run one recipe by hand from the dashboard.
3. Watch the **Schedules** page for the first scheduled run, and check **History**.

Keep the Mac's recipe folder, logs and Keychain items until one scheduled night has run cleanly, then finish step 3 and
delete what you no longer need.
