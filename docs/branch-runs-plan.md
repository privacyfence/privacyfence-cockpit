# Plan: manual runs from another branch

## Goal

Today every run, manual or scheduled, uses the one recipe checkout at `/data/repo`, which tracks the
configured branch (usually `main` or `master`). The owner wants to try recipes from a work-in-progress
branch without merging it first. After this change, a **manual** run (dashboard **Run selected**, and the
CLI's `run`, `check`, `preflight` and `test`) can name any branch of the recipe repository. The run uses a
separate git worktree of that branch, at its newest fetched commit, with its own recipe environment.
Scheduled runs, the per-recipe **Run** button, **Run now** on the Schedules page and the configured
checkout are not affected: they always use the configured branch. Every mode is allowed on a branch,
with the same token rules as any manual run. The run's history row, page, log and CSV export record the
branch, and branch runs do not count as a recipe's "last run" on the Overview or in `cockpit list`.

Decided with the user before planning: all modes are allowed; dashboard and CLI; a separate worktree whose
git-ignored `work/` folders are separate from the configured checkout's and start empty.

## Current state

- **One checkout.** `DataPaths.repo` is `/data/repo` (`src/privacyfence_cockpit/paths.py:13`). It is cloned
  with `git clone --branch <b> --single-branch` (`src/privacyfence_cockpit/gitrepo.py:154`), so a fetch
  (`gitrepo.py:158-176`, `fetch --prune origin`) only downloads the configured branch. The configured branch
  is the `git.branch` setting (`gitrepo.py:117`). Branch names are checked against
  `_BRANCH = ^[A-Za-z0-9._/-]{1,100}$` (`gitrepo.py:27`).
- **Trust.** The checkout moves only on the owner's request, fast-forward only, never over tracked changes
  ([ADR 0006](adr/0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md)).
  Recipe code is trusted, not sandboxed, because it is reviewed under ADR 0006: it runs as the cockpit's
  user and can read the master key and `/data/secrets`
  ([ADR 0003](adr/0003-secrets-are-encrypted-on-the-volume-with-a-key-from-a-docker-secret-and-only-one-recipe-token-reaches-a-subprocess.md)).
  `Re-clone` deletes `/data/repo` (`src/privacyfence_cockpit/jobs.py:176-183`).
- **One recipe environment at a time.** `VenvManager.ensure()` (`src/privacyfence_cockpit/venvs.py:143-162`)
  hashes `requirements.txt` and local `pyproject.toml`s (not the local packages' source,
  `venvs.py:108-113`) of `paths.repo`, builds `/data/venvs/<digest>`, switches the `venv.current` setting
  and prunes every folder in `/data/venvs` except the current and two previous ones (`venvs.py:257-273`).
  `_build` runs pip with `cwd=self.paths.repo` (`venvs.py:191`); editable local packages (`-e ./lib/x`,
  `venvs.py:59-65`) therefore point into `/data/repo`. A failure sets `venv.last_error` and emits a
  `VENV_FAILED` notification (`venvs.py:243-253`).
- **Jobs.** `Job` (`jobs.py:39-50`) has no branch. `Worker._run_job` (`jobs.py:186-227`) calls
  `venvs.ensure()`, selects recipes from `self._recipes()` (= `discover(paths.repo)`,
  `services.py:114-119`) and calls `Runner.run` per recipe. A selector that matches nothing records one
  `recipe_error` row via `_record_selection_error` (`jobs.py:237-262`).
- **Runner.** `Runner.run` (`src/privacyfence_cockpit/runner.py:86-167`) takes the commit from
  `self._git.head()`, the venv from `self._venvs.current()`, runs `privacyfence-sync` with `cwd=recipe.dir`,
  runs `cockpit_problems(recipe, self._paths.repo, …)` in `check` mode, and writes the log header with the
  recipe path relative to `self._paths.repo` (`runner.py:246-247`).
- **History.** Schema version 2 (`src/privacyfence_cockpit/db.py:11`); the `runs` table has `commit_sha`
  but no branch. `History.start` (`history.py:118-136`), `RunRow` (`history.py:35-52`), `CSV_COLUMNS`
  (`history.py:17-31`), `last_by_recipe` (`history.py:186-195`, used by the Overview and `cockpit list`).
- **Web.** `post_run` (`src/privacyfence_cockpit/web/routes_runs.py:100-112`) submits
  `Job(JobKind.RUN, "manual", selectors, mode)`; it needs at least one ticked recipe, and the ticks list the
  configured checkout's recipes (`web/templates/overview.html:21`). The **Run selected** form
  (`overview.html:41-49`) has a mode select only. `_describe` (`routes_runs.py:45-48`) names a job.
  `run.html:15` shows the commit; `history.html:41` shows the trigger.
- **CLI.** `_run`, `_check`, `_preflight`, `_test` (`src/privacyfence_cockpit/cli.py:375-424`) select recipes
  with `_select` against `cockpit.recipes()` and run them through `_execute` (`cli.py:326-374`), which builds
  a one-off `Worker` and a `Job(JobKind.RUN, "manual", …)`. `_pytest` (`cli.py:425-452`) runs the recipe
  repository's pytest in `paths.repo` with `venvs.ensure()`'s environment. `_command` (`cli.py:76-…`) already
  maps `GitError` to one stderr line and exit 2.
- **Tests** that this plan extends: `tests/test_gitrepo.py`, `tests/test_paths.py`, `tests/test_db.py`,
  `tests/test_services.py`, `tests/test_history.py`, `tests/test_venvs.py`, `tests/test_jobs.py`,
  `tests/test_runner.py`, `tests/test_web_runs.py`, `tests/test_cli_run.py`; git fixtures come from
  `tests/gitutil.py` (`make_remote`, `push_commit`).

## Design

### D1. Paths

`DataPaths` gets two properties:

- `branches` → `root / "branches"`: one worktree per branch, folder name `branch_folder(branch)`.
- `branch_venvs` → `root / "branch-venvs"`: recipe environments built only for branch runs.

`DataPaths.ensure()` creates neither (they are created on first use with mode `0o700`, like `repo`).

`branch_folder(branch: str) -> str` is a module-level function in `paths.py`: `branch.replace("/", "%2F")`.
`%` is not allowed by `_BRANCH`, so the mapping is one-to-one and reversible
(`folder.replace("%2F", "/")`).

### D2. History records the branch

- `db.py`: `SCHEMA_VERSION = 3`; `_SCHEMA_V3 = "ALTER TABLE runs ADD COLUMN branch TEXT;"`;
  `_MIGRATIONS = {1: _SCHEMA_V1, 2: _SCHEMA_V2, 3: _SCHEMA_V3}`. `NULL` means the configured checkout
  (every existing row, every scheduled row).
- `RunRow` gets a last field `branch: str | None = None`; `_row` reads `r["branch"]`.
- `History.start(…, branch: str | None = None)` inserts it.
- `CSV_COLUMNS` gets `"branch"` appended **at the end** (after `note`), and `export_csv` writes `r.branch`
  last, so existing column positions do not move.
- `last_by_recipe` adds `AND branch IS NULL` to its `WHERE`, so a branch experiment never becomes the
  configured recipe's "last run" on the Overview or in `cockpit list`.
- No history filter for branch.

### D3. Git: fetch every branch, list them, make worktrees

In `GitManager` (`gitrepo.py`):

- `fetch()` runs `git -C repo fetch --prune origin +refs/heads/*:refs/remotes/origin/*` instead of
  `fetch --prune origin`. Everything else in `fetch()` is unchanged. The configured checkout is still only
  moved by `update()`. (Verified with git 2.43 on a `--single-branch` clone: it adds the other branches,
  leaves `origin/<configured>` and `new_commits` unchanged, and prunes deleted branches.)
- `configured_branch() -> str | None`: the `git.branch` setting.
- `remote_branches() -> list[str]`: `[]` when not cloned, when `for-each-ref` exits non-zero, or when
  running git raises `GitError`; otherwise
  `git -C repo for-each-ref --format=%(refname:lstrip=3) refs/remotes/origin`, dropping `HEAD` and any name
  that does not match `_BRANCH`, starts with `-`, or has a `/`-separated component starting with `.`;
  sorted. No network access. It never raises.
- `branch_worktree(branch: str) -> tuple[Path, str]` returns `(worktree path, commit sha)`. Steps:
  1. `configured = self._require_branch()`.
  2. If `branch == configured`: `GitError(f"{branch} is the configured branch; run it without choosing a branch.")`.
  3. `remote = self.remote_branches()`; if `branch not in remote`:
     `GitError(f"There is no branch {branch} on the remote.")`.
  4. `sha = self._checked("rev-parse", "--verify", f"refs/remotes/origin/{branch}^{{commit}}").stdout.strip()`.
  5. `path = self._paths.branches / branch_folder(branch)`; if `path.parent != self._paths.branches`, raise
     the step-3 error (defence in depth; step 3's filter already excludes such names).
  6. Under `self._lock()`:
     - `self._prune_worktrees(remote)`.
     - If `(path / ".git").exists()`: if `git -C path status --porcelain --untracked-files=no` prints
       anything, raise
       `GitError(f"The checkout of {branch} has changes to tracked files and the cockpit never overwrites them. Inspect them with: docker exec <container> git -C /data/branches/{branch_folder(branch)} status")`;
       then `git -C path checkout --quiet --detach <sha>` through `_checked_in` (if git refuses, for example
       because untracked files would be overwritten, the error is `_checked_in`'s
       `git checkout failed: <first line>`, which is intended).
     - Else: `self._paths.branches.mkdir(parents=True, exist_ok=True)`, `chmod(0o700)`,
       `shutil.rmtree(path, ignore_errors=True)` (a folder left by a crash), then
       `git -C repo worktree add --detach <path> <sha>` through `_checked`.
  7. Return `(path, sha)`.
  Git-ignored files in an existing worktree (a recipe's `work/`) survive step 6, so they persist between
  runs of the same branch.
- `_prune_worktrees(remote: list[str]) -> None` (called with the git lock held): `git -C repo worktree
  prune`; then for each folder in `self._paths.branches` (if it exists) whose
  `folder.name.replace("%2F", "/")` is not in `remote`: `git -C repo worktree remove --force <folder>`
  (ignore its return code) and `shutil.rmtree(folder, ignore_errors=True)`. This discards anything in the
  worktree of a branch that no longer exists on the remote, tracked changes included.
- A private helper `_checked_in(cwd: Path, *args: str)` runs `git -C <cwd> …` like `_checked` does for the
  repo. All new git calls use fixed argv lists through `self._run`, never a shell.

### D4. Recipe environments for branch runs

In `VenvManager` (`venvs.py`):

- `KEEP_BRANCH_VENVS = 2` (module constant).
- `_build(self, target: Path, digest: str, repo: Path)`: pip's `cwd` becomes `repo`; `ensure()` passes
  `self.paths.repo`. No other change to `ensure()`.
- `_has_local_packages(repo: Path) -> bool` (module function): `True` when `requirement_files(repo)` has
  more than `requirements.txt`, or any line of `requirements.txt` (via `_requirement_lines`) gives a
  non-`None` `_local_path`. Such environments embed paths of, or copies from, the checkout they were built
  in, and the hash does not cover the packages' source.
- `ensure_for(repo: Path) -> VenvResult`, for a branch worktree. It never touches `venv.current`,
  `venv.previous`, `venv.last_error`, `notices_sent`, never emits events, and never deletes anything under
  `paths.venvs`:
  1. `problem = pin_problem(repo)`, `digest = venv_hash(repo, self._python_version())`; a `VenvError`
     returns `VenvResult(None, False, str(exc))`; a non-`None` `problem` returns
     `VenvResult(None, False, problem)`.
  2. `local = _has_local_packages(repo)`. If not `local` and
     `(self.paths.venvs / digest / READY_MARKER).exists()`: return
     `VenvResult(self.paths.venvs / digest, False, None)` (reuse the configured branch's environment when
     the requirements are the same and nothing is installed from the checkout).
  3. `name = f"{repo.name}-{digest}" if local else digest`; `target = self.paths.branch_venvs / name`.
     If `(target / READY_MARKER).exists()`: set the marker's times with
     `os.utime(marker, (t, t))`, `t = self.clock().timestamp()`, and return `VenvResult(target, False, None)`.
     (A branch worktree's `repo.name` is its `branch_folder`, so a branch with local packages always gets an
     environment of its own.)
  4. Otherwise `mkdir(parents=True, exist_ok=True)` + `chmod(0o700)` on `paths.branch_venvs`; remove a
     partial `target`; `error = self._build(target, digest, repo)`; on error `shutil.rmtree(target,
     ignore_errors=True)` and return `VenvResult(None, False, error)`.
  5. Write the marker and set its times as in step 3; then prune: of the folders in `paths.branch_venvs`,
     keep `target` and the `KEEP_BRANCH_VENVS - 1` others whose `READY_MARKER` has the newest mtime;
     `shutil.rmtree` the rest (folders without a marker included). Return `VenvResult(target, True, None)`.

### D5. Branch checkouts, jobs and the runner

New module `src/privacyfence_cockpit/branches.py`:

```python
@dataclass(frozen=True)
class BranchCheckout:
    branch: str
    sha: str
    root: Path
    venv: Path | None
    venv_error: str | None


def prepare_branch(git: GitManager, venvs: VenvManager, branch: str) -> BranchCheckout:
    """Fetch, move the branch's worktree to its newest commit and make sure it has an environment."""
    git.fetch()
    root, sha = git.branch_worktree(branch)
    result = venvs.ensure_for(root)
    return BranchCheckout(branch, sha, root, result.path, result.error)
```

`GitError` from `fetch` or `branch_worktree` propagates. A venv failure does not raise: the runner reports
it like today's missing environment.

`jobs.py`:

- `Job` gets `branch: str | None = None` (after `attended`) and a `__post_init__` that raises
  `ValueError("only manual run jobs can choose a branch")` when `branch is not None` and
  (`kind is not JobKind.RUN` or `trigger.startswith(SCHEDULE_PREFIX)`).
- `Worker._run_job`: when `job.branch is None`, behaviour is exactly today's. Otherwise, instead of the
  auto-update / `venvs.ensure()` / `self._recipes()` lines:
  ```python
  try:
      checkout = await asyncio.to_thread(prepare_branch, self._git, self._venvs, job.branch)
      recipes = discover(checkout.root)
  except GitError as exc:
      return JobSummary(job.id, (self._record_selection_error(job, str(exc), Outcome.FAILED),), None)
  except DiscoveryError as exc:
      return JobSummary(job.id, (self._record_selection_error(job, str(exc)),), None)
  ```
  then `select(recipes, job.selectors)` as today (its `SelectionError` also goes to
  `_record_selection_error`), and every `self._runner.run(…)` call gets `checkout=checkout`
  (`checkout=None` on the configured path).
- `_record_selection_error(self, job, message, outcome: Outcome = Outcome.RECIPE_ERROR)` passes
  `outcome` to `history.finish` and `branch=job.branch` to `history.start`. The scheduled-event branch of
  it is unchanged (branch jobs are never scheduled).
- `_reclone` also removes `paths.branches` and `paths.branch_venvs` (inside the same git lock, before
  `shutil.rmtree(paths.repo, …)`), since worktrees point into `/data/repo/.git`.

`runner.py`: `Runner.run(…, checkout: BranchCheckout | None = None)`. With a checkout:

- `commit = checkout.sha` (instead of `self._git.head()`),
- `venv = checkout.venv` (instead of `self._venvs.current()`), and the "not ready" reason is
  `checkout.venv_error or "no environment has been built"`,
- the repository root for `cockpit_problems` and for the header's relative path is `checkout.root`
  (pass it to `_write_header` as a new `root: Path` parameter; the configured path passes
  `self._paths.repo`),
- `history.start(…, branch=checkout.branch)`,
- the log header gets the line `# branch: <branch>` right after `# commit: …` (only for branch runs).

Settings that come from `cockpit.yaml` (server, timezone, timeout) are the configured checkout's
(`self._effective()`), even on a branch run. A branch's own `cockpit.yaml` is not read. Tokens resolve by
recipe path and group as today.

### D6. Dashboard

- `overview` context gets `configured_branch = cockpit.git.configured_branch()` and
  `branches = [b for b in cockpit.git.remote_branches() if b != configured_branch]`.
- In `overview.html`, inside `#run-selected`, after the Mode label, only when `branches` is non-empty:
  ```html
  <label>Branch
    <select name="branch">
      <option value="">{{ configured_branch }} (configured)</option>
      {% for branch in branches %}<option value="{{ branch }}">{{ branch }}</option>{% endfor %}
    </select>
  </label>
  <label>Also run (on the chosen branch; one recipe or group per line)
    <textarea name="more" rows="2"></textarea>
  </label>
  ```
  The per-recipe **Run** button posts no `branch` and stays on the configured checkout. The `more` field is
  how a recipe that exists only on the branch is run from the dashboard.
- `post_run`:
  - `branch = str(form.get("branch") or "").strip()`. Empty or equal to `cockpit.git.configured_branch()`
    → `None`.
  - `more = tuple(line.strip() for line in str(form.get("more") or "").splitlines() if line.strip())`.
    If `more` and `branch is None`: `redirect("/", flash="Also run needs a branch; tick the recipes instead.")`.
  - `selected = ticked + more`; the existing "Pick at least one recipe." check runs on that.
  - If `branch` is not `None` and not in `cockpit.git.remote_branches()`:
    `redirect("/", flash="Pick a branch from the list.")`; else submit `Job(JobKind.RUN, "manual",
    selectors=selected, mode=mode, branch=branch)`. Selectors that match nothing on the branch are recorded
    by the worker as a `recipe error` row, as today.
- `_describe(job)` for a run job with a branch:
  `f"{', '.join(job.selectors) or 'all recipes'} ({job.mode.value}, {job.trigger}, branch {job.branch})"`.
- `run.html`: after the Commit row, `{% if run.branch %}<tr><th>Branch</th><td class="mono">{{ run.branch }}</td></tr>{% endif %}`.
- `history.html`: the Trigger cell becomes
  `{{ r.trigger }}{% if r.branch %} <span class="mono">({{ r.branch }})</span>{% endif %}`.
- The branch list comes from the last fetch; a branch pushed since then appears after the next fetch
  (automatic, or **Fetch now** on the Code page). When the configured checkout has no recipes, the Overview
  shows no form at all (unchanged); branch runs then need the CLI.

### D7. Command line

- `--branch NAME` (metavar `NAME`, help `run from this branch of the recipe repository instead of the
  configured one`) on `run`, `check`, `preflight` and `test`.
- `run -n/--dry-run` together with `--branch`: checked **first**, before any selection or fetch: print
  `--dry-run cannot be combined with --branch.` to stderr, exit 1.
- A helper `_branch(cockpit, name: str | None) -> tuple[int, str | None]` returning `(exit code, branch)`:
  `None` or the configured branch → `(0, None)`. Otherwise `cockpit.git.fetch()` (a `GitError` propagates
  to `_command`, which prints it and exits 2); if `name not in cockpit.git.remote_branches()` → print
  `There is no branch {name} on the remote.` to stderr, return `(1, None)`; else `(0, name)`. The worker's
  `prepare_branch` fetches again; that second fetch is accepted (it keeps the worker self-contained).
- `_execute` takes `selectors: tuple[str, ...]` instead of `chosen`, gets `branch: str | None = None`
  (passed into the `Job`) and returns `(exit code, failed, runs)` where `runs = len(summary.run_ids)`.
  Without `--branch`, callers still validate with `_select` first and pass
  `tuple(r.path for r in chosen)`; with `--branch` they skip `_select` and pass the raw `PATH`s (the
  worker selects among the branch's recipes, and an unknown `PATH` is recorded as a `recipe error` row,
  exit 1). `_check` prints `check: {runs} recipe(s), {failed} failed`.
- `test --branch NAME` (pytest): `_pytest` gets `branch: str | None`. With a branch, inside the run lock it
  calls `prepare_branch(cockpit.git, cockpit.venvs, branch)` (a `GitError` propagates to `_command`, exit 2;
  release the lock in `finally` as today), uses `checkout.venv` (`None` → print
  `checkout.venv_error or "The recipe environment is not ready."`, return 2) and `cwd = checkout.root`.
  `test --live --branch NAME` runs `Mode.REPLAY` through `_execute` with the branch.

### D8. Trust

A branch run executes code that has not been through the owner's update under ADR 0006: whatever is on
that branch's newest fetched commit, including `pip install` of its `requirements.txt` and, with
`cockpit test --branch`, its tests. Anyone who can push a branch to the recipe repository can therefore get
code run inside the cockpit, with the same reach as recipe code (ADR 0003: the master key and
`/data/secrets`), but only when the owner starts a run on that branch. The owner's choice of branch is the
review. Nothing runs from a branch on a schedule or automatically. The run page and log name the branch
and the exact commit. ADR 0009 records this, and ADR 0003 and ADR 0006 get forward "Amended by" pointers.

### D9. What was rejected

- **Switching `/data/repo` to the branch for the run and back**: a crash or a stop between the two
  checkouts leaves schedules on the wrong code, and it breaks ADR 0006's promise that the checkout moves
  only by fast-forward on the owner's request.
- **Sharing each recipe's `work/` with the configured checkout** (symlinks): a branch run could corrupt the
  state that scheduled runs depend on. The user chose separate, initially empty `work/` folders.
- **Only non-writing modes on a branch**: the owner picks the branch deliberately and the token rules are
  the same; the user chose all modes.
- **`git ls-remote` on every Overview render** to list branches: a network call per page view. Fetching all
  heads in the existing fetch loop gives the list for free.
- **Reading the branch's `cockpit.yaml`** for server/timezone/timeout: two configurations at once would be
  confusing, and schedules from a branch are out of scope.
- **Building branch environments in `/data/venvs`**: `VenvManager._switch` prunes that folder and the
  `venv.*` settings describe the configured checkout only.
- **Reusing an environment when the repository installs local packages**: the environment would point
  into, or hold a copy of, another checkout's library code.
- **Extending the image smoke test (`scripts/smoke_image.sh`) with a branch run**: the smoke container has
  no remote and no configured `git.branch` setting (that is set on the Code page), so it would need a new
  bootstrap path just for the test; the git behaviour is covered by unit tests against real local remotes,
  and the real-credentials case by the manual check.

## ADRs

- **ADR 0009** — "Manual runs can use another branch of the recipe repository, in its own worktree and
  environment; schedules always use the configured checkout". Amends ADR 0006 (the fetch downloads every
  branch; branch worktrees are new checkouts made on the owner's request, never committed to; worktrees of
  deleted branches are removed with their contents) and ADR 0003 (branch code runs without an update under
  ADR 0006, the owner's choice of branch being its review: D8). Its Alternatives section is D9. ADR 0003's
  and ADR 0006's Status lines get "Amended by [ADR 0009](…): …" in the form ADR 0004 uses for ADR 0008.

## Manual steps

None before implementation. One after: run a branch from a real, credentialed recipe repository in a
running container (`ma1-real-branch-run`); see the
[step-by-step page](https://claude.ai/artifact/R8hJE3rmaY6sYmE38LN6eW) (`docs/branch-runs-plan-manual-steps.html`).

## Risks and open questions

- **Fetching all heads changes the fetch refspec.** If an existing `tests/test_gitrepo.py` fetch test fails
  after the change, the worker of p2 stops with `status=blocked` instead of changing that test.
- **`git worktree` behaviour.** If the container's git is older than 2.17 (no `worktree remove`), p2 stops
  with `status=blocked` and reports `git --version`.
- **Venv tests use a fake `run`** (`tests/test_venvs.py:26` `FakeRun`). p3 extends `FakeRun` additively only.
- **Coverage gate is 95% branch coverage** over the package; each phase adds tests for every new branch in
  its code.
- **The CLI's `_execute` signature changes** (D7); every caller is in `cli.py`. If a test outside
  `tests/test_cli_run.py` calls `_execute` directly, p6 stops with `status=blocked`.

## Implementation manifest

```yaml
plan_slug: branch-runs
feature_branch: feature/branch-runs
max_parallel: 2
manual_steps_artifact: https://claude.ai/artifact/R8hJE3rmaY6sYmE38LN6eW
manual_steps_source: docs/branch-runs-plan-manual-steps.html
manual_before: []
manual_after:
  - id: ma1-real-branch-run
    title: Run a recipe from a non-configured branch of the real recipe repository in a running cockpit
    why: CI only uses local file:// remotes; this checks the all-branches fetch, the worktree and the branch environment with the real SSH or HTTPS credentials and a real privacyfence-sync.
verify_after_merge:
  - python3 -m pytest -q tests/test_paths.py tests/test_db.py tests/test_services.py tests/test_history.py tests/test_gitrepo.py tests/test_venvs.py tests/test_jobs.py tests/test_runner.py tests/test_web_runs.py tests/test_cli_run.py
  - python3 -m mypy
  - bandit -c pyproject.toml -r src
final_checks:
  - docs/branch-runs-plan.md and docs/branch-runs-plan-manual-steps.html are deleted and `grep -rn "branch-runs-plan" --include=*.md .` finds nothing
  - docs/adr/0009-manual-runs-can-use-another-branch-in-its-own-worktree-schedules-always-use-the-configured-checkout.md exists, its Status is Accepted, and it is in the docs/adr/README.md index
  - ADR 0003's and ADR 0006's Status lines say "Amended by [ADR 0009]"
  - CHANGELOG.md has the branch-runs entry under `## [Unreleased]` and no new version heading
phases:
  - id: p1-paths-and-history
    title: Data paths for branch worktrees and environments, and a branch column in the run history
    depends_on: []
    complexity: S
    touches:
      - src/privacyfence_cockpit/paths.py
      - src/privacyfence_cockpit/db.py
      - src/privacyfence_cockpit/history.py
      - tests/test_paths.py
      - tests/test_db.py
      - tests/test_services.py
      - tests/test_history.py
    brief: |
      Read docs/branch-runs-plan.md, sections Design D1 and D2, first. Must-read docs: CLAUDE.md,
      CONTRIBUTING.md, docs/coding-guidelines.md, docs/adr/README.md, docs/reference/cockpit-architecture.md.
      1. src/privacyfence_cockpit/paths.py: add the module function `branch_folder(branch: str) -> str`
         (`branch.replace("/", "%2F")`) and the properties `branches` (root / "branches") and
         `branch_venvs` (root / "branch-venvs") to DataPaths. Do not add them to `ensure()`.
      2. src/privacyfence_cockpit/db.py: SCHEMA_VERSION = 3, `_SCHEMA_V3 = "ALTER TABLE runs ADD COLUMN branch TEXT;"`,
         add `3: _SCHEMA_V3` to `_MIGRATIONS`.
      3. src/privacyfence_cockpit/history.py: `RunRow.branch: str | None = None` as the last field; `_row`
         reads `r["branch"]`; `History.start(..., branch: str | None = None)` inserts it; append "branch" to
         the end of CSV_COLUMNS and write `r.branch` as the last CSV value; `last_by_recipe` adds
         `AND branch IS NULL` to its WHERE clause.
      4. Tests:
         - tests/test_paths.py: `test_branch_paths_and_folder_names` (the two properties;
           `branch_folder("feature/x/y") == "feature%2Fx%2Fy"`; `branch_folder("main") == "main"`).
         - tests/test_db.py: rename `test_migrate_creates_schema_version_2` to
           `test_migrate_creates_schema_version_3` and expect 3 in it, in `test_migrate_twice_is_a_noop` and in
           `test_a_version_1_database_is_upgraded_keeping_its_data`; add
           `test_a_version_2_database_gets_the_branch_column` (build v1+v2 by hand with user_version 2, insert a
           runs row, migrate: the row's branch is NULL and a new row can set it).
         - tests/test_services.py: in `test_open_creates_layout_canary_and_schema`, expect `user_version == 3`.
         - tests/test_history.py: in `test_export_csv`, the expected header ends with `,note,branch` and the
           expected `lines[2]` ends with `"n, 1",` (empty branch). Add `test_start_records_the_branch`
           (default None; an explicit value round-trips through `get`) and
           `test_last_by_recipe_ignores_branch_runs` (a later finished branch run of the same recipe does not
           replace the configured run).
      5. Run `ruff check .`, `python3 -m mypy`, `bandit -c pyproject.toml -r src`, `python3 -m pytest -q`.
      Stop with status=blocked if any test other than the ones named above asserts schema version 2, the
      CSV header or `last_by_recipe` results and would need its expectation changed.
    acceptance:
      - python3 -m pytest -q tests/test_paths.py tests/test_db.py tests/test_services.py tests/test_history.py passes
      - python3 -m pytest -q tests/test_db.py::test_migrate_creates_schema_version_3 tests/test_db.py::test_a_version_2_database_gets_the_branch_column passes
      - python3 -m pytest -q tests/test_history.py::test_start_records_the_branch tests/test_history.py::test_last_by_recipe_ignores_branch_runs tests/test_history.py::test_export_csv passes
      - grep -n "ALTER TABLE runs ADD COLUMN branch TEXT" src/privacyfence_cockpit/db.py matches
      - ruff check ., python3 -m mypy and bandit -c pyproject.toml -r src pass

  - id: p2-git-branches
    title: Fetch every branch, list remote branches, and keep one worktree per branch
    depends_on: [p1-paths-and-history]
    complexity: M
    touches:
      - src/privacyfence_cockpit/gitrepo.py
      - tests/test_gitrepo.py
      - tests/gitutil.py
    brief: |
      Read docs/branch-runs-plan.md, Design D3 (and D1 for `branch_folder` / `DataPaths.branches`), and
      docs/adr/0006-*.md. Must-read docs: CLAUDE.md, CONTRIBUTING.md, docs/coding-guidelines.md,
      docs/adr/README.md, docs/reference/cockpit-architecture.md.
      1. tests/gitutil.py: add `push_branch(remote: Path, files: dict[str, str | bytes], branch: str, base: str = "main") -> str`
         that clones `base`, creates `branch` (`git checkout -b`), writes the files, commits "branch commit",
         pushes `branch` and returns the new sha (copy the shape of `push_commit`; to add a commit to an
         existing branch later, tests use `push_commit(remote, files, msg, branch=...)`).
      2. src/privacyfence_cockpit/gitrepo.py, exactly as D3: change the fetch argv to
         `("fetch", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")`; add `configured_branch()`,
         `remote_branches()`, `branch_worktree(branch)`, `_prune_worktrees(remote)` and `_checked_in(cwd, *args)`.
         Use the exact GitError texts from D3. Import `branch_folder` from paths.py, and `shutil`.
      3. Tests in tests/test_gitrepo.py, using the existing `cloned` fixture and `push_branch`:
         - `test_fetch_downloads_every_branch` (after push_branch + fetch, remote_branches() == ["feature/x", "main"]).
         - `test_remote_branches_is_empty_before_the_clone`.
         - `test_remote_branches_is_empty_when_git_fails` (monkeypatch the manager's `_run` to raise GitError,
           and separately to return a CompletedProcess with returncode 1).
         - `test_remote_branches_drops_unsafe_names` (monkeypatch `_run` to return
           "HEAD\nmain\n-x\na/.hidden\nok/name\n": result ["main", "ok/name"]).
         - `test_configured_branch`.
         - `test_branch_worktree_checks_out_the_branch_tip` (returns data/branches/feature%2Fx and the pushed
           sha; the file from the branch exists there; `/data/repo` HEAD is unchanged).
         - `test_branch_worktree_moves_to_a_newer_commit_and_keeps_ignored_files` (the branch has a
           .gitignore with `work/`; create work/state in the worktree; push another commit to the branch;
           fetch; call again: new sha checked out, work/state still there).
         - `test_branch_worktree_replaces_a_leftover_folder` (create data/branches/feature%2Fx with a stray
           file and no .git before the first call: the call succeeds).
         - `test_branch_worktree_refuses_the_configured_branch` and `test_branch_worktree_refuses_an_unknown_branch`
           (exact messages from D3).
         - `test_branch_worktree_refuses_tracked_changes` (modify a tracked file in the worktree; GitError
           naming `/data/branches/feature%2Fx`).
         - `test_worktrees_of_deleted_branches_are_removed` (`git(remote, "branch", "-D", "feature/x")` on the
           bare remote, push_branch another one, fetch, call branch_worktree for the other one: the deleted
           branch's folder is gone).
         - Keep every existing test unchanged and passing.
      4. Run `ruff check .`, `python3 -m mypy`, `bandit -c pyproject.toml -r src`, `python3 -m pytest -q`.
      Stop with status=blocked if an existing tests/test_gitrepo.py test fails after the refspec change, or
      if `git worktree remove` is not available (`git --version` below 2.17); report the output.
    acceptance:
      - python3 -m pytest -q tests/test_gitrepo.py passes
      - python3 -m pytest -q tests/test_gitrepo.py -k "branch or worktree" runs at least 11 tests and all pass
      - grep -n '+refs/heads/\*:refs/remotes/origin/\*' src/privacyfence_cockpit/gitrepo.py matches
      - python3 -m pytest -q tests/test_gitrepo.py --cov=privacyfence_cockpit.gitrepo --cov-branch --cov-report=term-missing reports at least 95% for gitrepo.py
      - ruff check ., python3 -m mypy and bandit -c pyproject.toml -r src pass

  - id: p3-branch-venvs
    title: Recipe environments for branch worktrees that never disturb the configured one
    depends_on: [p1-paths-and-history]
    complexity: S
    touches:
      - src/privacyfence_cockpit/venvs.py
      - tests/test_venvs.py
    brief: |
      Read docs/branch-runs-plan.md, Design D4 (and D1 for `DataPaths.branch_venvs`). Must-read docs:
      CLAUDE.md, CONTRIBUTING.md, docs/coding-guidelines.md, docs/adr/README.md,
      docs/reference/cockpit-architecture.md.
      1. src/privacyfence_cockpit/venvs.py: add `KEEP_BRANCH_VENVS = 2` and `_has_local_packages(repo)`;
         give `_build` the `repo: Path` parameter (pip's cwd) and pass `self.paths.repo` from `ensure()`; add
         `ensure_for(repo: Path) -> VenvResult` exactly as D4 steps 1–5. `import os` for `os.utime`; the marker
         times always come from `self.clock()`.
      2. Tests in tests/test_venvs.py, using the existing `env` fixture and `FakeRun` (extend FakeRun only
         additively), with branch repos as folders under tmp_path named like worktrees (e.g. `feature%2Fx`),
         each with its own requirements.txt. Construct the VenvManager for the pruning test with an injected
         clock that you advance by one minute between builds:
         - `test_ensure_for_reuses_the_configured_environment_when_requirements_match` (ensure() first, then
           ensure_for on a copy with the same requirements returns paths.venvs/<digest>, built False, nothing
           in paths.branch_venvs).
         - `test_ensure_for_with_local_packages_builds_its_own_environment` (requirements with `-e ./lib/x`
           and a lib/x/pyproject.toml, same files in both repos: ensure_for builds
           `branch_venvs/feature%2Fx-<digest>` and pip ran with cwd = the branch repo).
         - `test_ensure_for_builds_in_branch_venvs_and_leaves_the_current_setting` (different requirements:
           built True, path under paths.branch_venvs, `venv.current` and `venv.previous` unchanged).
         - `test_ensure_for_reuses_a_ready_branch_environment`.
         - `test_ensure_for_keeps_two_branch_environments` (three different requirements in turn with the
           clock advanced: two folders remain, the first is gone).
         - `test_ensure_for_failure_is_returned_not_notified` (pip fails: path None, error text, no event in the
           sink, `venv.last_error` unset, the partial target removed; also a repo without requirements.txt and
           one without the pin return the error).
      3. Run `ruff check .`, `python3 -m mypy`, `bandit -c pyproject.toml -r src`, `python3 -m pytest -q`.
      Stop with status=blocked if an existing tests/test_venvs.py test needs a changed expectation.
    acceptance:
      - python3 -m pytest -q tests/test_venvs.py passes
      - python3 -m pytest -q tests/test_venvs.py -k ensure_for runs 6 tests and all pass
      - python3 -m pytest -q tests/test_venvs.py --cov=privacyfence_cockpit.venvs --cov-branch --cov-report=term-missing reports at least 95% for venvs.py
      - ruff check ., python3 -m mypy and bandit -c pyproject.toml -r src pass

  - id: p4-branch-jobs
    title: Branch checkouts in the job worker and the runner
    depends_on: [p2-git-branches, p3-branch-venvs]
    complexity: M
    touches:
      - src/privacyfence_cockpit/branches.py
      - src/privacyfence_cockpit/jobs.py
      - src/privacyfence_cockpit/runner.py
      - tests/test_branches.py
      - tests/test_jobs.py
      - tests/test_runner.py
    brief: |
      Read docs/branch-runs-plan.md, Design D5 (and D2–D4 for what it calls). Must-read docs: CLAUDE.md,
      CONTRIBUTING.md, docs/coding-guidelines.md, docs/adr/README.md, docs/reference/cockpit-architecture.md.
      1. Create src/privacyfence_cockpit/branches.py with `BranchCheckout` and `prepare_branch` exactly as
         in D5 (module docstring: one line saying what it is, like the other modules).
      2. src/privacyfence_cockpit/runner.py: add the `checkout: BranchCheckout | None = None` keyword to
         `Runner.run` and implement the five points of D5 (commit, venv and its reason, root for
         cockpit_problems and the header's relative path via a new `root: Path` parameter of `_write_header`,
         `branch=` on history.start, the `# branch: <branch>` header line after `# commit:`).
      3. src/privacyfence_cockpit/jobs.py: `Job.branch` + `__post_init__` check; the branch path in
         `_run_job` exactly as D5; `_record_selection_error(..., outcome=Outcome.RECIPE_ERROR)` passing
         `branch=job.branch`; `_reclone` also removes `paths.branches` and `paths.branch_venvs`.
      4. Tests:
         - tests/test_jobs.py: add `def ensure_for(self, repo): return VenvResult(self.current(), False, None)`
           to `CountingVenvs` (the FILES remote has no requirements.txt, so the real ensure_for would fail).
         - tests/test_branches.py (new): `test_prepare_branch_returns_the_worktree_sha_and_environment` and
           `test_prepare_branch_propagates_a_fetch_error`, with a real GitManager over a `make_remote` +
           `push_branch` remote and a VenvManager subclass whose `ensure_for` returns a fixed VenvResult.
         - tests/test_runner.py: `test_branch_run_uses_the_checkout_commit_venv_and_root` (header has
           `# commit: <checkout sha>` and `# branch: feature/x`, the history row's branch is `feature/x`,
           commit_sha is the checkout's) and `test_branch_run_without_an_environment_reports_its_error`
           (venv None, venv_error "boom": log line `# the recipe environment is not ready: boom`, outcome failed).
         - tests/test_jobs.py: `test_job_rejects_a_branch_for_scheduled_or_git_jobs` (ValueError);
           `test_branch_job_runs_the_branch_recipes` (a recipe that exists only on the branch runs; rows carry
           the branch and the branch sha; `/data/repo` HEAD and `venv.current` unchanged);
           `test_branch_job_with_an_unknown_branch_records_a_failed_row` (outcome failed, note
           `There is no branch nope on the remote.`, branch `nope`);
           `test_branch_job_with_an_unknown_selector_records_a_recipe_error_with_the_branch`;
           `test_branch_job_with_clashing_groups_records_a_recipe_error` (the branch has
           workflows/10-imports and workflows/20-imports, so discovery raises DiscoveryError);
           `test_reclone_removes_branch_worktrees_and_environments`.
      5. Run `ruff check .`, `python3 -m mypy`, `bandit -c pyproject.toml -r src`, `python3 -m pytest -q`.
      Stop with status=blocked if a scheduled-job test in tests/test_jobs.py or tests/test_scheduler.py
      changes behaviour, or if importing branches.py from runner.py creates an import cycle that cannot be
      solved with a `TYPE_CHECKING` import.
    acceptance:
      - python3 -m pytest -q tests/test_branches.py tests/test_jobs.py tests/test_runner.py tests/test_scheduler.py passes
      - python3 -m pytest -q tests/test_jobs.py -k "branch or reclone" runs at least 6 tests and all pass
      - python3 -m pytest -q tests/test_runner.py::test_branch_run_uses_the_checkout_commit_venv_and_root passes
      - python3 -m pytest -q tests/test_branches.py tests/test_jobs.py tests/test_runner.py --cov=privacyfence_cockpit.branches --cov=privacyfence_cockpit.jobs --cov=privacyfence_cockpit.runner --cov-branch --cov-report=term-missing reports at least 95% for each of the three modules
      - ruff check ., python3 -m mypy and bandit -c pyproject.toml -r src pass

  - id: p5-dashboard
    title: Branch picker and "Also run" on the Overview's Run selected, and the branch on run and history pages
    depends_on: [p4-branch-jobs]
    complexity: S
    touches:
      - src/privacyfence_cockpit/web/routes_runs.py
      - src/privacyfence_cockpit/web/templates/overview.html
      - src/privacyfence_cockpit/web/templates/run.html
      - src/privacyfence_cockpit/web/templates/history.html
      - tests/test_web_runs.py
    brief: |
      Read docs/branch-runs-plan.md, Design D6. Must-read docs: CLAUDE.md, CONTRIBUTING.md,
      docs/coding-guidelines.md, docs/adr/README.md, docs/reference/cockpit-architecture.md.
      1. src/privacyfence_cockpit/web/routes_runs.py: `overview` context gets `configured_branch` and
         `branches`; `post_run` reads `branch` and `more`, validates and passes them on; `_describe` names the
         branch — all with the exact strings and order in D6.
      2. Templates: overview.html (the Branch select and the "Also run" textarea inside #run-selected, only
         when `branches`), run.html (Branch row after Commit), history.html (branch after the trigger) — exact
         markup in D6.
      3. Tests in tests/test_web_runs.py (monkeypatch `cockpit.git.remote_branches` and
         `cockpit.git.configured_branch` on the existing `cockpit` fixture; no git needed):
         - `test_overview_offers_the_other_branches` (select present, first option `main (configured)`,
           `feature/x` offered, `main` not offered twice, textarea `more` present) and
           `test_overview_without_other_branches_has_no_branch_select`.
         - `test_post_run_with_a_branch_submits_a_branch_job` (queued job's branch is `feature/x`).
         - `test_post_run_with_a_branch_and_more_runs_the_extra_selectors` (no tick, `more` = "imports/new\n\nexports":
           job selectors ("imports/new", "exports")).
         - `test_post_run_more_without_a_branch_flashes` (`Also run needs a branch; tick the recipes instead.`).
         - `test_post_run_with_the_configured_or_empty_branch_runs_the_checkout` (branch None for "" and "main").
         - `test_post_run_with_an_unknown_branch_flashes` (`Pick a branch from the list.`, nothing queued).
         - `test_run_and_history_pages_show_the_branch` (insert a row with History.start(branch="feature/x")).
         - `test_describe_names_the_branch` (overview's running text includes `branch feature/x`).
      4. Run `ruff check .`, `python3 -m mypy`, `bandit -c pyproject.toml -r src`, `python3 -m pytest -q`.
      Stop with status=blocked if a CSRF or auth test in tests/test_csrf_routes.py fails because of the new form fields.
    acceptance:
      - python3 -m pytest -q tests/test_web_runs.py tests/test_csrf_routes.py passes
      - python3 -m pytest -q tests/test_web_runs.py -k "branch or more" runs at least 8 tests and all pass
      - grep -n 'name="branch"' src/privacyfence_cockpit/web/templates/overview.html matches
      - grep -n 'name="more"' src/privacyfence_cockpit/web/templates/overview.html matches
      - ruff check ., python3 -m mypy and bandit -c pyproject.toml -r src pass

  - id: p6-cli
    title: --branch on cockpit run, check, preflight and test
    depends_on: [p4-branch-jobs]
    complexity: M
    touches:
      - src/privacyfence_cockpit/cli.py
      - tests/test_cli_run.py
    brief: |
      Read docs/branch-runs-plan.md, Design D7. Must-read docs: CLAUDE.md, CONTRIBUTING.md,
      docs/coding-guidelines.md, docs/adr/README.md, docs/reference/cockpit-architecture.md.
      1. src/privacyfence_cockpit/cli.py: add `--branch NAME` to the run, check, preflight and test parsers
         (help text from D7); add `_branch(cockpit, name)`; change `_execute` to take `selectors` and `branch`
         and return `(code, failed, runs)`; update `_run` (dry-run + branch check first), `_check`,
         `_preflight`, `_test` and `_pytest` as D7 says, with its exact messages. Without `--branch` every
         command's output and exit code stay as today.
      2. Tests in tests/test_cli_run.py, using the existing `env`/`cloned` fixtures plus `push_branch` from
         tests/gitutil.py on the fixture's remote, and the fake venv helpers already in the file (for branch
         runs, make `VenvManager.ensure_for` return the fake venv with monkeypatch, like
         `build_venv_on_ensure` does for `ensure`):
         - `test_run_on_a_branch_runs_a_recipe_only_the_branch_has` (exit 0, the history row has the branch).
         - `test_check_on_a_branch_prints_the_summary` (`check: 1 recipe(s), 0 failed`).
         - `test_unknown_branch_exits_1` (stderr `There is no branch nope on the remote.`, no history row).
         - `test_configured_branch_runs_the_checkout` (`--branch main` behaves like no flag; row branch None).
         - `test_dry_run_with_a_branch_exits_1` (no fetch happened: monkeypatch `GitManager.fetch` to fail the test if called).
         - `test_test_on_a_branch_runs_pytest_in_the_worktree` (the recorded cwd is under data/branches).
         - `test_test_on_a_branch_without_an_environment_exits_2` (ensure_for returns path None, error "boom": stderr `boom`).
         - `test_test_live_on_a_branch_replays` (mode replay, row branch set).
         - `test_branch_fetch_failure_exits_2` (monkeypatch `GitManager.fetch` to raise GitError).
         Every existing test stays unchanged and passing.
      3. Documentation is p7's job; do not edit docs/user-guide.md here.
      4. Run `ruff check .`, `python3 -m mypy`, `bandit -c pyproject.toml -r src`, `python3 -m pytest -q`.
      Stop with status=blocked if an existing tests/test_cli_run.py test needs a changed expectation, or a
      test outside tests/test_cli_run.py calls `cli._execute`.
    acceptance:
      - python3 -m pytest -q tests/test_cli_run.py tests/test_cli_info.py passes
      - python3 -m pytest -q tests/test_cli_run.py -k branch runs at least 9 tests and all pass
      - python3 -m pytest -q tests/test_cli_run.py::test_unknown_branch_exits_1 tests/test_cli_run.py::test_configured_branch_runs_the_checkout tests/test_cli_run.py::test_branch_fetch_failure_exits_2 tests/test_cli_run.py::test_dry_run_with_a_branch_exits_1 passes
      - ruff check ., python3 -m mypy and bandit -c pyproject.toml -r src pass

  - id: p7-docs-adr-retire
    title: User guide, ADR 0009, changelog, and retire the plan
    depends_on: [p5-dashboard, p6-cli]
    complexity: S
    touches:
      - docs/user-guide.md
      - docs/adr/0009-manual-runs-can-use-another-branch-in-its-own-worktree-schedules-always-use-the-configured-checkout.md
      - docs/adr/0003-secrets-are-encrypted-on-the-volume-with-a-key-from-a-docker-secret-and-only-one-recipe-token-reaches-a-subprocess.md
      - docs/adr/0006-the-recipe-checkout-changes-only-on-the-owners-request-fast-forward-only-and-is-never-committed-to.md
      - docs/adr/README.md
      - CHANGELOG.md
      - docs/branch-runs-plan.md
      - docs/branch-runs-plan-manual-steps.html
    brief: |
      Read docs/branch-runs-plan.md in full, docs/adr/README.md (template and rules), ADR 0003, ADR 0004,
      ADR 0006 and ADR 0008 (the amend/forward-pointer shape to copy), CONTRIBUTING.md "Decisions, plans and
      ADRs", and docs/reference/cockpit-architecture.md.
      1. docs/adr/0009-manual-runs-can-use-another-branch-in-its-own-worktree-schedules-always-use-the-configured-checkout.md:
         title "ADR 0009: Manual runs can use another branch of the recipe repository, in its own worktree and environment; schedules always use the configured checkout".
         Status "Accepted — <today's date>. Amends [ADR 0003](0003-…md) and [ADR 0006](0006-…md)." Context:
         the owner wants to try a branch without merging it. Decision: D3–D8 in prose (all-branches fetch; one
         worktree per branch under /data/branches, moved to the branch tip at each run, git-ignored files kept,
         removed with their contents when the branch is gone or on Re-clone; environments reused from
         /data/venvs only without local packages, otherwise built in /data/branch-venvs, two kept; every mode
         allowed on a branch, manual runs only; settings from the configured cockpit.yaml; the branch recorded in
         history and left out of "last run"; the trust change of D8). Alternatives considered: the bullets of
         D9. Consequences: branch code runs with recipe-code reach once the owner picks the branch; disk use of
         worktrees and environments; a branch's `work/` starts empty; the branch list is as fresh as the last
         fetch; worktrees of deleted branches lose tracked changes. Related: branches.py, gitrepo.py, venvs.py,
         jobs.py.
      2. ADR 0003 and ADR 0006: append to each Status line, in the form ADR 0004 uses, "Amended by
         [ADR 0009](0009-…md): manual runs can use another branch." Do not change anything else in them.
      3. docs/adr/README.md: add the 0009 row to the Index table, status Accepted.
      4. docs/user-guide.md:
         - "From the Overview": a paragraph after the modes table: the **Branch** select and **Also run** box on
           **Run selected** (only shown when the remote has other branches); every mode works; the run uses
           `/data/branches/<branch>` at the branch's newest commit after a fetch, with its own `work/` folders and
           environment, and the server/timezone/timeout from the configured `cockpit.yaml`; the per-recipe
           **Run**, **Run now** and schedules always use the configured checkout; branch runs do not change a
           recipe's "Last run"; running a branch runs its code with the same access as any recipe, so only run
           branches you have looked at.
         - "History and logs": add `branch` at the end of the runs.csv column list, and say the log header has
           a `# branch:` line for branch runs.
         - "The Code page and the update model": the fetch downloads every branch but only the configured one is
           checked out; a branch worktree with changed tracked files blocks branch runs until it is cleaned with
           `docker exec <container> git -C /data/branches/<folder> checkout -- .`, the branch is deleted, or
           **Re-clone**, which also deletes the branch worktrees and their environments.
         - "The command line": `--branch NAME` on `run`, `check`, `preflight`, `test` and `test --live`; that
           it fetches first, exits 1 for an unknown branch, and cannot be combined with `-n`.
         - "Backups": add `branches/` and `branch-venvs/` to the volume's contents and to the folders that can
           be recreated and left out.
      5. CHANGELOG.md under `## [Unreleased]`, a `### Added` heading with one bullet: manual runs (dashboard
         **Run selected** and `--branch NAME` on the CLI) can use another branch of the recipe repository, in a
         separate worktree with its own environment; schedules keep using the configured branch; the database
         schema is now version 3 (a `branch` column on runs; an existing database is upgraded on start); link ADR 0009.
      6. Delete docs/branch-runs-plan.md and docs/branch-runs-plan-manual-steps.html.
      7. Run `grep -rn "branch-runs-plan" --include=*.md .` (no output expected), `ruff check .`, `python3 -m pytest -q`.
    acceptance:
      - test -f docs/adr/0009-manual-runs-can-use-another-branch-in-its-own-worktree-schedules-always-use-the-configured-checkout.md
      - grep -n "0009" docs/adr/README.md matches a row with "Accepted"
      - grep -n "Amended by \[ADR 0009\]" docs/adr/0003-*.md docs/adr/0006-*.md matches in both files
      - grep -n -- "--branch" docs/user-guide.md matches
      - grep -n "branch-venvs" docs/user-guide.md matches
      - the first heading after `## [Unreleased]` in CHANGELOG.md is `### Added` and its bullet mentions ADR 0009
      - test ! -e docs/branch-runs-plan.md && test ! -e docs/branch-runs-plan-manual-steps.html
      - grep -rn "branch-runs-plan" --include=*.md . prints nothing
```
