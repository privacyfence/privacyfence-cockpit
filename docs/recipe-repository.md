# The recipe repository

The cockpit runs the recipes of one private Git repository, which you connect on the Code page (see the
[user guide](user-guide.md#connecting-the-recipe-repository)). This page describes what the cockpit expects to find
in it. The recipes themselves (`recipe.yaml`) are privacyfence-sync's format; the cockpit does not change them.

## Layout

```
recipes/                          the repository root
├── cockpit.yaml                  optional: schedules, server, timezone, timeout
├── requirements.txt              required: the recipe environment
├── .gitignore                    must list each recipe's work folder
├── lib/                          optional: local packages that requirements.txt installs
│   └── helpers/
│       └── pyproject.toml
└── workflows/
    ├── 10-imports/               a group: <NN-name>
    │   ├── 10-sf-milestones/     a recipe: <NN-name>
    │   │   ├── recipe.yaml
    │   │   ├── scripts/
    │   │   └── work/             git-ignored
    │   └── 20-jira-issues/
    │       └── recipe.yaml
    ├── 20-transformations/
    │   └── 10-derive-tables/
    │       └── recipe.yaml
    └── _shared/                  skipped: starts with "_"
```

### Discovery and order

- Groups are the folders of `workflows/`. A recipe is a sub-folder of a group that contains `recipe.yaml`.
- A folder whose name starts with `_` or `.` is skipped, at both levels (`_shared`, `_drafts`, `.hidden`).
- **Order** is the group folder name, then the recipe folder name, compared as plain strings. Zero-pad the numbers
  (`10-`, `20-`, …) so `20-` sorts before `100-`. This is the order of `cockpit run`, of the Overview and of
  schedules.
- A recipe's canonical **path** is `<group>/<recipe folder>` with the group's number prefix removed:
  `imports/10-sf-milestones`. The path is what token names, history and the dashboard use. A group's name is its folder
  name without a leading `NN-`; two groups with the same name (`10-imports` and `40-imports`) are an error:
  `workflows/10-imports and workflows/40-imports both have the group name imports`.
- No `workflows/` folder means no recipes; the Overview says `No recipes found.`

### Naming recipes and groups on the command line and in `cockpit.yaml`

A selector is a group or a recipe. These all mean the same recipe: `imports/10-sf-milestones`,
`10-imports/10-sf-milestones`, `workflows/10-imports/10-sf-milestones` and `./workflows/imports/10-sf-milestones/recipe.yaml`.
A selector without a `/` (`imports` or `10-imports`) selects the whole group. A selector that matches nothing is
refused: `no recipe or group matches 'imports/10-typo'`. The result is de-duplicated and always in run order,
whatever order the selectors were given in.

## `requirements.txt`

The cockpit builds one Python virtual environment from `requirements.txt` and runs every recipe with it.

- It **must pin privacyfence-sync**: a line such as `privacyfence-sync==X.Y.Z`. Without one the environment is
  refused: `requirements.txt must pin privacyfence-sync (privacyfence-sync==X.Y.Z)`. A local package whose
  `pyproject.toml` is named `privacyfence-sync` also satisfies this.
- **Local packages** may be installed with `-e ./lib/helpers` (or `--editable`, or a bare `./lib/helpers`). A path
  outside the repository is refused.
- The pinned privacyfence-sync must be new enough for the cockpit: after building, the cockpit checks that
  `privacyfence-sync run --help` mentions `--result-file` and `privacyfence-sync permissions --help` mentions `--json`.
  Otherwise: `privacyfence-sync in requirements.txt is too old for the cockpit: pin the first version with run
  --result-file and permissions`.
- Add `pytest` if you want `cockpit test` to run your repository's tests.
- The environment is rebuilt only when `requirements.txt`, a local package's `pyproject.toml` or the container's
  Python version changes. Since the checkout only changes when you update it, that happens right after an update; the
  environment is also checked at the start of every run. The cockpit keeps the current environment and the two before
  it. A build that fails leaves the previous environment in place and raises a notification.

## Work folders

A recipe's `work_dir` (default `work`, relative to the recipe folder, and it must stay inside it) holds downloaded and
derived source data. **It must be git-ignored**, so source data never reaches your repository. `cockpit check` verifies it
and ends the recipe as a recipe error otherwise: `work_dir <path> is not git-ignored; add it to .gitignore so source data
never reaches git`. A `.gitignore` containing `work/` covers the default.

## What a recipe run gets

Each recipe runs as `privacyfence-sync run recipe.yaml --result-file …` in its own folder, with a minimal environment: the
environment's `bin` first on `PATH`, `HOME=/data/home`, `LANG`, `TZ` (the effective timezone), `PRIVACYFENCE_MCP_URL`
(the effective server, if any) and `PRIVACYFENCE_TOKEN` (the one resolved token, if any). The container's other
environment variables, including `COCKPIT_*`, are not passed. The result file tells the cockpit the outcome.

## `cockpit.yaml`

Optional, at the repository root. The file is parsed with `yaml.safe_load`. Every problem is one error that starts with
`cockpit.yaml: ` and is shown as a banner; while it is invalid, no schedule fires.

```yaml
version: 1
server: https://privacyfence.example.com/mcp
timezone: Europe/Berlin
timeout_minutes: 60
schedules:
  - name: nightly
    cron: "30 3 * * *"
    run: [imports, transformations]
    repeat: 1
    stop_on_error: true
  - name: hourly-milestones
    cron: "0 * * * 1-5"
    run: [imports/10-sf-milestones]
```

| Key | Required | Rule |
|---|---|---|
| `version` | yes | The number `1`. |
| `server` | no | Starts with `http://` or `https://`. Overrides the Settings page and `COCKPIT_SERVER_URL`. |
| `timezone` | no | A time zone name such as `Europe/Berlin` or `UTC`. Cron expressions are evaluated in it. Overrides the Settings page. Default `UTC`. |
| `timeout_minutes` | no | A whole number from 1 to 1440, default 60. A run that exceeds it is stopped and recorded as `timed out`. |
| `schedules` | no | A list of schedules (below). |

Only these top-level keys are allowed; an unknown key is an error (`unknown key 'foo' in the top level`).

Each schedule:

| Key | Required | Rule |
|---|---|---|
| `name` | yes | Lower-case letters, digits and `-`, starting with a letter or digit, at most 41 characters; unique. |
| `cron` | yes | A valid five-field cron expression. |
| `run` | yes | A non-empty list of selectors (groups or recipes, see above). |
| `repeat` | no | A whole number from 1 to 100, default 1. Each recipe runs up to this many times in a row, and stops repeating after a run that does not end `ok` (for instance `no change`). |
| `stop_on_error` | no | `true` or `false`, default `false`. After a recipe that did not succeed, the remaining recipes of that scheduled run are skipped. |

A selector in `run` that matches no recipe does not invalidate the file; the scheduled run ends as a recipe error
(`no recipe or group matches '…'`). Scheduled runs are unattended. See the [user guide](user-guide.md#schedules) for how
missed fires, pausing and updates behave.

## Live replay

`cockpit test --live [PATH…]` (and the **replay** mode on the Overview) runs the beginning of a recipe against real data
without writing anything: it is for testing a recipe's read side.

1. The cockpit copies the recipe's steps up to, but not including, the first step that calls a tool outside the
   allowlist, into a temporary `.replay.yaml` next to the recipe, and runs that file. Steps without a `tool` (script
   steps) before that point are kept.
2. The allowlist is: `drive_list_files`, `drive_download_file`, `drive_sheets_get_values`, `salesforce_export_report`,
   `salesforce_run_report`, `jira_export_issues`, `assets_export_objects`, `grist_export_records`, `grist_export_sql`,
   `grist_get_records`.
3. If the very first step is not on the allowlist, nothing runs: the log says `nothing to replay: the first step writes`
   and the run is recorded as `ok`.
4. The recipe's scripts receive `COCKPIT_REPLAY=1` in their environment, so a script that writes can skip its writes
   (or branch some other way) when it runs in a replay. A script step before the cut point is executed, so it must honour
   the flag if it has side effects.
5. The replay file is deleted afterwards, and the run is recorded with the mode `replay`.

Replay uses the recipe's resolved token like any other run, so PrivacyFence still applies its rules to the reads.
