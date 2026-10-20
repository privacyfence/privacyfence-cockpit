# Plan: PrivacyFence Cockpit v1

## Goal

Ship `ghcr.io/privacyfence/privacyfence-cockpit`: one Docker image that runs privacyfence-sync
recipes on its own schedule against an organization-mode PrivacyFence server, with a web dashboard
and a `cockpit` CLI. It replaces the maintainer's laptop-bound cockpit (a launchd agent, the macOS
Keychain and a SwiftUI wrapper around a `cockpit` script, described in
[`docs/reference/cockpit-architecture.md`](reference/cockpit-architecture.md)). v1 keeps that tool's
behaviour wherever this plan does not say otherwise.

For the owner, after this plan:

- `docker compose up` with a master-key secret and a `/data` volume gives a password-protected
  dashboard on `127.0.0.1:8080`.
- The cockpit clones one private recipe repository (deploy key or read-only PAT), shows its commits,
  and updates only when told to (or before scheduled runs, if that setting is on).
- Schedules live in the recipe repository's `cockpit.yaml`; runs are serial, logged, recorded in
  SQLite, and failures go to webhooks (generic JSON, Slack or ntfy).
- Tokens are PrivacyFence scoped tokens only, encrypted on the volume, checked daily for expiry.
- Releases are tag-driven through GitHub Actions to GHCR, multi-arch, with build provenance.

No tracking issue: the maintainer's request is the source. The privacyfence-sync features this
builds on (`run --result-file`, `permissions --json`, rule suggestions in `preflight`) come from
the PrivacyFence plan `docs/sync-scheduling-support-plan.md` on the `plan/sync-scheduling-support`
branch of privacyfence/privacyfence (Design D1–D3). The cockpit carries no workaround for their
absence and is tested against a fake `privacyfence-sync`.

## Current state

This repository is a skeleton (`main` at 314d050):

- `src/privacyfence_cockpit/__init__.py:1-8` (version only) and `py.typed`; `tests/test_package.py`.
- `pyproject.toml:9-15`: Python >= 3.12, **no runtime dependencies**, extras `test` (pytest,
  pytest-cov, pytest-asyncio) and `dev` (ruff, mypy, bandit). mypy strict over `src`; ruff rules
  `E,F,W,I,B,UP,S` with `S101` allowed in tests; pytest `asyncio_mode = "auto"`;
  setuptools_scm with `fallback_version = "0.0.0.dev0"`.
- `.github/workflows/tests.yml`: the definition of done on Python 3.12 and 3.13, coverage gate 95%.
- `.claude/toolkit.yaml`: no `ci` key yet (so nothing is dispatchable).
- `docs/adr/0001-…`: the cockpit is a separate repository and a plain client of privacyfence-sync,
  holding only PrivacyFence scoped tokens. Next free ADR number: **0002**.
- `CHANGELOG.md`: an empty `## [Unreleased]`.

What the cockpit drives (privacyfence/privacyfence at 4daa4f06, `sync/src/privacyfence_sync/`):

- `cli.py:35-58`: `run RECIPE [--server] [--attended] [--token-env]`, `check RECIPE`,
  `preflight RECIPE [--server] [--attended] [--token-env]`, `login`. Exit codes (`cli.py:121-151`):
  1 recipe error, 2 failure or unreachable, 3 denied, 4 not signed in.
- `cli.py:68-69`: server = `--server`, then the recipe's `server:`, then `$PRIVACYFENCE_MCP_URL`,
  then `http://127.0.0.1:8765/mcp`. `cli.py:72-75`: the token is popped from `$PRIVACYFENCE_TOKEN`
  (or `--token-env`); with none, it falls back to a stored OAuth sign-in under `$HOME`.
- `recipe.py:128-155`: `work_dir` defaults to `"work"` and is relative to the recipe folder.
- `transform.py:22-35`: script steps run with `sys.executable`, without any `PRIVACYFENCE_*`
  variable, cwd = work folder.
- `runner.py:174`: attended runs print `[<step id>] waiting for approval: <url>`.
- `client.py:65-110`: the MCP client pattern this plan copies for `privacyfence_status`
  (`streamable_http_client(url, http_client=httpx2.AsyncClient(headers=…))`, `ClientSession`,
  structured content or JSON text, errors from the handshake surface as `mcp.shared.exceptions.MCPError`
  inside a `BaseExceptionGroup`).

Facts checked in this session:

- PyPI today: starlette 1.9.0, jinja2 3.1.6, uvicorn 0.54.0, python-multipart 0.0.32,
  croniter 6.2.4 (no `py.typed`; `types-croniter` 6.2.4.20261006 exists), pyyaml 6.0.3,
  cryptography 50.0.2, mcp 2.3.0 (pulls httpx2 2.15.1), types-PyYAML. Starlette's `TestClient`
  needs `httpx` (0.28.1).
- mcp 2.x renamed FastMCP to `mcp.server.mcpserver.MCPServer`. An in-process MCP test server works
  with no network (see Design §T1).
- `python:3.12-slim` multi-arch index digest on 2026-10-20:
  `sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`.
- htmx 2.0.4 from the npm registry (`https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.4.tgz`,
  sha256 `a34988d1b9f005a458d593aa8d7486537cb878b8ac3b82703db1268983123ecc`);
  `package/dist/htmx.min.js` is 50917 bytes, sha256
  `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447`. unpkg, jsdelivr and cdnjs are
  blocked from the cloud container; the npm registry is not.
- Action pins (tag → commit): `actions/attest-build-provenance` v4.2.2 →
  `4d101475d8b20a2381f78447822ac1eab6504dd8`; `docker/setup-qemu-action` v4.4.0 →
  `99012661954931238ded8c8b007157a8430204e1`; `docker/setup-buildx-action` v4.4.1 →
  `f87e5991a6d7451dcb8d9637bfbc97413f497069`; `docker/build-push-action` v7.4.0 →
  `c3c9e263c25d99ce0380d002d59b67737d91b0dc`; `docker/login-action` v4.6.0 →
  `dbcb813823bdd20940b903addbd779551569679f`. Plus this repo's own `actions/checkout` and
  `actions/setup-python` pins from `tests.yml`.
- GitHub's SSH host keys (their SHA256 fingerprints `+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`,
  `p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM`, `uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s`
  were verified against the keys in Design §G2).
- The repository is private; the image is to be public (manual step).

## Design

Everything below is decided. Phases point at these sections by number.

### A. Package layout

All under `src/privacyfence_cockpit/`:

| Module | Phase | Holds |
|---|---|---|
| `models.py` | p1 | `Outcome`, `Mode`, `utc_now`, `iso`, `parse_iso` |
| `events.py` | p1 | `Event`, `EventKind`, `EventSink`, `NullSink`, `ListSink` |
| `config.py` | p1 | `EnvConfig` (environment variables) |
| `paths.py` | p1 | `DataPaths` (the `/data` layout) |
| `db.py` | p1 | `Database` (SQLite, schema, settings) |
| `recipes.py` | p2 | discovery, `Recipe`, `select` |
| `cockpit_yaml.py` | p2 | `CockpitConfig`, `Schedule`, `load_cockpit_yaml` |
| `effective.py` | p2 | `Effective`, `effective` (merged settings) |
| `crypto.py` | p3 | master key, AES-GCM |
| `secrets_store.py` | p3 | `SecretStore` |
| `tokens.py` | p3 | token names, lookup chain |
| `gitrepo.py` | p4 | `GitManager` |
| `gitauth.py` | p5 | deploy key, PAT, known hosts, credentials |
| `venvs.py` | p6 | `VenvManager` |
| `history.py` | p7, p8 | run rows, queries, CSV, retention |
| `runner.py` | p7, p8 | `Runner` |
| `replay.py`, `checks.py`, `permissions.py` | p8 | live replay, cockpit-side checks, permissions merge |
| `runlock.py`, `jobs.py`, `scheduler.py` | p9 | file lock, queue and worker, scheduler |
| `notify.py` | p10 | webhooks, `Notifier` |
| `token_check.py` | p11 | `privacyfence_status` through MCP |
| `services.py` | p12 | `Cockpit`: wiring and background loops |
| `web/` | p13–p18 | app shell (p13), auth (p14), pages (p15–p18), templates, static |
| `cli.py` | p13, p14, p19 | the `cockpit` command |

Conventions for every phase: Python 3.12 syntax (CI also runs 3.13); mypy strict; `pathlib`;
subprocesses with a fixed argv, never `shell=True`, each with `# nosec B603` and a reason in the
style of privacyfence-sync (`transform.py:28`); `import subprocess  # nosec B404  # <reason>`.
No log line, page, notification or exception message carries a token, PAT, deploy key, webhook
URL, password, setup code (except the one deliberate setup-code log line, §W1) or master key.
`# pragma: no cover` is allowed only where a section below says so.

### B. Dependencies (`pyproject.toml`, p1)

```toml
dependencies = [
  "starlette>=1.9,<2",
  "jinja2>=3.1.6,<4",
  "uvicorn>=0.54,<1",
  "python-multipart>=0.0.32",
  "croniter>=6.2,<7",
  "pyyaml>=6.0.3,<7",
  "cryptography>=50",
  "mcp>=2.3,<3",
  "httpx2>=2.15",
]

[project.optional-dependencies]
test = ["pytest", "pytest-cov", "pytest-asyncio", "httpx"]
dev = ["ruff", "mypy", "bandit", "types-PyYAML", "types-croniter"]
```

p13 adds `[project.scripts] cockpit = "privacyfence_cockpit.cli:main"` and package data
`privacyfence_cockpit = ["py.typed", "web/templates/*.html", "web/static/*"]`.

### C. Core types (p1)

`models.py`:

```python
class Outcome(StrEnum):
    OK = "ok"; NO_CHANGE = "no_change"; RECIPE_ERROR = "recipe_error"; FAILED = "failed"
    DENIED = "denied"; NOT_SIGNED_IN = "not_signed_in"; STOPPED = "stopped"
    TIMED_OUT = "timed_out"; MISSED = "missed"

OUTCOME_LABELS: dict[Outcome, str]   # "ok", "no change", "recipe error", "failed", "denied",
                                     # "not signed in", "stopped", "timed out", "missed"
SUCCESS_OUTCOMES = frozenset({Outcome.OK, Outcome.NO_CHANGE})
NOTIFY_OUTCOMES = frozenset({Outcome.RECIPE_ERROR, Outcome.FAILED, Outcome.DENIED,
                             Outcome.NOT_SIGNED_IN, Outcome.TIMED_OUT})
RESULT_FILE_OUTCOMES: dict[str, Outcome]   # "ok","no_change","recipe_error","failed","denied","not_signed_in"
EXIT_CODE_OUTCOMES: dict[int, Outcome]     # 0 OK, 1 RECIPE_ERROR, 2 FAILED, 3 DENIED, 4 NOT_SIGNED_IN

class Mode(StrEnum):
    ATTENDED = "attended"; UNATTENDED = "unattended"; REPLAY = "replay"
    PREFLIGHT = "preflight"; CHECK = "check"

def utc_now() -> datetime            # tz-aware UTC, microseconds dropped
def iso(dt: datetime) -> str         # "2026-10-20T06:15:00Z"
def parse_iso(text: str) -> datetime
```

`events.py`:

```python
class EventKind(StrEnum):
    RUN_FAILED = "run_failed"; SCHEDULE_MISSED = "schedule_missed"
    TOKEN_EXPIRING = "token_expiring"; TOKEN_INVALID = "token_invalid"
    VENV_FAILED = "venv_failed"; GIT_FETCH_FAILING = "git_fetch_failing"; TEST = "test"

@dataclass(frozen=True)
class Event:
    kind: EventKind
    time: datetime
    recipe: str | None = None
    schedule: str | None = None
    outcome: Outcome | None = None
    exit_code: int | None = None
    run_id: int | None = None
    token_name: str | None = None
    days_left: int | None = None
    detail: str | None = None

class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...

class NullSink: ...                     # drops events
class ListSink: events: list[Event]     # records events; used by tests
```

`config.py` — `EnvConfig.from_environ(environ: MutableMapping[str, str]) -> EnvConfig`, frozen:

| Variable | Field | Default |
|---|---|---|
| `COCKPIT_DATA_DIR` | `data_dir: Path` | `/data` |
| `COCKPIT_MASTER_KEY_FILE` | `master_key_file: Path` | `/run/secrets/cockpit_master_key` |
| `COCKPIT_PUBLIC_URL` | `public_url: str \| None` (trailing `/` stripped; empty = None) | None |
| `COCKPIT_SERVER_URL` | `server_url: str \| None` | None |
| `COCKPIT_LISTEN_HOST` | `listen_host: str` | `0.0.0.0` |
| `COCKPIT_LISTEN_PORT` | `listen_port: int` | `8080` |
| `COCKPIT_LOG_LEVEL` | `log_level: str` | `INFO` |
| `PRIVACYFENCE_TOKEN` | `env_token: str \| None` — **popped** from `environ` | None |

`from_environ` raises `ConfigError` (a `ValueError`) with `COCKPIT_LISTEN_PORT must be a number
between 1 and 65535` or `COCKPIT_PUBLIC_URL must start with http:// or https://`. `EnvConfig.secure_cookies`
is `public_url is not None and public_url.startswith("https://")`. `__repr__` of `EnvConfig` shows
`env_token` as `'<set>'` or `None` (dataclass `field(repr=False)` plus a custom `__repr__`).

`paths.py` — `DataPaths(root: Path)`, frozen, with properties `repo = root/"repo"`,
`venvs = root/"venvs"`, `logs = root/"logs"`, `db_file = root/"cockpit.db"`,
`secrets = root/"secrets"`, `home = root/"home"`, `run_lock = root/"run.lock"`,
`git_lock = root/"git.lock"`. `ensure()` creates `root`, `venvs`, `logs`, `secrets`, `home`
(not `repo`: clone creates it) with mode `0o700` and returns `self`.

### D. Database (p1)

`db.py` — `Database(path: Path)`. One `sqlite3.Connection` opened with `check_same_thread=False`,
`isolation_level=None`, `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`,
`PRAGMA foreign_keys=ON`, `row_factory = sqlite3.Row`, guarded by a `threading.Lock`. Methods:
`migrate()`, `execute(sql, params=()) -> sqlite3.Cursor`, `query(sql, params=()) -> list[sqlite3.Row]`,
`query_one(...) -> sqlite3.Row | None`, `get_setting(key: str, default: str | None = None) -> str | None`,
`set_setting(key: str, value: str) -> None`, `delete_setting(key: str) -> None`, `close()`.
The CLI and the server open the same file, so every write is a single statement (autocommit).

Schema version 1 (`PRAGMA user_version = 1`; `migrate()` is a no-op at 1 and raises
`DatabaseError("cockpit.db was written by a newer cockpit (schema N)")` above 1):

```sql
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  trigger TEXT NOT NULL,          -- "manual" | "schedule:<name>"
  recipe_path TEXT,               -- canonical recipe path; NULL for a missed-schedule row
  mode TEXT NOT NULL,             -- Mode
  status TEXT NOT NULL,           -- "running" | "finished"
  outcome TEXT,                   -- Outcome; NULL while running
  exit_code INTEGER,
  started_at TEXT NOT NULL,       -- iso()
  finished_at TEXT,
  duration_seconds REAL,
  log_path TEXT,                  -- relative to the data dir
  commit_sha TEXT,
  token_source TEXT,              -- "recipe:<path>" | "group:<name>" | "default" | "env" | "none"
  pid INTEGER,                    -- process group id while running
  stop_requested INTEGER NOT NULL DEFAULT 0,
  note TEXT
);
CREATE INDEX runs_recipe_started ON runs(recipe_path, started_at);
CREATE INDEX runs_started ON runs(started_at);
CREATE INDEX runs_job ON runs(job_id);
CREATE TABLE schedule_state (name TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0, changed_at TEXT NOT NULL);
CREATE TABLE sessions (id_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE token_status (name TEXT PRIMARY KEY, checked_at TEXT NOT NULL, state TEXT NOT NULL,
  label TEXT, expires_at TEXT, days_left INTEGER, error TEXT);
CREATE TABLE notices_sent (key TEXT PRIMARY KEY, sent_at TEXT NOT NULL);
CREATE TABLE webhooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, format TEXT NOT NULL,
  created_at TEXT NOT NULL, last_status TEXT, last_sent_at TEXT);
CREATE TABLE permissions_reports (recipe_path TEXT PRIMARY KEY, commit_sha TEXT, created_at TEXT NOT NULL,
  report_json TEXT, error TEXT);
```

Settings keys (all values are strings; booleans `"1"`/`"0"`):

| Key | Written by | Meaning |
|---|---|---|
| `server_url`, `timezone`, `log_retention_days`, `git.fetch_interval_minutes`, `git.auto_update` | Settings page | owner overrides (§E) |
| `git.repo_url`, `git.branch`, `git.auth` (`ssh`/`https`) | Code page | repository |
| `git.pinned_sha` | git manager | set while pinned |
| `git.last_fetch_ok_at`, `git.last_fetch_attempt_at`, `git.last_fetch_error`, `git.failing_since` | git manager | fetch state |
| `git.ssh_public_key` | git auth | the deploy key's public half |
| `git.pending_host_keys` | git auth | JSON list awaiting TOFU confirmation |
| `venv.current`, `venv.previous` (JSON list, newest first), `venv.last_error`, `venv.last_error_at` | venv manager | |
| `auth.password_hash`, `auth.setup_code_hash` | web auth / CLI | |
| `scheduler.last_tick_at` | scheduler | |
| `tokens.last_check_at` | token checker | |

### E. Recipes, `cockpit.yaml`, effective settings (p2)

`recipes.py`:

```python
@dataclass(frozen=True, order=True)
class Recipe:
    group_folder: str   # "10-imports"
    folder: str         # "10-sf-milestones"
    group: str          # "imports"  (group_folder minus a leading r"^\d+-")
    path: str           # "imports/10-sf-milestones"  (canonical; used for tokens, history, the UI)
    dir: Path           # absolute recipe folder
    @property
    def yaml_path(self) -> Path: ...   # dir / "recipe.yaml"

class DiscoveryError(ValueError): ...
class SelectionError(ValueError): ...

def discover(repo: Path) -> list[Recipe]
def group_names(recipes: Sequence[Recipe]) -> list[str]           # in run order, unique
def select(recipes: Sequence[Recipe], selectors: Sequence[str]) -> list[Recipe]
```

- `discover`: groups are the folders of `repo/"workflows"`; recipes are their sub-folders holding
  `recipe.yaml`. Folders starting with `_` or `.` are skipped at both levels. Order: `group_folder`
  then `folder`, plain string sort (the dataclass order). No `workflows/` → `[]`. Two group folders
  with the same short name → `DiscoveryError("workflows/10-imports and workflows/40-imports both
  have the group name imports")`.
- `select`: empty `selectors` → every recipe. Each selector is normalised: strip a leading `./`,
  then a leading `workflows/`, then a trailing `/recipe.yaml`, then trailing `/`. With a `/`: it is
  `G/R`, matching the recipe whose `group` or `group_folder` is `G` and whose `folder` is `R`.
  Without: the group whose `group` or `group_folder` equals it (all its recipes). No match →
  `SelectionError(f"no recipe or group matches {selector!r}")`. The result is deduplicated and in
  run order, whatever order the selectors came in.

`cockpit_yaml.py`:

```python
@dataclass(frozen=True)
class Schedule:
    name: str; cron: str; run: tuple[str, ...]; repeat: int = 1; stop_on_error: bool = False

@dataclass(frozen=True)
class CockpitConfig:
    server: str | None = None; timezone: str | None = None
    timeout_minutes: int = 60; schedules: tuple[Schedule, ...] = ()

class CockpitConfigError(ValueError): ...   # message always starts "cockpit.yaml: "

def load_cockpit_yaml(repo: Path) -> CockpitConfig        # no file -> CockpitConfig()
def selector_problems(config: CockpitConfig, recipes: Sequence[Recipe]) -> list[str]
```

Validation (each failure is one `CockpitConfigError`): top-level keys only `version, server,
timezone, timeout_minutes, schedules`; `version` must be the int `1`; `server` a string starting
`http://` or `https://`; `timezone` a valid `zoneinfo.ZoneInfo` key; `timeout_minutes` an int in
1..1440; `schedules` a list of mappings with keys only `name, cron, run, repeat, stop_on_error`;
`name` matches `^[a-z0-9][a-z0-9-]{0,40}$` and is unique; `cron` passes `croniter.is_valid`; `run`
a non-empty list of strings; `repeat` an int in 1..100; `stop_on_error` a bool. YAML is read with
`yaml.safe_load`. `selector_problems` returns `schedule <name>: no recipe or group matches '<sel>'`
for each selector `select` rejects.

`effective.py`:

```python
@dataclass(frozen=True)
class Effective:
    server_url: str | None; server_source: str      # "cockpit.yaml" | "settings" | "COCKPIT_SERVER_URL" | "none"
    timezone: str; timezone_source: str             # "cockpit.yaml" | "settings" | "default"
    timeout_minutes: int; retention_days: int; fetch_interval_minutes: int; auto_update: bool

def effective(db: Database, config: CockpitConfig, env: EnvConfig) -> Effective
```

Precedence: server `cockpit.yaml` > setting `server_url` > `COCKPIT_SERVER_URL` > none. Timezone
`cockpit.yaml` > setting `timezone` > `"UTC"`. `timeout_minutes` from `cockpit.yaml` (default 60).
`retention_days` setting, default 90; `fetch_interval_minutes` setting, default 15; `auto_update`
setting `git.auto_update == "1"`, default False.

### S. Secrets and tokens (p3)

`crypto.py`:

- `load_master_key(path: Path) -> bytes`: read text, strip, `base64.b64decode(validate=True)`, must
  be exactly 32 bytes. Missing file → `MasterKeyError(missing_key_message(path))`, where the
  message is exactly:
  ```
  No master key at {path} (COCKPIT_MASTER_KEY_FILE). Create one with
    openssl rand -base64 32 > cockpit_master_key
  and pass it to the container as the Docker secret cockpit_master_key. See the README, "Master key".
  ```
  Unreadable, not base64, or wrong length → `MasterKeyError(f"The master key in {path} must be 32
  random bytes, base64-encoded (openssl rand -base64 32).")`. The key is never in a message.
- `encrypt(key: bytes, plaintext: bytes, aad: str) -> bytes`: `b"PFC1" + nonce(12, os.urandom) +
  AESGCM(key).encrypt(nonce, plaintext, aad.encode())`.
- `decrypt(key, blob, aad) -> bytes`; wrong magic or `InvalidTag` → `SecretDecryptError`.

`secrets_store.py` — `SecretStore(root: Path, key: bytes)`, kinds `token`, `git`, `webhook`:

- File for `(kind, name)`: `root/kind/<urlsafe_b64encode(name.encode()) without "=">.bin`, mode
  `0o600`, written to `<file>.tmp` then `os.replace`. AAD `f"{kind}:{name}"`, so a file renamed to
  another name fails to decrypt.
- `set(kind, name, value: str)`, `get(kind, name) -> str | None`, `delete(kind, name) -> bool`,
  `names(kind) -> list[str]` (sorted), `has(kind, name) -> bool`.
- `verify_canary()`: `root/"canary.bin"` holds `encrypt(key, b"privacyfence-cockpit", "canary")`;
  created when missing; a decrypt failure raises `MasterKeyError("The master key does not match the
  one that encrypted {root}. Use the original key file, or move {root} away to start without the
  stored secrets.")`.
- Names used: `("token", <token name>)`, `("git", "ssh_key")`, `("git", "pat")`,
  `("webhook", str(<webhook id>))`.

`tokens.py`:

```python
DEFAULT_TOKEN = "default"
TOKEN_PREFIX = "pfst_"
class TokenNameError(ValueError): ...
class TokenValueError(ValueError): ...

def valid_token_names(recipes: Sequence[Recipe]) -> list[str]   # recipe paths, then group names, then "default"
def check_token_name(name: str, recipes: Sequence[Recipe]) -> None
def clean_token_value(value: str) -> str
@dataclass(frozen=True)
class ResolvedToken:
    value: str | None = field(repr=False); source: str
class TokenResolver:
    def __init__(self, store: SecretStore, env_token: str | None) -> None: ...
    def resolve(self, recipe: Recipe) -> ResolvedToken
    def resolve_name(self, name: str) -> str | None
```

- `check_token_name`: unknown name → `TokenNameError(f"unknown token name {name!r}; did you mean
  {match!r}? Token names are recipe paths, group names or default.")` with
  `difflib.get_close_matches(name, valid, n=1, cutoff=0.6)`; with no match the `did you mean` clause
  is left out.
- `clean_token_value`: strip whitespace; must start with `pfst_` and be at least 20 characters,
  else `TokenValueError("Only PrivacyFence scoped tokens (pfst_…) are supported.")`.
- `resolve`: `env_token` set → `(env_token, "env")`; else the first stored of
  `recipe.path` → `"recipe:<path>"`, `recipe.group` → `"group:<group>"`, `default` → `"default"`;
  else `(None, "none")`.

### T. Token checks through MCP (p11)

`token_check.py`:

```python
STATUS_REASON = "privacyfence-cockpit token check"
class TokenState(StrEnum):
    OK = "ok"; EXPIRING = "expiring"; EXPIRED = "expired"; REFUSED = "refused"
    NOT_SCOPED = "not_scoped"; UNREACHABLE = "unreachable"; NO_SERVER = "no_server"
@dataclass(frozen=True)
class TokenCheck:
    state: TokenState; label: str | None = None; expires_at: str | None = None
    days_left: int | None = None; error: str | None = None

async def fetch_status(server_url: str, token: str, *, transport: httpx2.AsyncBaseTransport | None = None,
                       timeout: float = 20.0) -> dict[str, Any]
def classify(status: dict[str, Any]) -> TokenCheck
class TokenChecker:
    def __init__(self, db, resolver: TokenResolver, store: SecretStore, effective: Callable[[], Effective],
                 events: EventSink, clock: Callable[[], datetime], fetch=fetch_status) -> None: ...
    async def check_one(self, name: str) -> TokenCheck
    async def check_all(self) -> dict[str, TokenCheck]
    def forget(self, name: str) -> None   # deletes token_status row and notices "token:<name>:%"
```

- `fetch_status` copies privacyfence-sync's `client.py:65-110` pattern: an
  `httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, transport=transport,
  timeout=httpx2.Timeout(timeout))`, `streamable_http_client(server_url, http_client=…)`,
  `ClientSession(...).initialize()`, `call_tool("privacyfence_status", {"reason": STATUS_REASON})`,
  structured content else `json.loads` of the text. It unwraps `BaseExceptionGroup` to the first
  leaf (as `cli.py:_first_leaf`).
- `check_one`: no server URL → `NO_SERVER`. `MCPError`, or a tool result with `is_error` → `REFUSED`
  with `error="refused by PrivacyFence: the token is revoked or expired, or the server URL is wrong"`.
  `httpx2.HTTPError` or `OSError` → `UNREACHABLE` with `error=f"cannot reach {server_url}"`. Else
  `classify`: no dict `token` key → `NOT_SCOPED` with `error="not a scoped token; v1 supports
  PrivacyFence scoped tokens only"`; `days_left == 0` → `EXPIRED`; `expiring_soon` → `EXPIRING`;
  else `OK`. No exception text from the transport is stored (it may echo headers).
- Every check upserts `token_status` and then emits: `TOKEN_INVALID` once per invalid streak for
  `REFUSED` or `EXPIRED` (notice key `token:<name>:invalid`, deleted again on `OK`/`EXPIRING`);
  `TOKEN_EXPIRING` for the smallest threshold `t` in (1, 7, 14) with `days_left <= t` whose key
  `token:<name>:<t>` is not yet in `notices_sent`. `check_all` checks every name in
  `store.names("token")` and sets `tokens.last_check_at`.

**§T1 test server.** Tests run a real MCP server in-process, no network:

```python
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
server = MCPServer("fake-privacyfence")
@server.tool()
def privacyfence_status(reason: str) -> dict: return {...}
app = server.streamable_http_app(
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
async with app.router.lifespan_context(app):
    await fetch_status("http://127.0.0.1/mcp", "pfst_x", transport=httpx2.ASGITransport(app=app))
```

A 401 case uses a bare ASGI app returning `PlainTextResponse("no", status_code=401)` as the
transport; the client then raises `MCPError` inside a `BaseExceptionGroup`.

### G. The git manager (p4) and its credentials (p5)

**§G1 `gitrepo.py`.**

```python
class GitError(RuntimeError): ...
@dataclass(frozen=True) class Commit: sha: str; author: str; date: str; subject: str
@dataclass(frozen=True) class FileChange: path: str; added: int | None; deleted: int | None   # None for binary
@dataclass(frozen=True) class CommitDetail: commit: Commit; message: str; files: tuple[FileChange, ...]
@dataclass(frozen=True)
class GitStatus:
    configured: bool; cloned: bool; branch: str | None; head: str | None; remote_tip: str | None
    pinned_sha: str | None; new_commits: int; dirty: bool
    last_fetch_ok_at: str | None; last_fetch_error: str | None

Credentials = Callable[[], AbstractContextManager[dict[str, str]]]
def no_credentials() -> AbstractContextManager[dict[str, str]]   # yields {}

class GitManager:
    def __init__(self, paths: DataPaths, db: Database, credentials: Credentials, events: EventSink,
                 clock: Callable[[], datetime] = utc_now) -> None: ...
    def configure(self, url: str, branch: str, auth: str) -> None
    def status(self) -> GitStatus
    def clone(self) -> None
    def fetch(self) -> None
    def log(self, limit: int = 200) -> list[Commit]
    def show(self, sha: str) -> CommitDetail
    def diff(self, sha: str, path: str) -> str
    def update(self) -> None
    def pin(self, sha: str) -> None
    def track(self) -> None
    def head(self) -> str | None
```

- Every command is `["git", "-C", str(paths.repo), ...]` (clone: `["git", "clone", "--branch",
  branch, "--single-branch", "--", url, str(paths.repo)]`), env = `{"PATH": "/usr/local/bin:/usr/bin:/bin",
  "HOME": str(paths.home), "LANG": "C.UTF-8", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}`
  plus what `credentials()` yields (network commands only: clone, fetch), timeout 300 s.
- Mutating commands (clone, fetch, merge, checkout) hold an exclusive `fcntl.flock` on
  `paths.git_lock` for their duration.
- `configure` validates: `url` matches `^(https://|ssh://|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:)` or is
  a local path or `file://` URL (tests use bare repositories in `tmp_path`); `branch` matches
  `^[A-Za-z0-9._/-]{1,100}$` and does not start with `-`; `auth` in `ssh`, `https`, `none`.
- `clone`: refuses when `paths.repo` exists and is not empty (`GitError("the checkout already
  exists")`); the re-clone job (§Q) deletes it first.
- `fetch`: `git fetch --prune origin` with credentials. Records `git.last_fetch_attempt_at`; on
  success `git.last_fetch_ok_at`, deletes `git.last_fetch_error`, `git.failing_since` and the notice
  `git:fetch-failing`; on failure sets `git.last_fetch_error` to `git fetch failed: <first stderr
  line, max 200 chars>`, `git.failing_since` if unset, and — when `failing_since` is more than
  24 hours old and notice `git:fetch-failing` is unsent — emits `GIT_FETCH_FAILING` and records the
  notice. Raises `GitError` with the same text.
- `status`: `head` = `rev-parse HEAD`; `remote_tip` = `rev-parse origin/<branch>`; `new_commits` =
  `rev-list --count HEAD..origin/<branch>`; `dirty` = `status --porcelain --untracked-files=no` is
  non-empty (untracked files never count: work folders, `.replay.yaml` and editable-install
  metadata are untracked).
- `log`: `log --format=%H%x1f%an%x1f%aI%x1f%s%x1e -n <limit> origin/<branch>`.
- `show(sha)`: `sha` must match `^[0-9a-f]{40}$` and `merge-base --is-ancestor <sha>
  origin/<branch>` must succeed, else `GitError("unknown commit")`. Message from
  `show -s --format=%B`; files from `show --format= --numstat` (`-` counts → None).
- `diff(sha, path)`: same `sha` check; `path` must be one of `show(sha).files` paths; `show
  --format= <sha> -- <path>`; output cut at 512 KiB with a final line `… diff truncated at 512 KiB`.
- `update`: pinned → `GitError("The checkout is pinned to <sha7>; return to tracking first.")`;
  `dirty` → `GitError("The checkout has changes to tracked files and the cockpit never overwrites
  them. Inspect them with: docker exec <container> git -C /data/repo status")`; then `fetch()`,
  `merge --ff-only origin/<branch>`; ff failure → `GitError("origin/<branch> cannot be
  fast-forwarded onto the checkout (the branch was rewritten upstream). Check out its newest commit
  to run it.")`.
- `pin(sha)`: same `sha` check and dirty check; `checkout --detach <sha>`; sets `git.pinned_sha`.
- `track()`: dirty check; `checkout <branch>`; deletes `git.pinned_sha`. It does not fast-forward.
- The cockpit never runs `commit`, `push`, `reset`, `clean` or `stash`.

**§G2 `gitauth.py`.**

```python
GITHUB_KNOWN_HOSTS = (
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
    "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=\n"
    "github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=\n"
)
@dataclass(frozen=True) class HostKey: host: str; port: int; key_type: str; key_b64: str
    # .fingerprint -> "SHA256:<b64 of sha256(b64decode(key_b64)), no padding>"
    # .known_hosts_line -> "host type key" for port 22, "[host]:port type key" otherwise

def generate_deploy_key(store: SecretStore, db: Database) -> str
def set_pat(store: SecretStore, value: str) -> None
def host_of(url: str) -> tuple[str, int] | None            # SSH URLs only; None for https/file/local
def write_builtin_known_hosts(paths: DataPaths) -> None    # home/.ssh/known_hosts_github, 0o600, every start
def is_known(paths: DataPaths, host: str, port: int) -> bool
def scan_host(host: str, port: int, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> list[HostKey]
def stage_host_keys(db: Database, keys: list[HostKey]) -> None
def confirm_host_keys(db: Database, paths: DataPaths) -> list[HostKey]
def credentials_factory(db: Database, store: SecretStore, paths: DataPaths) -> Credentials
```

- `generate_deploy_key`: `Ed25519PrivateKey.generate()`; private key as OpenSSH PEM
  (`serialization.PrivateFormat.OpenSSH`, `NoEncryption`) stored as `("git", "ssh_key")`; public as
  `"<OpenSSH public> privacyfence-cockpit"` in setting `git.ssh_public_key`, which it returns.
- `set_pat`: strip; must be non-empty and contain no whitespace, else `ValueError("That does not
  look like a GitHub token.")`; stored as `("git", "pat")`.
- `host_of`: `git@host:path` → `(host, 22)`; `ssh://[user@]host[:port]/path` → `(host, port or 22)`.
- `scan_host`: argv `["ssh-keyscan", "-T", "10", "-p", str(port), "-t", "ed25519,ecdsa,rsa", host]`
  with timeout 30; parses lines `host type key` (skipping `#` lines). Empty → `GitError(f"no SSH host
  keys from {host}:{port}")`. `host` must match `^[A-Za-z0-9.-]+$`.
- `stage_host_keys` stores the keys as JSON in `git.pending_host_keys`; `confirm_host_keys` appends
  their lines to `home/.ssh/known_hosts` (0o600), deletes the setting and returns them.
- `is_known`: `github.com` on port 22 → True; otherwise True when `home/.ssh/known_hosts` holds a
  line whose first field equals the host (port 22) or `[host]:port`.
- `credentials_factory(...)()` is a context manager. It creates `tempfile.mkdtemp(prefix="cockpit-git-")`
  (TMPDIR is the tmpfs `/tmp` in the image) and always `shutil.rmtree`s it on exit:
  - `git.auth == "ssh"`: writes the private key to `<dir>/id_ed25519` (0o600) and yields
    `{"GIT_SSH_COMMAND": shlex.join(["ssh", "-i", key, "-o", "IdentitiesOnly=yes", "-o",
    "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o",
    f"UserKnownHostsFile={home}/.ssh/known_hosts_github {home}/.ssh/known_hosts"])}`.
    No key stored → `GitError("No deploy key: generate one on the Code page.")`.
  - `git.auth == "https"`: writes `<dir>/secret` (0o600, the PAT) and `<dir>/askpass` (0o700):
    ```sh
    #!/bin/sh
    case "$1" in
      Username*) echo x-access-token ;;
      *) cat "$(dirname "$0")/secret" ;;
    esac
    ```
    and yields `{"GIT_ASKPASS": "<dir>/askpass"}`. No PAT → `GitError("No access token: add one on
    the Code page.")`.
  - otherwise yields `{}`.
  Secret material reaches git only as a short-lived file in `/tmp`, never in argv or the
  environment.

### V. The venv manager (p6)

`venvs.py`:

```python
GATE_MESSAGE = ("privacyfence-sync in requirements.txt is too old for the cockpit: "
                "pin the first version with run --result-file and permissions")
class VenvError(RuntimeError): ...
@dataclass(frozen=True) class VenvResult: path: Path | None; built: bool; error: str | None
Run = Callable[..., subprocess.CompletedProcess[str]]

def requirement_files(repo: Path) -> list[Path]
def pin_problem(repo: Path) -> str | None
def venv_hash(repo: Path, python_version: str) -> str
class VenvManager:
    def __init__(self, paths: DataPaths, db: Database, events: EventSink, python: str = sys.executable,
                 run: Run = subprocess.run, clock: Callable[[], datetime] = utc_now) -> None: ...
    def current(self) -> Path | None
    def ensure(self) -> VenvResult
    def sync_bin(self, venv: Path) -> Path      # venv / "bin" / "privacyfence-sync"
```

- `requirement_files`: `repo/"requirements.txt"` (missing → `VenvError("the recipe repository
  has no requirements.txt")`), plus, for each line `-e <path>` or `--editable <path>` or a bare
  local path starting with `./`, the `pyproject.toml` in that folder. The folder must resolve inside
  `repo` (`VenvError(f"requirements.txt names {path}, which is outside the repository")`). Sorted
  by path relative to `repo`.
- `pin_problem`: None when a line (comments and whitespace stripped) matches
  `^privacyfence[-_.]sync\s*==\s*\S+$` (case-insensitive), or when a referenced local
  `pyproject.toml` has `[project] name` normalising (`re.sub(r"[-_.]+", "-", name).lower()`) to
  `privacyfence-sync` (this lets a fixture or a pre-release be tested). Otherwise
  `"requirements.txt must pin privacyfence-sync (privacyfence-sync==X.Y.Z)"`. `tomllib` reads
  `pyproject.toml`.
- `venv_hash`: sha256 over, for each file of `requirement_files` in order,
  `relpath.encode() + b"\0" + bytes + b"\0"`, then `b"python " + python_version.encode()`, where
  `python_version` is `platform.python_version()` of `self.python`'s interpreter (`VenvManager`
  passes `".".join(map(str, sys.version_info[:3]))` when `python == sys.executable`). Hex digest.
- `ensure()`:
  1. Compute the hash `h`; `target = paths.venvs / h`. Pin problem → stop with that error (step 6).
  2. `target/".cockpit-ready"` exists → set `venv.current = h` (moving the old current to the front
     of `venv.previous`), prune, return `VenvResult(target, False, None)`.
  3. Else remove `target` if present; `[python, "-m", "venv", str(target)]`; then
     `[target/"bin/python", "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
     "-r", "requirements.txt"]` with cwd `paths.repo`, env `{"PATH": "/usr/local/bin:/usr/bin:/bin",
     "HOME": str(paths.home), "LANG": "C.UTF-8", "PIP_CACHE_DIR": str(paths.home/".cache/pip")}`,
     timeout 900 s, output appended to `paths.logs/"venv"/f"{h}.log"`.
  4. Verify: `privacyfence-sync --help` exits 0; `privacyfence-sync run --help` output contains
     `--result-file`; `privacyfence-sync permissions --help` exits 0 and its output contains
     `--json`. A failure of the last two is the error `GATE_MESSAGE`.
  5. Success: write `.cockpit-ready`, switch as in 2, delete `venv.last_error`, prune, return
     `VenvResult(target, True, None)`.
  6. Failure: error text is one line — `GATE_MESSAGE`, the pin problem, or
     `f"pip install failed with exit code {code}; see logs/venv/{h}.log"` /
     `"privacyfence-sync --help failed in the new environment"`; never pip's output. Set
     `venv.last_error`, `venv.last_error_at`; emit `VENV_FAILED` with `detail=error` when notice
     `venv:<h>` is unsent, and record it; remove `target`; return
     `VenvResult(self.current(), False, error)` — the previous venv stays in use.
- Prune: keep the current and the first two of `venv.previous`; `shutil.rmtree` every other folder
  in `paths.venvs`.

**Test fake.** `tests/fakesync.py` (p6; later phases import it):

```python
def make_fake_sync(bin_dir: Path, *, exit_code: int = 0, outcome: str | None = "ok", lines: Sequence[str] = (),
                   sleep: float = 0.0, help_result_file: bool = True, help_permissions: bool = True,
                   permissions_json: dict[str, Any] | None = None, record: Path | None = None) -> Path
```

It writes `bin_dir/"privacyfence-sync"` (0o755) starting `#!{sys.executable}` with the options
embedded as a JSON literal. Behaviour: `--help` prints a usage line and exits 0; `run --help`
prints usage including `--result-file` when `help_result_file`; `permissions --help` exits 0 with
`--json` in its text when `help_permissions`, else exits 2 with `invalid choice: 'permissions'`;
`run` prints `lines`, sleeps `sleep`, writes the result file (when `--result-file` is given and
`outcome` is not None) with the D1 fields, and exits `exit_code`; `check` and `preflight` print
`lines` and exit `exit_code`; `permissions RECIPE --json` prints `permissions_json` and exits
`exit_code`. With `record`, it appends one JSON line `{"argv": sys.argv[1:], "env": dict(os.environ),
"cwd": os.getcwd()}` to that file. Tests build a venv-shaped folder (`<venv>/bin/…`, plus
`.cockpit-ready`) around it instead of running pip.

### R. The runner (p7) and its modes (p8)

`history.py` (p7):

```python
@dataclass(frozen=True)
class RunRow:  # one row of `runs`, typed
    id: int; job_id: str | None; trigger: str; recipe_path: str | None; mode: Mode; status: str
    outcome: Outcome | None; exit_code: int | None; started_at: str; finished_at: str | None
    duration_seconds: float | None; log_path: str | None; commit_sha: str | None
    token_source: str | None; pid: int | None; stop_requested: bool; note: str | None
@dataclass(frozen=True)
class HistoryFilter:
    recipe: str | None = None; outcome: Outcome | None = None; trigger: str | None = None
    mode: Mode | None = None; since: date | None = None; until: date | None = None; job_id: str | None = None

class History:
    def __init__(self, db: Database) -> None: ...
    def start(self, *, job_id, trigger, recipe_path, mode, started_at, log_path, commit_sha, token_source) -> int
    def set_pid(self, run_id: int, pid: int | None) -> None
    def finish(self, run_id: int, *, outcome: Outcome, exit_code: int | None, finished_at: datetime, note: str | None = None) -> None
    def request_stop(self, run_id: int) -> RunRow | None
    def get(self, run_id: int) -> RunRow | None
    def query(self, f: HistoryFilter, limit: int = 50, offset: int = 0) -> list[RunRow]
    def count(self, f: HistoryFilter) -> int
    def last_by_recipe(self) -> dict[str, RunRow]          # newest finished row per recipe_path
    def record_missed(self, schedule: str, due_at: datetime, note: str) -> int
    def mark_orphans(self, now: datetime) -> int
```

`finish` computes `duration_seconds` from `started_at`. `mark_orphans`: every `running` row whose
`pid` is None or not alive (`os.killpg(pid, 0)` raises `ProcessLookupError`) becomes `finished`
with outcome `failed`, note `the cockpit stopped during this run`. `trigger` filter matches exactly;
`since`/`until` are inclusive UTC dates on `started_at`. Order: `started_at DESC, id DESC`.

`runner.py` (p7):

```python
@dataclass(frozen=True) class RunResult: run_id: int; outcome: Outcome; exit_code: int | None; log_path: Path
class Runner:
    def __init__(self, *, paths: DataPaths, history: History, venvs: VenvManager, git: GitManager,
                 tokens: TokenResolver, effective: Callable[[], Effective], clock: Callable[[], datetime] = utc_now,
                 kill_grace_seconds: float = 10.0, timeout_unit_seconds: float = 60.0) -> None: ...
    async def run(self, recipe: Recipe, *, mode: Mode, trigger: str, job_id: str | None,
                  on_line: Callable[[str], None] | None = None) -> RunResult
    def stop(self, run_id: int) -> bool
```

`run`:

1. `token = tokens.resolve(recipe)`; `eff = effective()`; `commit = git.head()`; log path
   `paths.logs / <UTC date YYYY-MM-DD> / f"{HHMMSS}_{recipe.group}_{recipe.folder}_{run_id}.log"`
   (the row is inserted first with `log_path` NULL, then updated; the file's folder is created).
2. Venv: `venvs.current()`; None → no subprocess: header, the line `# the recipe environment is not
   ready: <venv.last_error or "no environment has been built">`, footer; outcome `failed`, exit None.
3. argv (`sync = venvs.sync_bin(venv)`, `yaml = recipe.yaml_path`):
   - attended/unattended: `[sync, "run", yaml, "--result-file", result]`, plus `--attended`
     for attended. `result` is `<mkdtemp(prefix="cockpit-run-")>/result.json`, removed afterwards.
   - preflight: `[sync, "preflight", yaml]`. check: `[sync, "check", yaml]`.
   - replay: §R2.
4. env, exactly: `PATH=f"{venv}/bin:/usr/local/bin:/usr/bin:/bin"`, `HOME=str(paths.home)`,
   `LANG="C.UTF-8"`, `TZ=eff.timezone`, `PRIVACYFENCE_MCP_URL=eff.server_url` (only when set),
   `PRIVACYFENCE_TOKEN=token.value` (only when set), `COCKPIT_REPLAY="1"` (replay only). Nothing
   from the cockpit's own environment.
5. `asyncio.create_subprocess_exec(*argv, cwd=recipe.dir, env=env, stdout=PIPE,
   stderr=STDOUT, start_new_session=True)`; `history.set_pid(run_id, proc.pid)` (the process group
   id equals the pid). Each output line (decoded UTF-8 with `errors="replace"`, newline stripped)
   is written to the log and passed to `on_line`.
6. Timeout `eff.timeout_minutes * timeout_unit_seconds` s (60 in production; tests pass a tiny unit): on expiry, terminate the group (step 8); outcome `timed_out`.
7. Outcome after exit: `stop_requested` on the row → `stopped`; timed out → `timed_out`; run modes
   (attended, unattended, replay): the result file parsed as JSON, `outcome` mapped through
   `RESULT_FILE_OUTCOMES`; missing, unreadable or unknown → `failed`. preflight and check:
   `EXIT_CODE_OUTCOMES.get(code, FAILED)`.
8. Terminating a group: `os.killpg(pgid, SIGTERM)`; wait `kill_grace_seconds`; still alive →
   `os.killpg(pgid, SIGKILL)`. `ProcessLookupError` is ignored.
9. `history.finish(...)`, `set_pid(run_id, None)`; footer; return.

`stop(run_id)`: `history.request_stop` sets `stop_requested=1` and returns the row; if it is
`running` with a pid, terminate the group as in step 8 in a background thread
(`threading.Thread(daemon=True)`), return True; else False. It works for a run started by another
process (the CLI), because the pid is in the database.

Log header and footer (each line exactly; `command` shows the executable as `privacyfence-sync`
and the YAML path relative to `paths.repo`):

```
# PrivacyFence Cockpit run {run_id}
# recipe: {recipe.path}
# mode: {mode}   trigger: {trigger}
# command: {shlex.join(display_argv)}
# token: {token.source}
# commit: {commit or "unknown"}
# started: {iso(start)}
...
# exit code: {exit_code if not None else "none"}
# outcome: {OUTCOME_LABELS[outcome]}
# duration: {seconds:.1f} s
```

**§R2 p8 additions.**

`replay.py`:

```python
REPLAY_ALLOWLIST = frozenset({"drive_list_files", "drive_download_file", "drive_sheets_get_values",
    "salesforce_export_report", "salesforce_run_report", "jira_export_issues", "assets_export_objects",
    "grist_export_records", "grist_export_sql", "grist_get_records"})
REPLAY_FILE = ".replay.yaml"
def cut_replay(recipe_yaml: Path) -> Path | None
```

`cut_replay` loads the YAML (`yaml.safe_load`), keeps steps in order up to (not including) the first
step that has a `tool` key whose value is not in the allowlist, writes the rest of the document with
those steps to `recipe_yaml.parent / REPLAY_FILE` (`yaml.safe_dump(sort_keys=False)`), returns the
path; returns None (and writes nothing) when no step is kept. Runner replay mode: `cut_replay`; None →
no subprocess, outcome `ok`, note `nothing to replay: the first step writes`; else
`[sync, "run", <replay file>, "--result-file", result]` (unattended) with `COCKPIT_REPLAY=1`, and
the replay file is deleted in a `finally`.

`checks.py`:

```python
def cockpit_problems(recipe: Recipe, repo: Path) -> list[str]
```

The recipe YAML must parse to a mapping (`recipe.yaml is not a YAML mapping`); `work_dir`
(default `"work"`) must resolve inside the recipe folder (`work_dir {w} is outside the recipe
folder`); `git -C repo check-ignore -q <work dir relative to repo>` must exit 0; exit 1 →
`work_dir {rel} is not git-ignored; add it to .gitignore so source data never reaches git`, exit
128 → `the recipe repository is not a git checkout`. Runner check mode writes each problem to the
log as `# cockpit check: <problem>` before running `privacyfence-sync check`; any problem makes
the outcome `recipe_error` whatever the exit code.

`permissions.py`:

```python
NO_SUGGESTIONS = "update PrivacyFence to see suggestions"
@dataclass(frozen=True) class PermissionsReport: recipe: str; data: dict[str, Any] | None; error: str | None
@dataclass(frozen=True) class NeededRule: group: str; label: str; value: tuple[str, ...] | None; verbs: tuple[str, ...]; recipes: tuple[str, ...]
@dataclass(frozen=True)
class MergedSet:
    title: str; rules: tuple[NeededRule, ...]; tools: tuple[str, ...]
    unsuggested: tuple[str, ...]; errors: tuple[str, ...]

async def collect(recipe: Recipe, runner: Runner, *, timeout: float = 120.0) -> PermissionsReport
def merge(reports: Sequence[PermissionsReport], by: Literal["union", "group"], groups: Mapping[str, str]) -> list[MergedSet]
def rule_text(label: str, value: Sequence[str] | None, verbs: Sequence[str]) -> str
def render_recipe(report: PermissionsReport) -> list[str]
def render_merged(sets: Sequence[MergedSet]) -> list[str]
```

- The `permissions RECIPE --json` output (PrivacyFence plan D3) is
  `{"version": 1, "recipe": name, "steps": [{"id", "tool", "status", "matched_rule", "dropped",
  "suggested_rules"}], "needed": [{"group", "label", "value", "verbs"}], "tools": [...]}`. Statuses:
  `auto`, `covered`, `needs_rule`, `unknown`, `not_checked`, `not_available`. `value` is null when the
  rule names a resource reached only at run time; `suggested_rules` is null when the server is too
  old to suggest (show `NO_SUGGESTIONS`). Each suggestion is `{"group", "label", "value",
  "value_known", "verb", "operation"}`.
- `collect` runs `[sync, "permissions", yaml, "--json"]` with the same env and cwd as a run
  (the runner exposes `Runner.command_env(recipe) -> tuple[Path, dict[str, str]] | None` returning
  `(venv, env)` or None when no venv; `collect` takes the `Runner`). Exit 0 → `json.loads(stdout)`;
  otherwise `error = f"privacyfence-sync permissions exited with {code}: {first stderr line[:200]}"`;
  no venv → `error = "the recipe environment is not ready"`. Not recorded in `runs`; does not take
  the run lock.
- `rule_text`: `f"{label} = {', '.join(value)} ({', '.join(verbs)})"`, or
  `f"{label}, naming what it reaches at run time ({', '.join(verbs)})"` when `value` is None.
- `merge` combines `needed` entries keyed by `(group, tuple(value) or None)`, verbs unioned and
  sorted, recipes listed; tools unioned and sorted; `unsuggested` lists
  `f"{recipe} [{step id}] {tool}"` for steps whose status is `needs_rule` or `unknown` and whose
  `suggested_rules` is null; `errors` lists `f"{recipe}: {error}"`. `by="union"` gives one set
  titled `All recipes`; `by="group"` one per group (`groups` maps recipe path → group) titled
  `Group <name>`, in run order.
- `render_recipe` lines: the recipe path; per step `  [{id}] {tool}: {status}` and, for
  `needs_rule`/`unknown`, each suggestion as `      {rule_text}` or `      {NO_SUGGESTIONS}` when
  `suggested_rules` is null; then `  Rules:` with `    {rule_text}` per `needed` entry (or
  `    none`), then `  Tools: {comma list}`. An error report is the path and `  error: {error}`.
- `render_merged`: the title; `  Rules:`; `    {rule_text}   [{recipes}]`; `  Tools: …`; when
  non-empty `  Steps without suggestions ({NO_SUGGESTIONS}):` and the entries; `  Errors:` and the
  entries.

`history.py` additions (p8):

```python
CSV_COLUMNS = ("id", "started_at", "finished_at", "trigger", "recipe", "mode", "outcome",
               "exit_code", "duration_seconds", "commit", "token_source", "log_path", "note")
def export_csv(self, f: HistoryFilter, out: TextIO) -> int     # all matching rows, oldest first
def prune_logs(self, paths: DataPaths, retention_days: int, now: datetime) -> int
```

`prune_logs` deletes `paths.logs/<YYYY-MM-DD>` folders older than `retention_days` (by the folder's
date) and `paths.logs/"venv"/*.log` files whose mtime is older; rows stay. It returns the number of
folders and files removed.

### Q. Run lock, queue, worker and scheduler (p9)

`runlock.py`: `RunLock(path: Path)` around `fcntl.flock(LOCK_EX)` on a file opened `a+`;
`acquire(on_wait: Callable[[], None] | None = None)` (tries `LOCK_NB` first; when that fails it
calls `on_wait`, then blocks), `try_acquire() -> bool`, `release()`, context manager. The worker
takes it around each job (the server's and the CLI's own); the CLI passes an `on_wait` that prints
`Waiting for the cockpit's current run to finish…` to stderr. CLI commands that change the checkout
without the worker (`git update`) take it directly with the same `on_wait`. A process never takes
it twice (flock on a second descriptor in the same process would block). This is what keeps runs
serial across the server and `docker exec cockpit …`.

`jobs.py`:

```python
class JobKind(StrEnum):
    RUN = "run"; GIT_UPDATE = "git_update"; GIT_PIN = "git_pin"; GIT_TRACK = "git_track"; GIT_CLONE = "git_clone"

@dataclass(frozen=True)
class Job:
    kind: JobKind; trigger: str
    selectors: tuple[str, ...] = (); mode: Mode = Mode.UNATTENDED; repeat: int = 1
    stop_on_error: bool = False; sha: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    submitted_at: datetime = field(default_factory=utc_now)

@dataclass(frozen=True)
class JobSummary: job_id: str; run_ids: tuple[int, ...]; error: str | None

class JobQueue:
    def submit(self, job: Job) -> bool   # False when a job with the same "schedule:<name>" trigger is queued or running
    def queued(self) -> list[Job]; def running(self) -> Job | None
    def cancel(self, job_id: str) -> bool  # queued jobs only
    async def next(self) -> Job

class Worker:
    def __init__(self, *, queue: JobQueue, runner: Runner, git: GitManager, venvs: VenvManager,
                 history: History, lock: RunLock, recipes: Callable[[], list[Recipe]],
                 effective: Callable[[], Effective], events: EventSink,
                 on_lock_wait: Callable[[], None] | None = None,
                 on_line: Callable[[str], None] | None = None) -> None: ...   # on_line is passed to Runner.run
    async def execute(self, job: Job) -> JobSummary
    async def run_forever(self) -> None
```

`execute` (holding `lock`, acquired with `await asyncio.to_thread(lock.acquire, on_lock_wait)`):

- `GIT_UPDATE` → `git.update()`, then `venvs.ensure()`; `GIT_PIN` → `git.pin(sha)`, `venvs.ensure()`;
  `GIT_TRACK` → `git.track()`, `venvs.ensure()`; `GIT_CLONE` → remove `paths.repo` if present,
  `git.clone()`, `venvs.ensure()`. A `GitError`/`VenvError` becomes `JobSummary.error`, logged at
  WARNING with the message only. Git calls run in `asyncio.to_thread`.
- `RUN`: when the trigger starts with `schedule:` and `effective().auto_update` and the checkout is
  not pinned: `git.update()` (an error is logged and the run goes on with the current checkout).
  Then `venvs.ensure()` in a thread. Then `select(recipes(), job.selectors)`; a `SelectionError`
  records one finished row (recipe_path = the joined selectors, outcome `recipe_error`, note = the
  message) and ends the job. For each recipe in order, up to `job.repeat` times: run it; on a
  scheduled trigger and an outcome in `NOTIFY_OUTCOMES`, emit `RUN_FAILED` (recipe, schedule name,
  outcome, exit code, run id) unless `(recipe, outcome)` was already emitted in this job; repeat
  again only while the outcome is `ok`; after the repeats, when `stop_on_error` and the last outcome
  is not in `SUCCESS_OUTCOMES`, skip the remaining recipes.
- `run_forever`: `next()`, `execute`, never dies on an exception (logged with `logger.exception`).

`scheduler.py`:

```python
TICK_SECONDS = 30
MISSED_AFTER_SECONDS = 90
def next_fire(schedule: Schedule, tz: str, after: datetime) -> datetime
def fires_between(schedule: Schedule, tz: str, start: datetime, end: datetime) -> list[datetime]   # (start, end]
def schedules_for(recipe: Recipe, config: CockpitConfig, recipes: Sequence[Recipe]) -> list[Schedule]

class Scheduler:
    def __init__(self, *, db: Database, history: History, queue: JobQueue, config: Callable[[], CockpitConfig],
                 effective: Callable[[], Effective], events: EventSink, clock: Callable[[], datetime] = utc_now) -> None: ...
    def is_paused(self, name: str) -> bool
    def set_paused(self, name: str, paused: bool) -> None
    def tick(self) -> list[Job]      # the jobs submitted
    async def run_forever(self) -> None   # tick every TICK_SECONDS
```

`tick()`: `now = clock()`; `last = parse_iso(scheduler.last_tick_at)` or `now` when unset (first
start ever: nothing is missed). A `CockpitConfigError` from `config()` → log WARNING, set
`last_tick_at = now`, return `[]`. For each schedule not paused: `due = fires_between(s, tz, last,
now)`; those with `due <= now - MISSED_AFTER_SECONDS` are missed; the rest are due now. Missed fires
→ one `history.record_missed(name, last_missed, note)` with note
`f"{n} run(s) between {first:%Y-%m-%d %H:%M} and {last:%Y-%m-%d %H:%M} {tz} were missed while the cockpit was not running"`
and one `SCHEDULE_MISSED` event with that note as `detail`. Any due-now fire → one
`Job(kind=RUN, trigger=f"schedule:{name}", selectors=s.run, mode=UNATTENDED, repeat=s.repeat,
stop_on_error=s.stop_on_error)` submitted (a False from `submit` is logged at INFO). Finally
`last_tick_at = now`. Fires are computed with `croniter(cron, start.astimezone(ZoneInfo(tz)))`.
Paused schedules neither run nor count as missed. Missed runs are never caught up.

### N. Notifications (p10)

`notify.py`:

```python
class WebhookFormat(StrEnum): JSON = "json"; SLACK = "slack"; NTFY = "ntfy"
@dataclass(frozen=True) class Webhook: id: int; name: str; format: WebhookFormat; last_status: str | None; last_sent_at: str | None
def message_for(event: Event) -> str
def link_for(event: Event, public_url: str | None) -> str | None
def build_request(fmt: WebhookFormat, event: Event, public_url: str | None) -> tuple[dict[str, str], bytes]
class Notifier:   # implements EventSink
    def __init__(self, db: Database, store: SecretStore, public_url: str | None,
                 transport: httpx2.AsyncBaseTransport | None = None, clock=utc_now) -> None: ...
    def add(self, name: str, fmt: WebhookFormat, url: str) -> int
    def delete(self, webhook_id: int) -> bool
    def list(self) -> list[Webhook]
    def emit(self, event: Event) -> None              # thread-safe; queues for delivery
    async def deliver_pending(self) -> int            # sends everything queued; returns count
    async def send_test(self, webhook_id: int) -> str  # the resulting status text
    async def run_forever(self) -> None               # deliver_pending every 2 s
```

- `add`: `name` 1..60 characters; `url` must start `https://` or `http://` (`ValueError("The
  webhook URL must start with https:// or http://")`); URL stored encrypted as
  `("webhook", str(id))`; never logged or shown again (the page shows the name and format only).
- `message_for` (exact):
  - `RUN_FAILED`: `f"{recipe}: {label} (exit {exit_code}) in schedule {schedule}"`; with no exit
    code `(no exit code)` replaces `(exit …)`.
  - `SCHEDULE_MISSED`: `f"Schedule {schedule}: {detail}"`.
  - `TOKEN_EXPIRING`: `f"Token {token_name} expires in {days_left} day(s)"`.
  - `TOKEN_INVALID`: `f"Token {token_name} was refused by PrivacyFence: it is revoked or expired, or the server URL is wrong"`.
  - `VENV_FAILED`: `f"The recipe environment could not be built: {detail}"`.
  - `GIT_FETCH_FAILING`: `"Fetching the recipe repository has failed for over 24 hours"`.
  - `TEST`: `"Test notification from PrivacyFence Cockpit"`.
- `link_for`: None without `public_url`; `RUN_FAILED` → `{public}/runs/{run_id}`;
  `SCHEDULE_MISSED` → `/schedules`; token events → `/tokens`; `VENV_FAILED` → `/`;
  `GIT_FETCH_FAILING` → `/code`; `TEST` → `/notifications`.
- `build_request`:
  - JSON: header `Content-Type: application/json`; body `{"source": "privacyfence-cockpit",
    "event": kind, "message": …, "recipe": …, "schedule": …, "outcome": …, "exit_code": …,
    "time": iso(time), "url": link}` (nulls kept).
  - Slack: `{"text": "PrivacyFence Cockpit: " + message + (f" <{link}|Open>" if link else "")}`.
  - ntfy: body = message (UTF-8); headers `Title: PrivacyFence Cockpit`,
    `Tags: warning` (`white_check_mark` for `TEST`), `Click: <link>` when there is one.
- Delivery: `httpx2.AsyncClient(timeout=10, transport=transport)` POST to every webhook in id
  order. Status text `HTTP {code}` or `error: {type(exc).__name__}`, saved in `last_status` and
  `last_sent_at`. Failures are logged as `webhook {name}: {status}` at WARNING and never raised.
  Payloads never include log lines, file contents, tokens or the webhook URL.

### W. The web app (p12–p18)

**§W0 Shell contracts (p13).** These let the page phases (p15–p18) run in parallel without
editing a shared file.

- `web/app.py` builds its routes from `routes_auth`, `routes_runs`, `routes_schedules`,
  `routes_code`, `routes_tokens`, `routes_permissions`, `routes_notifications`, `routes_settings`,
  in that order, each exposing `ROUTES: list[BaseRoute]`. p13 creates every one of these modules
  as a stub (`ROUTES: list[BaseRoute] = []`). A page phase edits only its own module, its own
  templates and its own tests.
- `web/render.py`: `render(request, template: str, context: dict[str, Any], status_code: int = 200)
  -> HTMLResponse` adds `cockpit`, `banners` (§W2), `flash` (read from the cookie and cleared),
  `csrf_token` (`getattr(request.state, "csrf_token", "")`), `nav` (first path segment) and
  `version`. `redirect(url: str, flash: str | None = None) -> RedirectResponse` returns a 303 and,
  with a flash, sets cookie `cockpit_flash` (urlsafe base64 of the text cut to 300 characters,
  HttpOnly, SameSite=Strict, Path=/, Max-Age=60).
- Jinja filters registered in `app.py`: `localtime(iso_text, tz) -> "YYYY-MM-DD HH:MM <tz>"`
  (empty string for None), `outcome_label(value)`, `sha7(value)`.
- `web/templates/_macros.html`: `chip(outcome)` (a `span.chip.chip-<outcome>` with the label),
  `post_button(action, label, csrf_token, fields={}, cls="")` (an inline form with the hidden
  `csrf_token` and the extra hidden fields), `field_error(text)`.
- `cockpit.css` holds every class the pages use: `.banner.banner-info|warning|error`, `.flash`,
  the chips, `table.data`, `.actions`, `form.inline`, `.muted`, `.mono`, `pre.log`, `pre.diff`,
  `.add`, `.del`, `.danger`, `.card`, `.grid2`, `textarea.key`. Page phases use these and do not
  edit `cockpit.css` or `cockpit.js`.
- Test helpers: `tests/cockpit_factory.py` (p12) `make_cockpit(tmp_path, *, clock=None,
  environ=None) -> Cockpit` (writes a master key file, sets `COCKPIT_DATA_DIR` and
  `COCKPIT_MASTER_KEY_FILE` under `tmp_path`); `tests/webclient.py` (p13) `client(cockpit) ->
  TestClient` over `create_app(cockpit, start_background=False)`, and (p14) `logged_in(cockpit)
  -> tuple[TestClient, str]` that sets a password, logs in and returns the CSRF token.

**§W1 Auth (p14), `web/auth.py`.**

- Passwords: `hashlib.scrypt(pw.encode(), salt=os.urandom(16), n=2**15, r=8, p=1,
  maxmem=64*1024*1024, dklen=32)`; stored `"scrypt$32768$8$1$<salt b64>$<hash b64>"`;
  `verify_password` uses `hmac.compare_digest`. Minimum length 12: `Use at least 12 characters.`
- Setup code: `new_setup_code(db) -> str`: 12 characters from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`
  via `secrets.choice`, shown as `XXXX-XXXX-XXXX`; settings `auth.setup_code_hash` = sha256 hex of the
  12 characters without dashes, upper-cased. On server start with no `auth.password_hash`, a new
  code is made and logged once at WARNING:
  `No password is set. Setup code: {code}. Open {public_url or "the cockpit"}/setup to set one.`
  This is the only log line that carries a credential; it is one-time and useless once a password
  is set. `cockpit user reset-password` deletes `auth.password_hash` and every session, makes a new
  code, prints `Password cleared. Setup code: {code}` and the same "Open …/setup" line.
- `/setup` (GET, POST: `code`, `password`, `confirm`) exists only while no password is set (else 303
  to `/login`). Wrong code → `That setup code is not valid.`; mismatch → `The passwords do not
  match.` Success: store the hash, delete `auth.setup_code_hash`, start a session, 303 to `/`.
- `/login` (GET, POST `password`), `/logout` (POST). Wrong password → `Wrong password.`
- Sessions: cookie `cockpit_session` = `secrets.token_urlsafe(32)`; table `sessions` keeps its
  sha256 hex, a per-session `csrf_token` (`token_urlsafe(32)`) and `expires_at` = created + 7 days.
  Cookie flags `HttpOnly; SameSite=Strict; Path=/`, plus `Secure` when `EnvConfig.secure_cookies`,
  `Max-Age=604800`. Password change and reset delete every other session.
- CSRF: every POST must carry form field `csrf_token` or header `X-CSRF-Token` equal to the
  session's token (`hmac.compare_digest`); `/login` and `/setup` use a double-submit cookie
  `cockpit_csrf` (HttpOnly, SameSite=Strict) set on GET. Failure → 403
  `Invalid or missing CSRF token. Reload the page and try again.` `base.html` puts
  `hx-headers='{"X-CSRF-Token": "<token>"}'` on `<body>`.
- Throttle (`LoginThrottle(clock)`, shared by `/login` and `/setup`): after 5 consecutive failures,
  every attempt within `min(30 * 2 ** (failures - 5), 900)` seconds of the last failure is refused
  with `Too many failed attempts. Try again in {n} seconds.`; success resets it.
- Middleware order: security headers, then auth. Unauthenticated: `/healthz`, `/static/*`,
  `/login`, `/setup`. Other GETs redirect (303) to `/setup` when no password is set, else `/login`;
  other methods get 403.
- Security headers on every response: `Content-Security-Policy: default-src 'self'; script-src
  'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none';
  base-uri 'none'; form-action 'self'`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: same-origin`, `X-Frame-Options: DENY`. htmx's inline indicator style is
  disabled with `<meta name="htmx-config" content='{"includeIndicatorStyles": false}'>`.

**§W2 Services (p12) and the app shell (p13).** `services.py`:

```python
class Cockpit:
    env: EnvConfig; paths: DataPaths; db: Database; store: SecretStore; tokens: TokenResolver
    notifier: Notifier; git: GitManager; venvs: VenvManager; history: History; runner: Runner
    queue: JobQueue; worker: Worker; scheduler: Scheduler; token_checker: TokenChecker; lock: RunLock
    @classmethod
    def open(cls, env: EnvConfig, *, clock: Callable[[], datetime] = utc_now) -> Cockpit
    def recipes(self) -> list[Recipe]           # discover(paths.repo); [] on DiscoveryError (logged)
    def config(self) -> CockpitConfig           # load_cockpit_yaml(paths.repo); raises CockpitConfigError
    def config_or_default(self) -> tuple[CockpitConfig, str | None]   # (config, error text)
    def effective(self) -> Effective
    async def start(self) -> None               # background tasks
    async def stop(self) -> None
```

`open`: `paths.ensure()`; `load_master_key`; `SecretStore.verify_canary()`; `Database.migrate()`;
`history.mark_orphans`; `write_builtin_known_hosts`; builds everything with `notifier` as the event
sink. `start()` creates tasks: `worker.run_forever`, `scheduler.run_forever`, `notifier.run_forever`,
a fetch loop (when `git.status()` reports `configured` and `cloned`: `git.fetch` in a thread every
`fetch_interval_minutes`, errors logged), a token loop (60 s after start, then hourly: `check_all` when
`tokens.last_check_at` is unset or 24 h old), a retention loop (daily: `history.prune_logs`).
`stop()` cancels them.

`web/app.py` (p13): `create_app(cockpit: Cockpit, *, start_background: bool = True) -> Starlette`;
`app.state.cockpit`; lifespan runs `cockpit.start()`/`stop()` when `start_background`; mounts
`/static` (`StaticFiles(packages=[("privacyfence_cockpit.web", "static")])`); routes from each
`web/routes_*.py` module's `ROUTES: list[BaseRoute]`. `Jinja2Templates` with
`PackageLoader("privacyfence_cockpit.web", "templates")`, autoescape on.

`web/server.py` (p13): `serve(env: EnvConfig) -> int` configures logging
(`%(asctime)s %(levelname)s %(name)s: %(message)s`, level `env.log_level`), calls `Cockpit.open`
(a `MasterKeyError` or `ConfigError` → its message on stderr, return 2), issues the setup code if
needed (p14), and runs `uvicorn.Server(uvicorn.Config(app, host, port, log_config=None,
proxy_headers=False)).run()` — that one call carries `# pragma: no cover  # needs a real socket`.

`/healthz`: `{"status": "ok"}` 200 when `SELECT 1` succeeds, else `{"status": "error"}` 503.

`web/context.py` (p13): `banners(cockpit) -> list[Banner(level: "info"|"warning"|"error", text: str,
link: str | None)]`, rendered on every page by `base.html`, in this order:
1. `env_token` set: warning `PRIVACYFENCE_TOKEN is set in the container environment: it overrides every stored token.`
2. Repo not configured: info `No recipe repository yet.` → `/code`.
3. `cockpit.yaml` error: error with its text → `/schedules`.
4. `DiscoveryError`: error with its text.
5. Pinned: warning `Schedules run the pinned commit {sha7}, not the tip of {branch}.` → `/code`.
6. `venv.last_error`: error `The recipe environment could not be built: {error}` → `/code`.
7. `git.failing_since` set: warning `Fetching the recipe repository fails: {git.last_fetch_error}` → `/code`.
8. `new_commits > 0` and tracking: info `{n} new commit(s) on {branch}.` → `/code`.

`base.html`: nav links Overview `/`, History `/history`, Schedules `/schedules`, Code `/code`,
Tokens `/tokens`, Permissions `/permissions`, Notifications `/notifications`, Settings `/settings`,
and a Log out button (POST `/logout`). Static files: `htmx.min.js` (2.0.4, sha256 above, fetched
from the npm registry tarball), `cockpit.css` (CSS custom properties; a light palette and a dark one
under `@media (prefers-color-scheme: dark)`; outcome chips `.chip-ok`, `.chip-no_change`,
`.chip-recipe_error`, `.chip-failed`, `.chip-denied`, `.chip-not_signed_in`, `.chip-stopped`,
`.chip-timed_out`, `.chip-missed`), `cockpit.js` (§W3). No inline scripts or styles anywhere.

**§W3 Overview, runs, history (p15), `web/routes_runs.py`.**

| Route | Does |
|---|---|
| `GET /` | Groups in run order, each recipe with checkbox `recipe=<path>`, last-outcome chip and time (link to the run), next scheduled run (earliest `next_fire` over non-paused schedules whose selectors include it, shown in the effective timezone), and a Run button. Above: the queue (`Running: <job>` and `<n> queued`). Below the table: mode select (`unattended` default, `attended`, `preflight`, `check`, `replay`) and "Run selected". |
| `POST /run` | Form `recipe` (repeated) and `mode`; no recipe → 303 back with flash `Pick at least one recipe.`; else `Job(RUN, "manual", selectors=recipes, mode=mode)`, 303 to `/jobs/{id}`. |
| `GET /jobs/{job_id}` | The job's rows (`HistoryFilter(job_id=…)`) and, while queued or running, `hx-get="/jobs/{id}/rows" hx-trigger="every 2s"` on the table. A Cancel button while queued. |
| `GET /jobs/{job_id}/rows` | The table fragment. |
| `POST /jobs/{job_id}/cancel` | `queue.cancel`, 303 back. |
| `GET /runs/{id}` | Header facts (recipe, mode, trigger, outcome chip, exit code, duration, commit, token source — never the token), a Stop button while running, a link to download, and `<pre id="log" data-events="/runs/{id}/events">`. |
| `GET /runs/{id}/events` | SSE (`text/event-stream`, `Cache-Control: no-cache`): reads the log file from the start, polling every 0.5 s; each line → `event: line` / `data: {"line": …}`; when the row is finished and the file is read to the end → `event: end` / `data: {"outcome": label}` and close. `: ping` every 15 s. A missing log (retention) → one `line` event `log deleted after the retention period` then `end`. |
| `GET /runs/{id}/log` | `text/plain`, `Content-Disposition: attachment; filename="run-{id}.log"`. |
| `POST /runs/{id}/stop` | `runner.stop`, 303 to the run. |
| `GET /history` | Filters `recipe`, `outcome`, `trigger`, `mode`, `since`, `until` (YYYY-MM-DD), `page` (1-based, 50 per page); invalid values are ignored. Missed rows show `—` as recipe and their note. Link "Download runs.csv" with the same query. |
| `GET /history/runs.csv` | `history.export_csv`, `Content-Disposition: attachment; filename="runs.csv"`. |

`cockpit.js`: on a page with `#log[data-events]`, opens `EventSource`, appends each line as a text
node plus `\n`; a line matching `/^\[[A-Za-z0-9_-]+\] waiting for approval: (https?:\/\/\S+)$/`
is appended as the prefix text plus an `<a href=url target="_blank" rel="noopener noreferrer">`
whose `textContent` is the URL; scrolls to the bottom when already at the bottom; on `end` shows
the outcome in `#run-outcome` and closes the source. No `innerHTML` with log data.

**§W4 Schedules and Code (p16).**

`web/routes_schedules.py`: `GET /schedules` lists each schedule (name, cron, effective timezone,
`run` selectors, next fire, paused/active, the last run row with that trigger) and the 20 newest
missed rows; a `cockpit.yaml` error is shown instead of the table. `POST /schedules/{name}/pause`,
`/resume` (`scheduler.set_paused`), `POST /schedules/{name}/run` (Run now:
`Job(RUN, "manual", selectors=s.run, repeat=s.repeat, stop_on_error=s.stop_on_error)`, 303 to the
job). Unknown name → 404.

`web/routes_code.py`:

| Route | Does |
|---|---|
| `GET /code` | Not configured → the setup form (URL, branch default `main`, auth `ssh`/`https`). Configured → status (URL, branch, auth, checkout sha + "tracking" or "pinned", remote tip, new commits, last fetch / error, dirty warning), the credentials box (§W4a), actions (Fetch now, Update to latest, Return to tracking when pinned, Clone / Re-clone), and the 200-commit list with the checkout marked `checked out` and the remote tip `origin/<branch>`. Each commit links to its detail and has "Check out this commit". |
| `GET /code/commits/{sha}` | Full message, author, date, changed files with `+a −d` (`binary` for None); each file has `hx-get="/code/commits/{sha}/diff?path=…"` into a `<pre>` below it. Unknown → 404. |
| `GET /code/commits/{sha}/diff` | The diff text in a `<pre class="diff">`, lines prefixed `+`/`-` wrapped in `<span class="add">`/`<span class="del">` (escaped). |
| `POST /code/setup` | `git.configure`; 303 to `/code`. Changing the URL of a cloned repo asks for a Re-clone. |
| `POST /code/fetch` | Fetch in a background thread now; 303 with flash `Fetching…`. |
| `POST /code/update`, `/code/track`, `/code/checkout` (`sha`), `/code/clone` | Submit `GIT_UPDATE` / `GIT_TRACK` / `GIT_PIN` / `GIT_CLONE` jobs; flash `Queued: it starts after the current run.` when a job is running, else `Started.` |
| `POST /code/ssh-key` | `generate_deploy_key`; replacing an existing key needs the checkbox `confirm=1`. |
| `POST /code/pat` | `set_pat` (write-only field, `autocomplete="off"`). |
| `POST /code/host-keys/scan` | `scan_host` for the URL's host, `stage_host_keys`; the page lists each pending key's type and fingerprint with a Confirm button. |
| `POST /code/host-keys/confirm` | `confirm_host_keys`. |

§W4a credentials box: for `ssh`, the public key in a read-only `<textarea>` and, for a
`github.com` URL, the link `https://github.com/<owner>/<repo>/settings/keys/new` with the text
`Add it as a deploy key with "Allow write access" left off.`; for an SSH host that `is_known`
rejects, the scan/confirm flow, and Clone is disabled until confirmed. For `https`, `Access token:
present|missing` and the write-only form, with the hint `A fine-grained token with read-only
"Contents" access to this one repository.` Clone is disabled while the needed credential is missing.

**§W5 Tokens and Permissions (p17).**

`web/routes_tokens.py`: `GET /tokens`: a row per stored token name (present), with the
`token_status` columns (label, expires, days left, state chip, checked at, error), a Delete button;
names in `valid_token_names` that have no token are listed as `missing`; a "Set a token" form (name
input with a `<datalist>` of valid names, `<input type="password" autocomplete="off">` for the
value); "Check all now"; the line `Last use and last denial of each token: {origin}/settings` (the
scheme and host of the effective server URL, shown only when there is one). `POST /tokens`:
`check_token_name` (the error with its suggestion is shown), `clean_token_value`, `store.set`,
`token_checker.forget`, then `await token_checker.check_one(name)`, 303 to `/tokens`.
`POST /tokens/delete` (form `name`): `store.delete` and `forget`. `POST /tokens/check`:
`check_all`. The value is never echoed back, even on an error.

`web/routes_permissions.py`: `GET /permissions?view=recipe|union|group` (default `recipe`) shows the
stored `permissions_reports` rendered with §R2's functions (as HTML lists, same texts), the time and
commit of each report, and `{NO_SUGGESTIONS}` where it applies. `POST /permissions/refresh` starts
one background task (none if one is running) that runs `permissions.collect` for every recipe in
order and upserts `permissions_reports` (report JSON or error, `git.head()`); the page shows
`Refreshing…` with `hx-trigger="every 3s"` while it runs.

**§W6 Notifications and Settings (p18).**

`web/routes_notifications.py`: `GET /notifications` lists webhooks (name, format, last status, last
sent) with Delete and "Send test"; an add form (name, format `json`/`slack`/`ntfy`, URL as
`type="url"`); and the list of events that notify (the `EventKind` messages of §N, in words).
`POST /notifications` (`add`), `POST /notifications/{id}/delete`, `POST /notifications/{id}/test`
(flash: the status text).

`web/routes_settings.py`: `GET /settings` shows a form for `server_url` (empty = unset),
`timezone`, `log_retention_days` (1..3650), `git.fetch_interval_minutes` (5..1440), `git.auto_update`
(checkbox "Update before each scheduled run"), with the effective value and its source next to each
(`cockpit.yaml wins over this setting` where it does), and a password form (current, new, confirm).
`POST /settings` validates (`Timezone {x} is unknown.`, `Server URL must start with http:// or
https://.`, `{field} must be a whole number between {a} and {b}.`), saves, 303 with `Saved.`
`POST /settings/password`: wrong current → `Wrong password.`; then §W1 rules; success deletes all
other sessions.

### L. The CLI (p13, p14, p19)

`cli.py` — `main(argv: list[str] | None = None) -> int`, argparse `prog="cockpit"`,
`allow_abbrev=False`. Every command builds `Cockpit.open(EnvConfig.from_environ(os.environ))`
(p13 adds `serve`, p14 `user reset-password`, p19 the rest). Commands that run recipes or
change the checkout hold the run lock (§Q). Exit codes: 0 ok; 1 a usage, recipe, selection or
validation error; 2 an environment error (master key, config, git, venv); for `run`, the exit code
of the first recipe whose outcome is not in `SUCCESS_OUTCOMES` (`stopped`/`timed_out` → 2).

| Command | Output |
|---|---|
| `serve` | §W2 |
| `list` | per group: the group name, then `  {folder:<32} {label:<14} {when}` (`never` when no run) |
| `check [PATH…]` | runs mode `check` per recipe, printing the log lines; summary `check: {n} recipe(s), {k} failed` |
| `preflight [PATH…] [--attended]` | mode `preflight` per recipe; streams lines |
| `permissions [PATH…] [--union \| --by-group] [--json]` | `collect` per recipe; text via `render_recipe`/`render_merged`; `--json` prints `{"recipes": [{"recipe", "report", "error"}]}` |
| `run [PATH…] [--attended] [-n/--dry-run] [--stop-on-error] [--repeat N]` | `--dry-run` prints `would run: {path}` per recipe and runs nothing; else one `Job(RUN, "manual", …)` executed in-process by `Worker.execute` (not through the server's queue) with `on_line` printing; summary line per run `{path}: {label} (exit {code})` |
| `test [-- ARGS]` | `<venv>/bin/python -m pytest -q ARGS` in `paths.repo`, minimal env without any token; pytest missing in the venv → `Add pytest to requirements.txt to use cockpit test.`, exit 1 |
| `test --live [PATH…]` | mode `replay` per recipe |
| `token set NAME` | reads the value with `getpass.getpass("Token: ")` on a TTY, else one line from stdin; never from argv; then checks it and prints `{name}: {state} {label} expires {expires_at} ({days_left} day(s) left)` |
| `token show` | one line per valid name: `{name:<40} present\|missing  {state} {label} {days_left}`; the value never |
| `token delete NAME` | `deleted {name}` or `no token named {name}` (exit 1) |
| `schedule list` | `{name}  {cron}  {tz}  {active\|paused}  next {time}  run: {selectors}` |
| `git status` | branch, URL, checkout + tracking/pinned, remote tip, new commits, last fetch, error |
| `git update` | `git.update()` then `venvs.ensure()`, holding the run lock |
| `user reset-password` | §W1 |

`check`, `preflight`, `run` and `test --live` all build one `Job(RUN, "manual", selectors=PATHS,
mode=…)` and execute it in-process with a `Worker` built with `on_lock_wait` and `on_line` printing
(`Worker.execute` takes the run lock and builds the venv first). They record history rows with trigger `manual`.

### I. Image, CI and release (p20, p21)

**`Dockerfile`** (two stages, both `FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`):

- build stage: `ARG VERSION=0.0.0.dev0`; `COPY . /src`; `pip install --require-hashes -r
  requirements/build.lock.txt`; `SETUPTOOLS_SCM_PRETEND_VERSION=$VERSION pip wheel --no-deps
  --no-build-isolation -w /wheels /src`.
- final stage: `apt-get install -y --no-install-recommends git openssh-client tini` (lists
  removed); `useradd --uid 10001 --user-group --no-create-home --home-dir /data/home --shell
  /usr/sbin/nologin cockpit`; `mkdir -p /data && chown 10001:10001 /data`; `pip install
  --no-cache-dir --require-hashes -r requirements/image.lock.txt` (copied from the build stage);
  `pip install --no-cache-dir --no-deps /wheels/*.whl`; `ENV PYTHONDONTWRITEBYTECODE=1
  PYTHONUNBUFFERED=1 COCKPIT_DATA_DIR=/data HOME=/data/home TMPDIR=/tmp`; `USER 10001:10001`;
  `VOLUME /data`; `EXPOSE 8080`;
  `HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-c",
  "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]`;
  `ENTRYPOINT ["tini", "--"]`; `CMD ["cockpit", "serve"]`.
- `.dockerignore`: `.git`, `.github`, `.claude`, `docs`, `tests`, `.venv`, `dist`, `build`,
  `*.egg-info`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `data`. (The smoke test mounts the
  fixture from the checkout, not from the image.)

**Locks.** `requirements/build.in` (`setuptools>=61`, `wheel`, `setuptools_scm>=8`);
`scripts/update_image_locks.sh` runs, with `uv` (`python3 -m pip install uv`):
`uv pip compile --universal --python-version 3.12 --generate-hashes pyproject.toml -o
requirements/image.lock.txt` and the same for `requirements/build.in` → `requirements/build.lock.txt`.

**Fixture recipe repository** `tests/fixtures/recipe-repo/`: `cockpit.yaml` (version 1, timezone
`UTC`, one schedule `smoke` with cron `0 3 * * *` and `run: [imports]`), `requirements.txt`
(`-e ./lib/privacyfence-sync-fake`), `.gitignore` (`work/`), `workflows/10-imports/10-fixture/recipe.yaml`
(version 1, name `fixture`, work_dir `work`, one script step `scripts/hello.py`) with
`scripts/hello.py`, and `lib/privacyfence-sync-fake/` (`pyproject.toml` with `[project] name =
"privacyfence-sync"`, version `0.0.0+fake`, `[project.scripts] privacyfence-sync =
"privacyfence_sync_fake:main"`, setuptools backend; `privacyfence_sync_fake.py` implementing
`--help`, `run --help` mentioning `--result-file`, `permissions --help` mentioning `--json`, and
`check RECIPE` printing `fixture: 1 step(s), OK`).

**`scripts/smoke_image.sh IMAGE`** (bash, `set -euo pipefail`): makes a temp dir with
`openssl rand -base64 32 > key` (chmod 644); a volume `cockpit-smoke-$$`; seeds it as root with
`docker run --rm --user 0 -v VOL:/data -v $PWD/tests/fixtures/recipe-repo:/fixture:ro --entrypoint sh IMAGE -c 'cp -r /fixture /data/repo && git -C /data/repo init -q -b main && git -C /data/repo add -A && git -C /data/repo -c user.name=smoke -c user.email=smoke@example.invalid commit -qm fixture && chown -R 10001:10001 /data'`;
starts `docker run -d --name cockpit-smoke-$$ --read-only --tmpfs /tmp -v VOL:/data -v KEY:/run/secrets/cockpit_master_key:ro -p 127.0.0.1:18080:8080 IMAGE`;
polls `curl -fsS http://127.0.0.1:18080/healthz` for up to 60 s; runs `docker exec … cockpit check`
and requires exit 0 and `fixture: 1 step(s), OK` in its output; waits up to 120 s for
`docker inspect -f '{{.State.Health.Status}}'` to be `healthy`; on failure prints `docker logs`;
always removes the container and volume (`trap`).

**`.github/workflows/image.yml`**: `on: workflow_dispatch` and `pull_request` with paths
`Dockerfile`, `.dockerignore`, `src/**`, `pyproject.toml`, `requirements/**`,
`tests/fixtures/recipe-repo/**`, `scripts/smoke_image.sh`, `.github/workflows/image.yml`;
`permissions: contents: read`; `concurrency: image-${{ github.ref }}` cancel in progress. Jobs:
`amd64` (checkout with `fetch-depth: 0`, setup-buildx, build-push with `platforms: linux/amd64`,
`load: true`, `tags: privacyfence-cockpit:smoke`, `build-args: VERSION=0.0.0.dev0`, then
`scripts/smoke_image.sh privacyfence-cockpit:smoke`), and `arm64` (setup-qemu, setup-buildx,
build-push with `platforms: linux/arm64`, `push: false`). Pins as in Current state.

**`.github/workflows/release.yml`** ("Release"):

- `on: workflow_dispatch` (inputs `version`, string, required; `dry_run`, boolean, default true)
  and `push: tags: ['v*']`. `concurrency: {group: release, cancel-in-progress: false}`.
  Top-level `permissions: contents: read`.
- Job `prepare` (`permissions: contents: write`): checkout `ref: main` (dispatch) or the tag (push),
  `fetch-depth: 0`; `python3 scripts/release_check.py` with `VERSION` and `EVENT` from the
  environment (never interpolated into the script): for a dispatch, `--version $VERSION` checks the
  form `^\d+\.\d+\.\d+$`, that `HEAD == origin/main`, that tag `v$VERSION` does not exist and sorts
  after every existing `v*` tag, that `CHANGELOG.md` has a `## [$VERSION]` heading and the
  `## [Unreleased]` section above it is empty; for a tag push, `--tag $GITHUB_REF_NAME` checks the
  form and that the tagged commit is an ancestor of `origin/main`. Then, for a dispatch with
  `dry_run == false`: `git tag -a v$VERSION -m v$VERSION && git push origin v$VERSION` with
  `GITHUB_TOKEN` (a tag pushed with it starts no workflow, so this run publishes itself; there is no
  second run to collide with). Outputs `version`, `sha`, `publish` (`false` for a dry run).
- Job `image` (`needs: prepare`, `if: needs.prepare.outputs.publish == 'true'`, `permissions:
  contents: read, packages: write, id-token: write, attestations: write`): checkout at `sha`;
  setup-qemu, setup-buildx; build amd64 with `load: true` and run `scripts/smoke_image.sh`; login
  to `ghcr.io` with `github.actor` / `secrets.GITHUB_TOKEN`; build-push `linux/amd64,linux/arm64`,
  `push: true`, `build-args: VERSION=<version>`, tags `ghcr.io/privacyfence/privacyfence-cockpit:X.Y.Z`,
  `:X.Y`, `:latest`; `actions/attest-build-provenance` with `subject-name:
  ghcr.io/privacyfence/privacyfence-cockpit`, `subject-digest: ${{ steps.build.outputs.digest }}`,
  `push-to-registry: true`; a step summary with the tags and digest.

`scripts/release_check.py` is stdlib-only, exits 1 with one `::error::` line per problem, and is
tested by `tests/test_release_check.py` against throwaway git repositories (it is outside the
package, so it does not count toward coverage).

`docs/releasing.md` (p21): bump-free versioning from tags; move `[Unreleased]` to `## [X.Y.Z] -
YYYY-MM-DD` in a release PR; dispatch Release with `dry_run: true`, then `false`; what is
published; how to verify provenance (`gh attestation verify oci://ghcr.io/privacyfence/privacyfence-cockpit:X.Y.Z --owner privacyfence`).

`.claude/toolkit.yaml` (p20) gains:

```yaml
ci:
  dispatchable:
    - workflow: image.yml
      use_when: Anything that needs docker build or a running container (the image smoke test)
      notes: >-
        Dispatch on your pushed branch (ref = the branch). It builds amd64 and runs
        scripts/smoke_image.sh, and builds arm64 without pushing. It only becomes dispatchable once
        it is on main; before that it runs on pull requests that touch the image's inputs.
    - workflow: release.yml
      use_when: Checking that a release would pass, never to cut one
      notes: Dispatch on main with dry_run true only. Cutting a release is the maintainer's decision.
```

`docker-compose.yml` (p20), exactly the example in the README (§X): image `:latest`,
`restart: unless-stopped`, `read_only: true`, `tmpfs: [/tmp]`, `ports: ["127.0.0.1:8080:8080"]`,
environment `COCKPIT_PUBLIC_URL` and `COCKPIT_SERVER_URL` with example values, volume
`cockpit-data:/data`, secret `cockpit_master_key` from `./cockpit_master_key`.

### X. Docs (p22)

- `README.md`: what it is; quick start (create the master key: `openssl rand -base64 32 >
  cockpit_master_key && chmod 400 cockpit_master_key && sudo chown 10001:10001 cockpit_master_key`;
  `docker compose up -d`; read the setup code with `docker compose logs cockpit`; open
  `http://127.0.0.1:8080/setup`); "Master key" (losing it loses the stored secrets; back it up
  apart from the volume); remote access through the owner's own TLS reverse proxy with a Caddy
  example (`cockpit.example.com { reverse_proxy 127.0.0.1:8080 }`) and `COCKPIT_PUBLIC_URL`;
  the environment-variable table (§C); links to the guides.
- `docs/user-guide.md`: the first start, connecting the repository (deploy key or PAT, host-key
  confirmation), tokens (scoped tokens only; the lookup chain; `PRIVACYFENCE_TOKEN` override;
  daily checks; "last use / last denial" lives in PrivacyFence Settings), runs and outcomes (the
  outcome table), schedules, the Code page and the update model, notifications (events, formats,
  payload), settings, the CLI reference (§L), backups (`/data` plus the master key),
  troubleshooting (venv gate message, exit codes, `user reset-password`).
- `docs/recipe-repository.md`: layout and ordering (`workflows/<NN-group>/<NN-recipe>/recipe.yaml`,
  `_`/`.` skip, group number optional on the command line), `requirements.txt` (must pin
  privacyfence-sync; `-e ./lib/...` allowed), work folders git-ignored, `cockpit.yaml` reference
  (every key and its validation), live replay (`COCKPIT_REPLAY=1`, the allowlist).
- `docs/migrating-from-the-mac-cockpit.md`: retiring the laptop setup — unload and delete the
  launchd agent (`launchctl bootout gui/$(id -u) <plist>`), move each Keychain token to
  `cockpit token set` as a **new scoped token** (not the old AI-client token), replace the
  hand-maintained tool → rule table with the Permissions page, the SwiftUI app is no longer needed,
  `logs/runs.csv` history stays on the laptop (the cockpit starts a fresh history), the `cockpit
  sync` shared-block convention moves to a script in the recipe repository.
- `docs/reference/cockpit-architecture.md` is left as is (it describes the tool being replaced).

### Rejected alternatives (for the ADRs)

- **Accounts, OIDC, or no auth behind a proxy** (ADR 0002): one owner per container; OIDC and
  multi-user need a user model, roles and an IdP the target owners do not run. Relying on the
  reverse proxy alone fails open when the port is published by mistake. A local scrypt password,
  bootstrapped by a one-time code from the container log, needs nothing outside the container.
- **Plain-text secrets on the volume, or the master key in an environment variable** (ADR 0003):
  a copied volume or backup would hand over every token. An environment variable shows up in
  `docker inspect` and in every child process's environment; a Docker secret file does not.
  Passing the git credential through the environment or argv was rejected for the same reason: it
  is a 0600 file in the tmpfs `/tmp`, removed after each command.
- **Schedules in the database or the UI** (ADR 0004): they would not be reviewed with the recipes
  they run and would be lost with the volume. Only the pause state, which is operational, lives in
  the database.
- **Catching up missed runs** (ADR 0005): after a long outage a burst of stale runs hits the
  sources at once; the recipes are idempotent and diff against the target, so the next scheduled
  run does the work anyway. Missed fires are recorded and notified instead.
- **Auto-pull on every fetch, or a webhook from GitHub** (ADR 0006): untrusted code would start
  running unattended without the owner looking at it; a webhook needs an inbound URL. The cockpit
  fetches, shows the commits, and updates only on request (or before scheduled runs when the owner
  opts in); it never commits, pushes or overwrites tracked changes.
- **A PrivacyFence-style two-workflow release with a `RELEASE_TAG_TOKEN`** (ADR 0007): that chain
  exists because a tag pushed with `GITHUB_TOKEN` starts no workflow. Here the dispatch that cuts
  the tag publishes the image in the same run, so no long-lived secret is needed; a tag pushed by
  hand still publishes through the `push: tags` trigger. Also rejected: `latest`-only tags, an
  unpinned base image, and unhashed dependencies.

## ADRs

- **0002**: One owner per container, with a local scrypt password bootstrapped by a one-time setup
  code from the container log (§W1; rejects accounts, OIDC, proxy-only auth).
- **0003**: Secrets are AES-GCM encrypted on the volume with a key from a Docker-secret file, and
  only the one recipe's token ever reaches a subprocess environment (§S, §R, §G2; rejects plain
  files, an environment-variable key, credentials in env/argv).
- **0004**: Schedules live in `cockpit.yaml` in the recipe repository; only pause state is in the
  database (§E, §Q).
- **0005**: Missed scheduled runs are recorded and notified, never caught up (§Q).
- **0006**: The recipe checkout changes only on the owner's request (or before scheduled runs when
  opted in), fast-forward only, never over tracked changes, and never commits or pushes (§G1).
- **0007**: One multi-arch image on GHCR, built from a digest-pinned base with hash-locked
  dependencies, smoke-tested, published with provenance by the run that cuts the tag (§I).

## Manual steps

Nothing before implementation: every phase builds against fakes and in-process servers. After the
last merge, three checks the CI cannot do, with step-by-step instructions on the
[manual steps page](https://claude.ai/artifact/EBXKfb45hsmFX18ZZjv1j9):

1. Make the GHCR package public after the first release.
2. Have the PrivacyFence org admin enable scoped tokens, and create one scoped token for a test recipe.
3. Run the released image on a real host against the org server: set the password, add the deploy
   key, set the token, preflight, a scheduled run, and a webhook for a deliberately denied step.

## Risks and open questions

- **The privacyfence-sync release.** Steps 2–3 of `manual_after` need a PyPI release of
  privacyfence-sync with D1–D3 of the PrivacyFence plan. Nothing in this plan waits for it: the
  fake implements the options. If a phase finds the fake's contract (§V test fake) disagrees with
  D1–D3 of `docs/sync-scheduling-support-plan.md` on `plan/sync-scheduling-support` (read it with
  `git fetch origin plan/sync-scheduling-support` in a privacyfence clone, or the GitHub tools),
  stop with `status=blocked` and quote both.
- **mcp 2.x API drift.** §T1 was checked against mcp 2.3.0. If `MCPServer`,
  `TransportSecuritySettings` or `streamable_http_client(url, http_client=…)` do not exist as
  written, stop with `status=blocked`; do not mock the MCP client instead.
- **Process groups in tests.** Runner tests start real subprocesses (the fake). Use short sleeps
  and `kill_grace_seconds=0.5`; a test that needs more than 5 s is wrong.
- **SQLite from two processes.** Server and CLI share `cockpit.db` (WAL, busy timeout 5 s). A
  `database is locked` error in a test means a transaction was left open; autocommit single
  statements avoid it.
- **Docker is not available in the cloud container.** p20 and p21 cannot run `docker build`; their
  acceptance checks syntax and the scripts' logic, and the image is proven by `image.yml` on the
  feature PR (it triggers on the paths p20 adds). The final reviewer and the PR's CI catch a broken
  Dockerfile; a phase that edits it must still read it line by line against §I.
- **Coverage gate 95%.** Each phase keeps `python3 -m pytest --cov=privacyfence_cockpit --cov-branch
  --cov-fail-under=95` passing over the whole package. A phase that cannot reach it without a
  pragma not allowed by this plan stops with `status=blocked`.
- **No changelog lines in phases.** All of v1 is one user-visible change; p23 writes the
  `CHANGELOG.md` lines. Phases do not touch `CHANGELOG.md`, so parallel phases cannot conflict there.

## Implementation manifest

```yaml
plan_slug: cockpit-v1
feature_branch: feature/cockpit-v1
max_parallel: 2
manual_steps_artifact: https://claude.ai/artifact/EBXKfb45hsmFX18ZZjv1j9
manual_steps_source: docs/cockpit-v1-plan-manual-steps.html
manual_before: []
manual_after:
  - id: ma1-ghcr-public
    title: Make the ghcr.io/privacyfence/privacyfence-cockpit package public after the first release
    why: A new GHCR package of a private repository starts private, so nobody else can pull the image; only an organization owner can change its visibility.
  - id: ma2-scoped-token
    title: Ask the PrivacyFence org admin to enable scoped tokens, then create one scoped token for a test recipe
    why: The cockpit accepts only pfst_ scoped tokens and the tests fake the server; a real token is needed for the end-to-end check in ma3.
  - id: ma3-real-host-run
    title: Run the released image on a real host against the org server, from password setup to a denied-step webhook
    why: CI proves the image starts and checks a fixture; only a real host shows the deploy key, the token check, preflight, a scheduled unattended run and a webhook against the real PrivacyFence and GitHub. It needs a privacyfence-sync release with run --result-file and permissions.
verify_after_merge:
  - 'python3 -m pytest -q --cov=privacyfence_cockpit --cov-branch --cov-fail-under=95'
  - 'python3 -m mypy'
  - 'bandit -c pyproject.toml -r src'
final_checks:
  - 'docs/cockpit-v1-plan.md and docs/cockpit-v1-plan-manual-steps.html are deleted; grep -rn "cockpit-v1-plan" . --exclude-dir=.git prints nothing'
  - 'git diff --name-only --diff-filter=A origin/main -- docs/adr lists exactly 0002 to 0007; each is Accepted and has a row in docs/adr/README.md''s index'
  - 'CHANGELOG.md has new lines under "## [Unreleased]" and no new version heading'
  - '.claude/toolkit.yaml lists image.yml and release.yml under ci.dispatchable'
  - 'The image.yml run on the feature PR is green (amd64 smoke test and arm64 build)'
  - 'grep -rnE "shell=True" src prints nothing'
phases:
  - id: p1-foundation
    title: Dependencies, core types, environment config, the /data layout and the SQLite schema
    depends_on: []
    complexity: M
    touches:
      - pyproject.toml
      - src/privacyfence_cockpit/models.py
      - src/privacyfence_cockpit/events.py
      - src/privacyfence_cockpit/config.py
      - src/privacyfence_cockpit/paths.py
      - src/privacyfence_cockpit/db.py
      - tests/test_models.py
      - tests/test_events.py
      - tests/test_config.py
      - tests/test_paths.py
      - tests/test_db.py
    brief: |
      Read docs/cockpit-v1-plan.md sections A, B, C and D first; they are the spec.
      Setup: git fetch --unshallow --tags origin; python3 -m venv --upgrade-deps .venv;
      .venv/bin/python -m pip install -e ".[dev,test]" (after step 1, reinstall).
      1. pyproject.toml: set `dependencies` and the `test`/`dev` extras exactly as Design §B. Do not add
         [project.scripts] or package data yet (p13 does).
      2. src/privacyfence_cockpit/models.py: Outcome, OUTCOME_LABELS, SUCCESS_OUTCOMES, NOTIFY_OUTCOMES,
         RESULT_FILE_OUTCOMES, EXIT_CODE_OUTCOMES, Mode, utc_now, iso, parse_iso as §C. iso() requires a
         tz-aware datetime (ValueError otherwise) and converts to UTC.
      3. events.py: EventKind, Event, EventSink (typing.Protocol), NullSink, ListSink as §C.
      4. config.py: ConfigError and EnvConfig.from_environ exactly as the §C table, including popping
         PRIVACYFENCE_TOKEN from the mapping passed in, the secure_cookies property and a __repr__ that
         never shows the token.
      5. paths.py: DataPaths as §C; ensure() uses mkdir(parents=True, exist_ok=True) then chmod 0o700.
      6. db.py: Database as §D: connection pragmas, the lock, migrate() creating schema version 1 verbatim
         (every table and index), DatabaseError for a newer schema, and the setting helpers.
      7. Tests (plain pytest functions, tmp_path, no network): tests/test_models.py (every RESULT_FILE and
         EXIT_CODE mapping, iso/parse_iso round trip, iso rejects naive datetimes); tests/test_events.py
         (ListSink records); tests/test_config.py (defaults, every variable, PRIVACYFENCE_TOKEN popped from
         the mapping, repr has no token, both ConfigError texts, secure_cookies); tests/test_paths.py (ensure
         creates the folders with mode 0o700, repo is not created); tests/test_db.py (migrate twice is a no-op,
         user_version is 1, every table exists, settings get/set/delete, newer schema raises DatabaseError,
         two Database objects on one file see each other's writes).
      8. Run python3 -m pytest -q --cov=privacyfence_cockpit --cov-branch --cov-fail-under=95, ruff check .,
         python3 -m mypy, bandit -c pyproject.toml -r src. No CHANGELOG line (p23 writes them).
      Stop condition: if a version in §B cannot be installed from PyPI, stop with status=blocked and quote pip's error.
    acceptance:
      - 'python3 -m pytest tests/test_models.py tests/test_events.py tests/test_config.py tests/test_paths.py tests/test_db.py -q passes'
      - 'grep -n "mcp>=2.3,<3" pyproject.toml matches'
      - 'grep -n "types-croniter" pyproject.toml matches'
      - 'python3 -m mypy passes'
      - 'python3 -m pytest -q --cov=privacyfence_cockpit --cov-branch --cov-fail-under=95 passes'

  - id: p2-recipes-config
    title: Recipe discovery and selection, cockpit.yaml, and effective settings
    depends_on: [p1-foundation]
    complexity: M
    touches:
      - src/privacyfence_cockpit/recipes.py
      - src/privacyfence_cockpit/cockpit_yaml.py
      - src/privacyfence_cockpit/effective.py
      - tests/test_recipes.py
      - tests/test_cockpit_yaml.py
      - tests/test_effective.py
    brief: |
      Read docs/cockpit-v1-plan.md §E (and §C for EnvConfig, §D for settings keys) and
      docs/reference/cockpit-architecture.md §5 (layout and ordering).
      1. recipes.py: Recipe, DiscoveryError, SelectionError, discover, group_names, select exactly as §E,
         including the error texts.
      2. cockpit_yaml.py: Schedule, CockpitConfig, CockpitConfigError, load_cockpit_yaml, selector_problems as
         §E. Every validation rule in §E is one test. Use yaml.safe_load, zoneinfo.ZoneInfo,
         croniter.croniter.is_valid.
      3. effective.py: Effective and effective() with the precedence in §E; bad numeric settings fall back
         to the defaults (90, 15).
      4. Tests: tests/test_recipes.py builds workflows/ trees in tmp_path (numbered groups and recipes, a
         `_disabled` and a `.hidden` folder, a folder without recipe.yaml, duplicate group short names) and
         covers every selector form in §E (with and without group number, workflows/ prefix, /recipe.yaml
         suffix, group only, unknown, dedup, run order regardless of selector order).
         tests/test_cockpit_yaml.py covers the sample from §E, a missing file, and each invalid case.
         tests/test_effective.py covers every precedence branch.
      5. Run the definition-of-done commands (as p1 step 8). No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_recipes.py tests/test_cockpit_yaml.py tests/test_effective.py -q passes'
      - 'python3 -m pytest tests/test_recipes.py -q --cov=privacyfence_cockpit.recipes --cov-branch --cov-report=term-missing reports 100% for recipes.py'
      - 'python3 -m mypy passes'

  - id: p3-secrets-tokens
    title: Master key, encrypted secret store, token names and the lookup chain
    depends_on: [p2-recipes-config]
    complexity: M
    touches:
      - src/privacyfence_cockpit/crypto.py
      - src/privacyfence_cockpit/secrets_store.py
      - src/privacyfence_cockpit/tokens.py
      - tests/test_crypto.py
      - tests/test_secrets_store.py
      - tests/test_tokens.py
    brief: |
      Read docs/cockpit-v1-plan.md §S (and §E for Recipe).
      1. crypto.py: MasterKeyError, SecretDecryptError, missing_key_message, load_master_key, encrypt,
         decrypt exactly as §S, using cryptography.hazmat.primitives.ciphers.aead.AESGCM. The key never
         appears in a message or repr.
      2. secrets_store.py: SecretStore as §S: file naming, 0o600 files written via .tmp + os.replace,
         AAD binding, names(), verify_canary() and its message.
      3. tokens.py: DEFAULT_TOKEN, TOKEN_PREFIX, TokenNameError, TokenValueError, valid_token_names,
         check_token_name (difflib suggestion, exact text), clean_token_value, ResolvedToken (value not in
         repr), TokenResolver.resolve with the env > recipe > group > default > none chain and the source
         strings in §S, resolve_name.
      4. Tests: tests/test_crypto.py (round trip; wrong AAD and tampered blob raise SecretDecryptError;
         missing/short/non-base64 key messages; the missing-key message equals §S verbatim);
         tests/test_secrets_store.py (set/get/delete/names/has; files are 0o600 and hold no plaintext;
         a file copied to another name fails to decrypt; canary created then verified; a different key
         raises MasterKeyError); tests/test_tokens.py (valid names order; suggestion text for "imprts";
         no suggestion clause for "zzz"; pfst_ rule; every resolve branch; env wins).
      5. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_crypto.py tests/test_secrets_store.py tests/test_tokens.py -q passes'
      - 'grep -n "AESGCM" src/privacyfence_cockpit/crypto.py matches'
      - 'python3 -m mypy passes'
      - 'bandit -c pyproject.toml -r src passes'

  - id: p4-git-manager
    title: The git manager over real git, tested against bare repositories
    depends_on: [p1-foundation]
    complexity: M
    touches:
      - src/privacyfence_cockpit/gitrepo.py
      - tests/gitutil.py
      - tests/test_gitrepo.py
    brief: |
      Read docs/cockpit-v1-plan.md §G1 (and §C, §D for DataPaths, settings, events).
      1. tests/gitutil.py: helpers `make_remote(tmp_path: Path, files: dict[str, str], branch: str = "main")
         -> Path` (creates a work repo, commits with `-c user.name=test -c user.email=test@example.invalid`,
         returns a bare clone `remote.git`) and `push_commit(remote: Path, files: dict[str, str], message: str)
         -> str` (clones to a temp dir, writes files, commits, pushes, returns the sha).
      2. src/privacyfence_cockpit/gitrepo.py: GitError, Commit, FileChange, CommitDetail, GitStatus,
         Credentials, no_credentials, GitManager with every method and message in §G1. Subprocess calls use
         subprocess.run with a fixed argv list, the env in §G1, capture_output, text, timeout=300,
         check=False; the git lock uses fcntl.flock on paths.git_lock. GitManager takes the clock for the
         24-hour fetch-failure rule.
      3. tests/test_gitrepo.py with bare remotes in tmp_path (URL = the bare repo path): configure
         validation; clone; status (head, remote tip, new_commits after a push + fetch); log order and fields;
         show (message, numstat, binary file → None counts); diff (only listed paths; unknown sha and a
         non-ancestor sha → GitError("unknown commit")); update fast-forwards; update refuses when pinned,
         when a tracked file is modified (an untracked file does not count), and when the remote was rewritten
         (force-push a different history); pin and track; fetch failure (remote path deleted) sets
         git.last_fetch_error and git.failing_since and emits GIT_FETCH_FAILING only once the clock is
         more than 24 h after failing_since (use a fake clock and events.ListSink); a later successful fetch
         clears the state and the notice.
      4. Definition-of-done commands. No CHANGELOG line.
      Stop condition: if `git` is older than 2.28 (no `init -b`) in the container, stop with status=blocked.
    acceptance:
      - 'python3 -m pytest tests/test_gitrepo.py -q passes'
      - 'python3 -m pytest tests/test_gitrepo.py -q --cov=privacyfence_cockpit.gitrepo --cov-branch --cov-report=term-missing reports at least 95% for gitrepo.py'
      - 'grep -nE "\"(commit|push|reset|clean|stash)\"" src/privacyfence_cockpit/gitrepo.py prints nothing'
      - 'python3 -m mypy passes'

  - id: p5-git-auth
    title: Deploy key, PAT, SSH host keys and per-command git credentials
    depends_on: [p3-secrets-tokens, p4-git-manager]
    complexity: M
    touches:
      - src/privacyfence_cockpit/gitauth.py
      - tests/test_gitauth.py
    brief: |
      Read docs/cockpit-v1-plan.md §G2.
      1. src/privacyfence_cockpit/gitauth.py: GITHUB_KNOWN_HOSTS (copy the three lines from §G2 exactly),
         HostKey (fingerprint, known_hosts_line), generate_deploy_key, set_pat, host_of, write_builtin_known_hosts,
         is_known, scan_host, stage_host_keys, confirm_host_keys, credentials_factory, all as §G2. Use
         cryptography's Ed25519PrivateKey and serialization.PrivateFormat.OpenSSH. The askpass script text is
         exactly the one in §G2.
      2. tests/test_gitauth.py:
         - the three GITHUB_KNOWN_HOSTS fingerprints equal SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU,
           SHA256:p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM and SHA256:uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s;
         - generate_deploy_key: public key starts "ssh-ed25519 " and ends " privacyfence-cockpit"; the private
           key is only in the secret store;
         - host_of for git@host:path, ssh://user@host:2222/path, https URLs and local paths;
         - scan_host with an injected `run` returning canned ssh-keyscan output (no network); bad host names rejected;
         - stage/confirm writes known_hosts lines (bracketed form for a non-22 port); is_known;
         - credentials_factory: for ssh yields GIT_SSH_COMMAND naming a key file that exists with mode 0o600 inside
           the context and is gone after it; for https the askpass script prints x-access-token for "Username for ..."
           and the PAT otherwise (run it with subprocess) and the folder is removed afterwards; missing key / PAT
           raise the §G2 GitError texts; the yielded env never contains the PAT or the private key text;
         - an end-to-end https clone is not tested (no network).
      3. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_gitauth.py -q passes'
      - 'grep -n "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" src/privacyfence_cockpit/gitauth.py matches'
      - 'python3 -m mypy passes'
      - 'bandit -c pyproject.toml -r src passes'

  - id: p6-venvs
    title: The recipe environment manager, its version gate, and the fake privacyfence-sync
    depends_on: [p1-foundation]
    complexity: M
    touches:
      - src/privacyfence_cockpit/venvs.py
      - tests/fakesync.py
      - tests/test_fakesync.py
      - tests/test_venvs.py
    brief: |
      Read docs/cockpit-v1-plan.md §V, including "Test fake", and the result-file format in the Goal/Current
      state sections (fields version, recipe, outcome, exit_code, stopped_at_step, started_at, finished_at,
      privacyfence_sync_version).
      1. tests/fakesync.py: make_fake_sync exactly as §V, plus `install_fake_venv(paths: DataPaths, db: Database,
         **fake_kwargs) -> Path` that creates paths.venvs/"fake"/bin/privacyfence-sync via make_fake_sync, writes
         paths.venvs/"fake"/".cockpit-ready" and sets the setting venv.current to "fake". tests/test_fakesync.py
         runs the generated script for each subcommand and checks output, exit codes, the result file and the
         record file.
      2. src/privacyfence_cockpit/venvs.py: GATE_MESSAGE (verbatim), VenvError, VenvResult, requirement_files,
         pin_problem, venv_hash, VenvManager (current, ensure, sync_bin, pruning) as §V. Subprocesses go through
         the injected `run` callable with fixed argv lists.
      3. tests/test_venvs.py (no pip, no network): inject a `run` fake that, for `-m venv <dir>`, creates
         <dir>/bin and copies a fake made by make_fake_sync there, and for `pip install` returns a chosen code.
         Cover: requirement_files with -e ./lib/x and an outside path; pin_problem for ==, a local pyproject named
         privacyfence-sync / privacyfence_sync, a missing pin; venv_hash changes when requirements.txt, a referenced
         pyproject.toml or the Python version changes and not otherwise; ensure builds once then reuses; the gate
         refuses a fake without --result-file and one without permissions (GATE_MESSAGE, previous venv kept,
         VENV_FAILED emitted once per hash via ListSink, venv.last_error set); pip failure message; pruning keeps
         current + two previous.
      4. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_fakesync.py tests/test_venvs.py -q passes'
      - 'grep -n "pin the first version with run --result-file and permissions" src/privacyfence_cockpit/venvs.py matches'
      - 'python3 -m mypy passes'

  - id: p7-runner
    title: Run history and the runner (subprocess, process group, stop, timeout, outcome, log)
    depends_on: [p3-secrets-tokens, p4-git-manager, p6-venvs]
    complexity: M
    touches:
      - src/privacyfence_cockpit/history.py
      - src/privacyfence_cockpit/runner.py
      - tests/test_history.py
      - tests/test_runner.py
    brief: |
      Read docs/cockpit-v1-plan.md §R (up to, not including, §R2) and §C, §D, §S, §V.
      1. history.py: RunRow, HistoryFilter, History with every method in §R (not export_csv/prune_logs; p8).
      2. runner.py: RunResult and Runner.run / Runner.stop exactly as §R steps 1–9, for modes attended,
         unattended, preflight and check. Mode replay raises NotImplementedError("p8") for now (p8 replaces it;
         cover it with one test that p8 will rewrite). Use asyncio.create_subprocess_exec with
         start_new_session=True; read stdout line by line; enforce the timeout with asyncio.wait_for around the
         read-and-wait coroutine; terminate with os.killpg SIGTERM, wait kill_grace_seconds, then SIGKILL.
         The log header/footer lines are verbatim from §R.
      3. tests/test_history.py: start/finish/duration, query filters and order, count, last_by_recipe,
         record_missed, request_stop, mark_orphans (dead pid → failed with the note; live pid left alone; use
         os.getpid() of a sleeping child for "live").
      4. tests/test_runner.py with tests/fakesync.py's install_fake_venv and record=, a real git checkout from
         tests/gitutil.py (or git init in tmp_path) and a recipe folder: argv for each mode; env is exactly the §R
         step 4 keys (assert no other key, token present only when stored, PRIVACYFENCE_MCP_URL only when a server
         URL is effective); cwd is the recipe folder; outcome from the result file for every outcome string; missing
         result file → failed; preflight/check outcome from exit code; timeout → timed_out (construct the Runner with
         timeout_unit_seconds=0.05 and an Effective with timeout_minutes=1, and a fake that sleeps 2 s); stop() from a second task →
         stopped, process group gone; no venv → failed with the §R step 2 line and no subprocess; header/footer
         contents; the token value never appears in the log file.
         Use kill_grace_seconds=0.5 and fake sleeps of at most 2 s.
      5. Definition-of-done commands. No CHANGELOG line.
      Stop condition: if a timeout or stop test leaves a process behind (check with os.killpg(pgid, 0) in the test),
      fix the runner, never the test; if that cannot be made reliable, stop with status=blocked.
    acceptance:
      - 'python3 -m pytest tests/test_history.py tests/test_runner.py -q passes'
      - 'python3 -m pytest tests/test_runner.py -q --cov=privacyfence_cockpit.runner --cov-branch --cov-report=term-missing reports at least 95% for runner.py'
      - 'grep -n "start_new_session=True" src/privacyfence_cockpit/runner.py matches'
      - 'python3 -m mypy passes'

  - id: p8-run-modes
    title: Live replay, cockpit-side checks, permissions collection and merge, CSV export and log retention
    depends_on: [p7-runner]
    complexity: M
    touches:
      - src/privacyfence_cockpit/replay.py
      - src/privacyfence_cockpit/checks.py
      - src/privacyfence_cockpit/permissions.py
      - src/privacyfence_cockpit/runner.py
      - src/privacyfence_cockpit/history.py
      - tests/test_replay.py
      - tests/test_checks.py
      - tests/test_permissions.py
      - tests/test_runner.py
      - tests/test_history.py
    brief: |
      Read docs/cockpit-v1-plan.md §R2, including the permissions --json shape it quotes.
      1. replay.py: REPLAY_ALLOWLIST (the ten tools in §R2, exactly), REPLAY_FILE, cut_replay.
      2. checks.py: cockpit_problems with the §R2 texts.
      3. runner.py: add `command_env(recipe) -> tuple[Path, dict[str, str]] | None` (the venv and the §R step 4
         env, reused by run), replay mode (§R2: cut, run unattended with COCKPIT_REPLAY=1, delete the file in
         finally, the "nothing to replay" case), and check mode's cockpit problems in the log and outcome.
      4. permissions.py: NO_SUGGESTIONS, PermissionsReport, NeededRule, MergedSet, collect (taking the Runner),
         merge, rule_text, render_recipe, render_merged with the exact texts of §R2.
      5. history.py: CSV_COLUMNS, export_csv, prune_logs.
      6. Tests: tests/test_replay.py (cut at the first non-allowlisted tool; run/template steps before it kept; all
         read-only recipe kept whole; first step writes → None and no file); tests/test_checks.py (ignored work
         dir ok; not ignored; default "work"; outside the folder; not a git repo); tests/test_permissions.py
         (collect with the fake's permissions_json, an exit 4, and no venv; merge union and by group with duplicate
         rules across recipes, value None rules, suggested_rules null → unsuggested; exact rendered lines);
         extend tests/test_runner.py (replay argv and env, file deleted after a failing run, check mode problem →
         recipe_error); extend tests/test_history.py (CSV header and rows; prune_logs removes old day folders and
         old venv logs only).
      7. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_replay.py tests/test_checks.py tests/test_permissions.py tests/test_runner.py tests/test_history.py -q passes'
      - 'python3 -c "from privacyfence_cockpit.replay import REPLAY_ALLOWLIST as A; assert len(A) == 10 and \"grist_get_records\" in A" passes'
      - 'grep -n "update PrivacyFence to see suggestions" src/privacyfence_cockpit/permissions.py matches'
      - 'grep -n "NotImplementedError" src/privacyfence_cockpit/runner.py prints nothing'
      - 'python3 -m mypy passes'

  - id: p9-queue-scheduler
    title: Run lock, job queue and worker, and the scheduler with an injectable clock
    depends_on: [p8-run-modes]
    complexity: M
    touches:
      - src/privacyfence_cockpit/runlock.py
      - src/privacyfence_cockpit/jobs.py
      - src/privacyfence_cockpit/scheduler.py
      - tests/test_runlock.py
      - tests/test_jobs.py
      - tests/test_scheduler.py
    brief: |
      Read docs/cockpit-v1-plan.md §Q (and §R, §G1, §V for what the worker calls).
      1. runlock.py: RunLock as §Q (fcntl.flock LOCK_EX, LOCK_NB for try_acquire).
      2. jobs.py: JobKind, Job, JobSummary, JobQueue (dedupe of schedule triggers over queued + running; cancel
         queued only; an asyncio.Condition or asyncio.Queue plus a list for snapshots), Worker.execute and
         run_forever exactly as §Q, including auto-update before scheduled runs, venvs.ensure, selection errors
         recorded as one recipe_error row, repeat-while-ok, stop_on_error, and RUN_FAILED dedupe per (recipe,
         outcome) per job for schedule triggers only.
      3. scheduler.py: TICK_SECONDS, MISSED_AFTER_SECONDS, next_fire, fires_between, schedules_for, Scheduler
         (is_paused/set_paused on schedule_state, tick, run_forever) exactly as §Q, including the missed note text.
      4. Tests:
         - tests/test_runlock.py: a second RunLock on the same file fails try_acquire while the first holds it
           (use a subprocess holding the lock to test across processes).
         - tests/test_jobs.py: use a real Runner with tests/fakesync.py; one recipe ok; repeat 3 with ok → 3 runs;
           repeat 3 with no_change → 1 run; stop_on_error skips later recipes and without it they run; RUN_FAILED
           emitted for a schedule trigger once per (recipe, outcome) and never for "manual"; unknown selector →
           recipe_error row with the SelectionError note; dedupe of a schedule job already queued and already
           running; cancel; GIT_UPDATE/GIT_PIN/GIT_TRACK/GIT_CLONE against tests/gitutil.py remotes; a GitError
           becomes JobSummary.error; auto_update before a scheduled run fast-forwards and is skipped when pinned.
         - tests/test_scheduler.py with a fake clock (a list-backed callable) and Europe/Budapest: first tick ever
           fires nothing and misses nothing; a fire inside the window submits one job with the schedule's repeat and
           stop_on_error; paused schedule neither fires nor is missed; restart after 3 days → one missed row and one
           SCHEDULE_MISSED with the note's count, and no job; two fires in one tick → one job; DST change day
           (2026-10-25 in Europe/Budapest) fires once at the local time; cockpit.yaml error → no jobs, last_tick moves.
      5. Definition-of-done commands. No CHANGELOG line.
      Stop condition: if a worker test needs real time beyond a few seconds, the design is being bypassed; stop with status=blocked.
    acceptance:
      - 'python3 -m pytest tests/test_runlock.py tests/test_jobs.py tests/test_scheduler.py -q passes'
      - 'grep -n "MISSED_AFTER_SECONDS = 90" src/privacyfence_cockpit/scheduler.py matches'
      - 'python3 -m mypy passes'

  - id: p10-notifications
    title: Webhook notifications in generic JSON, Slack and ntfy formats
    depends_on: [p3-secrets-tokens]
    complexity: M
    touches:
      - src/privacyfence_cockpit/notify.py
      - tests/test_notify.py
    brief: |
      Read docs/cockpit-v1-plan.md §N (and §C for Event).
      1. notify.py: WebhookFormat, Webhook, message_for, link_for, build_request, Notifier exactly as §N, with the
         exact message texts. emit() appends to a queue.SimpleQueue so it is safe from threads; deliver_pending
         drains it and POSTs with httpx2.AsyncClient(timeout=10, transport=self._transport).
      2. tests/test_notify.py with httpx2.MockTransport capturing requests (no network): add validates name and URL;
         the URL is stored encrypted (not in cockpit.db: grep the db file bytes); list/delete; every EventKind message;
         link_for with and without a public URL; the three body formats and ntfy headers; a 500 and a transport
         exception set last_status ("HTTP 500", "error: ConnectError") and do not raise; caplog never contains the URL;
         send_test returns the status text; emit from a thread then deliver_pending sends it.
      3. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_notify.py -q passes'
      - 'python3 -m pytest tests/test_notify.py -q --cov=privacyfence_cockpit.notify --cov-branch --cov-report=term-missing reports 100% for notify.py'
      - 'python3 -m mypy passes'

  - id: p11-token-check
    title: Token checks through privacyfence_status over MCP, with expiry notices
    depends_on: [p3-secrets-tokens]
    complexity: S
    touches:
      - src/privacyfence_cockpit/token_check.py
      - tests/test_token_check.py
    brief: |
      Read docs/cockpit-v1-plan.md §T and §T1, and privacyfence/privacyfence sync/src/privacyfence_sync/client.py
      lines 65-110 (the pattern to copy).
      1. token_check.py: STATUS_REASON, TokenState, TokenCheck, fetch_status, classify, TokenChecker exactly as §T,
         including the error texts and the notice keys token:<name>:invalid and token:<name>:<threshold>.
      2. tests/test_token_check.py using the §T1 in-process MCPServer (no network): ok, expiring_soon, days_left 0,
         no token key (not scoped), a 401 ASGI app (refused), a transport raising httpx2.ConnectError (unreachable),
         no server URL; TokenChecker with a fake `fetch` for the notice logic: 13 days → one TOKEN_EXPIRING with
         days_left 13 and key 14; a second check at 13 days → nothing; 6 days → key 7; refused twice → one
         TOKEN_INVALID; ok after refused clears the invalid key; forget() deletes the row and the notices;
         check_all sets tokens.last_check_at.
      3. Definition-of-done commands. No CHANGELOG line.
      Stop condition: if MCPServer, TransportSecuritySettings or streamable_http_client do not exist as §T1 shows in
      the installed mcp, stop with status=blocked (do not mock the MCP client).
    acceptance:
      - 'python3 -m pytest tests/test_token_check.py -q passes'
      - 'grep -n "streamable_http_client" src/privacyfence_cockpit/token_check.py matches'
      - 'python3 -m mypy passes'

  - id: p12-services
    title: The Cockpit service container and its background loops
    depends_on: [p5-git-auth, p9-queue-scheduler, p10-notifications, p11-token-check]
    complexity: M
    touches:
      - src/privacyfence_cockpit/services.py
      - tests/cockpit_factory.py
      - tests/test_services.py
    brief: |
      Read docs/cockpit-v1-plan.md §W2 (the services.py part) and §W0's test-helper line.
      1. services.py: Cockpit with every attribute and method in §W2. open() does the start-up sequence in §W2 in
         that order and wires notifier as the EventSink of git, venvs, scheduler, worker and token_checker; the git
         manager gets gitauth.credentials_factory. start() creates the six loops (worker, scheduler, notifier, fetch,
         tokens, retention) as asyncio tasks; each loop catches and logs exceptions and keeps going; stop() cancels
         and awaits them. Loop intervals are attributes (`fetch_poll_seconds`, `token_poll_seconds`,
         `retention_poll_seconds`) so tests can shorten them.
      2. tests/cockpit_factory.py: make_cockpit as §W0.
      3. tests/test_services.py: open() on an empty tmp data dir creates the layout, the canary and the schema;
         open() without a key file raises MasterKeyError with the §S message; with another key raises the canary
         MasterKeyError; recipes() and config_or_default() on a repo with a broken cockpit.yaml; effective(); start()
         then stop() leaves no task running; the fetch loop calls git.fetch when cloned (monkeypatch); the token loop
         calls check_all when last_check_at is 25 h old and not when 1 h old.
      4. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_services.py -q passes'
      - 'python3 -m pytest -q --cov=privacyfence_cockpit --cov-branch --cov-fail-under=95 passes'
      - 'python3 -m mypy passes'

  - id: p13-web-shell
    title: Starlette app shell, render helpers, static files, health check, banners and cockpit serve
    depends_on: [p12-services]
    complexity: M
    touches:
      - pyproject.toml
      - src/privacyfence_cockpit/cli.py
      - src/privacyfence_cockpit/web/__init__.py
      - src/privacyfence_cockpit/web/app.py
      - src/privacyfence_cockpit/web/render.py
      - src/privacyfence_cockpit/web/context.py
      - src/privacyfence_cockpit/web/server.py
      - src/privacyfence_cockpit/web/routes_*.py
      - src/privacyfence_cockpit/web/templates/base.html
      - src/privacyfence_cockpit/web/templates/_macros.html
      - src/privacyfence_cockpit/web/templates/home_placeholder.html
      - src/privacyfence_cockpit/web/static/**
      - tests/webclient.py
      - tests/test_web_shell.py
      - tests/test_cli_serve.py
    brief: |
      Read docs/cockpit-v1-plan.md §W0, §W2 (app.py, server.py, context.py, base.html, static, /healthz) and §W3's
      cockpit.js paragraph.
      1. pyproject.toml: add [project.scripts] cockpit = "privacyfence_cockpit.cli:main" and package data
         ["py.typed", "web/templates/*.html", "web/static/*"].
      2. Static files in src/privacyfence_cockpit/web/static/:
         - htmx.min.js: `curl -sSfL -o /tmp/htmx.tgz https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.4.tgz`,
           check `sha256sum /tmp/htmx.tgz` is a34988d1b9f005a458d593aa8d7486537cb878b8ac3b82703db1268983123ecc,
           extract package/dist/htmx.min.js, check its sha256 is
           e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447, copy it unchanged.
         - cockpit.css with every class listed in §W0 and §W2 (light palette, dark under prefers-color-scheme).
         - cockpit.js: the §W3 log streamer (EventSource, approval-link lines, no innerHTML).
      3. web/render.py, web/context.py (Banner, banners() with the eight banners in §W2 order and texts), web/app.py
         (create_app, the Jinja environment with the filters in §W0, StaticFiles, GET /healthz, a temporary GET /
         that renders home_placeholder.html — p15 replaces it; the routes_* stubs listed in §W0), web/server.py
         (serve, with the one allowed pragma), templates base.html (nav, banners, flash, the htmx-config meta, body
         hx-headers with the csrf token, scripts htmx.min.js and cockpit.js, no inline script/style) and _macros.html.
         Create every stub module routes_auth.py, routes_runs.py, routes_schedules.py, routes_code.py, routes_tokens.py,
         routes_permissions.py, routes_notifications.py, routes_settings.py with `ROUTES: list[BaseRoute] = []`.
      4. cli.py: main(argv) with argparse prog "cockpit" and the `serve` subcommand calling web.server.serve; unknown
         subcommands exit 1 via argparse's SystemExit handled like privacyfence-sync's cli.main.
      5. tests/webclient.py: client(cockpit) per §W0. tests/test_web_shell.py: /healthz 200 and 503 (closed db); static
         htmx.min.js served and its sha256 equals the value above; every banner condition renders its text; flash
         cookie round trip; filters; CSP header absent is fine here (p14 adds it). tests/test_cli_serve.py: serve with
         a missing master key returns 2 and prints the §S message (monkeypatch uvicorn so nothing binds).
      6. Definition-of-done commands. No CHANGELOG line.
      Stop condition: if the npm tarball's sha256 differs, stop with status=blocked; never vendor an unverified file.
    acceptance:
      - 'python3 -m pytest tests/test_web_shell.py tests/test_cli_serve.py -q passes'
      - 'sha256sum src/privacyfence_cockpit/web/static/htmx.min.js prints e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447'
      - 'grep -rn "<script>" src/privacyfence_cockpit/web/templates prints nothing'
      - 'grep -n "innerHTML" src/privacyfence_cockpit/web/static/cockpit.js prints nothing'
      - 'python3 -m mypy passes'

  - id: p14-web-auth
    title: Password setup with a one-time code, login, sessions, CSRF, throttling and security headers
    depends_on: [p13-web-shell]
    complexity: M
    worker_model: opus
    worker_model_reason: Security-sensitive authentication code (password hashing, session and CSRF handling, the setup-code bootstrap and throttling); a mistake here is the trust boundary of the whole container, and the review of edge cases benefits from the stronger model.
    touches:
      - src/privacyfence_cockpit/web/auth.py
      - src/privacyfence_cockpit/web/routes_auth.py
      - src/privacyfence_cockpit/web/app.py
      - src/privacyfence_cockpit/web/server.py
      - src/privacyfence_cockpit/web/templates/login.html
      - src/privacyfence_cockpit/web/templates/setup.html
      - src/privacyfence_cockpit/cli.py
      - tests/webclient.py
      - tests/test_web_auth.py
      - tests/test_cli_user.py
    brief: |
      Read docs/cockpit-v1-plan.md §W1 completely, and §W0.
      1. web/auth.py: hash_password, verify_password, new_setup_code, check_setup_code, sessions (create, lookup,
         delete_all_except, expiry), LoginThrottle, the AuthMiddleware and SecurityHeadersMiddleware exactly as §W1
         (pure ASGI or BaseHTTPMiddleware; set request.state.csrf_token for authenticated requests).
      2. web/routes_auth.py: ROUTES for GET/POST /setup, GET/POST /login, POST /logout with the exact messages.
         Templates login.html and setup.html extend base.html.
      3. web/app.py: add the two middlewares (security headers outermost).
      4. web/server.py: on start with no password, issue a setup code and log the one §W1 WARNING line.
      5. cli.py: `user reset-password` per §W1 (prints the code; never logs it elsewhere).
      6. tests/webclient.py: add logged_in(cockpit) per §W0.
      7. tests/test_web_auth.py: hash format and verify; wrong password; GET / redirects to /setup with no password and
         to /login after; setup with wrong code, mismatch, short password, success (code consumed, session cookie set
         with HttpOnly, SameSite=Strict, Secure only when COCKPIT_PUBLIC_URL is https); /setup after a password → 303
         /login; POST without or with a wrong CSRF token → 403 with the §W1 text; login throttle with a fake clock
         (5 failures then lockout seconds 30, 60, … capped at 900; success resets); session expiry after 7 days; logout
         deletes the session; security headers on a page and on /healthz; /healthz and /static need no login; the setup
         code appears in caplog exactly once and nowhere in responses except as typed. tests/test_cli_user.py:
         reset-password clears the hash and every session and prints a code that then works on /setup.
      8. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_web_auth.py tests/test_cli_user.py -q passes'
      - 'grep -n "n=2\*\*15\|n=32768" src/privacyfence_cockpit/web/auth.py matches'
      - 'grep -n "compare_digest" src/privacyfence_cockpit/web/auth.py matches'
      - 'python3 -m mypy passes'
      - 'bandit -c pyproject.toml -r src passes'

  - id: p15-web-runs
    title: Overview, job and run pages with live logs, history with filters, and runs.csv
    depends_on: [p14-web-auth]
    complexity: M
    touches:
      - src/privacyfence_cockpit/web/routes_runs.py
      - src/privacyfence_cockpit/web/templates/overview.html
      - src/privacyfence_cockpit/web/templates/job.html
      - src/privacyfence_cockpit/web/templates/job_rows.html
      - src/privacyfence_cockpit/web/templates/run.html
      - src/privacyfence_cockpit/web/templates/history.html
      - src/privacyfence_cockpit/web/templates/home_placeholder.html
      - src/privacyfence_cockpit/web/app.py
      - tests/test_web_runs.py
    brief: |
      Read docs/cockpit-v1-plan.md §W3 and §W0.
      1. web/routes_runs.py: every route in the §W3 table, using render/redirect from web/render.py and the queue,
         history and runner on request.app.state.cockpit. The SSE endpoint is a StreamingResponse over an async
         generator; poll interval and ping interval are module constants (LOG_POLL_SECONDS = 0.5, PING_SECONDS = 15)
         that tests monkeypatch.
      2. Templates overview.html, job.html, job_rows.html, run.html, history.html (use _macros.html and only §W0 classes).
      3. web/app.py: remove the temporary GET / and delete home_placeholder.html (GET / now comes from routes_runs).
      4. tests/test_web_runs.py with logged_in(): overview lists groups and recipes in run order with chips, last run and
         next fire; POST /run with no recipe flashes the §W3 text; with recipes submits one Job with the selectors and
         mode and redirects to /jobs/<id>; job rows fragment; cancel; run page shows token source and never a token
         value; SSE stream of a finished run yields every log line and an end event; a running run's stream picks up
         appended lines; a deleted log yields the retention line; stop calls runner.stop; history filters each narrow
         the rows and pagination works; runs.csv headers equal CSV_COLUMNS; every POST without CSRF is 403.
      5. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_web_runs.py -q passes'
      - 'test ! -e src/privacyfence_cockpit/web/templates/home_placeholder.html'
      - 'python3 -m mypy passes'

  - id: p16-web-schedules-code
    title: Schedules page and the Code page (commit browser, update model, credentials, host keys)
    depends_on: [p14-web-auth]
    complexity: M
    touches:
      - src/privacyfence_cockpit/web/routes_schedules.py
      - src/privacyfence_cockpit/web/routes_code.py
      - src/privacyfence_cockpit/web/templates/schedules.html
      - src/privacyfence_cockpit/web/templates/code.html
      - src/privacyfence_cockpit/web/templates/commit.html
      - src/privacyfence_cockpit/web/templates/diff.html
      - tests/test_web_schedules.py
      - tests/test_web_code.py
    brief: |
      Read docs/cockpit-v1-plan.md §W4, §W4a, §G1, §G2 and §W0.
      1. web/routes_schedules.py: the §W4 schedule routes and texts.
      2. web/routes_code.py: every route in the §W4 Code table and the §W4a credentials box; job submissions use the
         JobKinds of §Q; Fetch now runs git.fetch via asyncio.to_thread in a background task stored on app.state so
         tests can await it.
      3. Templates schedules.html, code.html, commit.html, diff.html (escaped; add/del spans).
      4. Tests with tests/gitutil.py remotes and logged_in():
         tests/test_web_schedules.py: list with next fire and paused state; pause/resume persist; Run now submits a
         manual job with the schedule's selectors; unknown name 404; cockpit.yaml error shown.
         tests/test_web_code.py: setup form when unconfigured; configure + clone job; commit list marks checkout and
         remote tip; commit detail and diff fragment (escaping of "<script>" inside a diff); unknown sha 404; update,
         checkout and track submit the right jobs; pinned banner text; ssh key generation and the confirm rule for
         replacing it; the GitHub deploy-key link for a github.com URL; PAT form never echoes the value; host-key scan
         with a monkeypatched scan_host lists fingerprints and confirm writes known_hosts; Clone disabled until the
         credential exists.
      5. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_web_schedules.py tests/test_web_code.py -q passes'
      - 'python3 -m mypy passes'

  - id: p17-web-tokens-permissions
    title: Tokens page and Permissions page
    depends_on: [p14-web-auth]
    complexity: M
    touches:
      - src/privacyfence_cockpit/web/routes_tokens.py
      - src/privacyfence_cockpit/web/routes_permissions.py
      - src/privacyfence_cockpit/web/templates/tokens.html
      - src/privacyfence_cockpit/web/templates/permissions.html
      - tests/test_web_tokens.py
      - tests/test_web_permissions.py
    brief: |
      Read docs/cockpit-v1-plan.md §W5, §S, §T, §R2 (permissions) and §W0.
      1. web/routes_tokens.py: the §W5 token routes; set uses check_token_name, clean_token_value, store.set,
         token_checker.forget and check_one; the PrivacyFence settings link is origin + "/settings".
      2. web/routes_permissions.py: GET with the three views using permissions.merge/render functions; POST refresh
         starts one background task (stored on app.state) that collects every recipe and upserts permissions_reports.
      3. Templates tokens.html and permissions.html.
      4. Tests with logged_in(): tests/test_web_tokens.py (unknown name shows the suggestion; non-pfst value refused;
         the submitted value never appears in any response body, including errors; set → state from a monkeypatched
         check_one; delete; check all; missing names listed; env override banner; settings link only with a server URL).
         tests/test_web_permissions.py (with stored reports: recipe, union and group views show rule_text lines and
         NO_SUGGESTIONS where suggested_rules is null; refresh with install_fake_venv + permissions_json stores reports;
         a second refresh while running does not start another task).
      5. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_web_tokens.py tests/test_web_permissions.py -q passes'
      - 'python3 -m mypy passes'

  - id: p18-web-notifications-settings
    title: Notifications page and Settings page
    depends_on: [p14-web-auth]
    complexity: M
    touches:
      - src/privacyfence_cockpit/web/routes_notifications.py
      - src/privacyfence_cockpit/web/routes_settings.py
      - src/privacyfence_cockpit/web/templates/notifications.html
      - src/privacyfence_cockpit/web/templates/settings.html
      - tests/test_web_notifications.py
      - tests/test_web_settings.py
    brief: |
      Read docs/cockpit-v1-plan.md §W6, §N, §E (effective settings) and §W0.
      1. web/routes_notifications.py and notifications.html per §W6; the webhook URL is never rendered after it is saved.
      2. web/routes_settings.py and settings.html per §W6, with the exact validation messages and the effective
         value/source column from effective(); the password form follows §W1 rules and deletes other sessions.
      3. Tests with logged_in(): tests/test_web_notifications.py (add, invalid URL message, list shows name/format only
         and never the URL, delete, Send test with a Notifier built on httpx2.MockTransport shows "HTTP 200");
         tests/test_web_settings.py (each field saved; each invalid value message; cockpit.yaml server wins and the page
         says so; password change: wrong current, mismatch, success logs out a second session).
      4. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_web_notifications.py tests/test_web_settings.py -q passes'
      - 'python3 -m mypy passes'

  - id: p19-cli
    title: The cockpit CLI commands for docker exec
    depends_on: [p14-web-auth]
    complexity: M
    touches:
      - src/privacyfence_cockpit/cli.py
      - tests/test_cli.py
    brief: |
      Read docs/cockpit-v1-plan.md §L, §Q (RunLock, Worker.execute) and §R2 (permissions rendering).
      1. cli.py: add list, check, preflight, permissions, run, test, token set|show|delete, schedule list, git status,
         git update with the outputs and exit codes in §L. check, preflight, run and test --live build one
         Job(RUN, "manual", ...) and run it with a Worker built with on_lock_wait (prints the §Q waiting message) and
         on_line (prints each log line); the worker takes the run lock itself, so the CLI never takes it a second time
         for these. git update takes RunLock(paths.run_lock) directly with the same on_wait. token set reads with
         getpass on a TTY, else one stdin line; nothing reads a token from argv.
      2. tests/test_cli.py using make_cockpit's environment (COCKPIT_DATA_DIR, COCKPIT_MASTER_KEY_FILE via monkeypatch.setenv),
         tests/fakesync.py and tests/gitutil.py: list output format; run -n prints would-run lines and records nothing;
         run with an ok and a denied recipe exits 3 and prints both summary lines; --repeat and --stop-on-error;
         preflight streams lines; check prints the summary; permissions text, --union, --by-group and --json; test with
         and without pytest in the fake venv (make the fake venv's python a symlink to sys.executable for the "with" case
         and a script exiting 1 with "No module named pytest" for the "without" case); test --live uses replay; token
         set from stdin (pfst_ value) then show lists it present and never prints the value; token set with an unknown
         name exits 1 with the suggestion; delete; schedule list; git status and update against a gitutil remote; the
         lock message when another process holds the lock (hold it from a subprocess).
      3. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_cli.py -q passes'
      - 'python3 -m pytest -q --cov=privacyfence_cockpit --cov-branch --cov-fail-under=95 passes'
      - 'python3 -m mypy passes'

  - id: p20-image
    title: Dockerfile, hash-locked requirements, fixture recipe repository, smoke test, image.yml and compose example
    depends_on: [p19-cli]
    complexity: M
    touches:
      - Dockerfile
      - .dockerignore
      - docker-compose.yml
      - requirements/**
      - scripts/update_image_locks.sh
      - scripts/smoke_image.sh
      - tests/fixtures/recipe-repo/**
      - tests/test_fixture_repo.py
      - .github/workflows/image.yml
      - .claude/toolkit.yaml
    brief: |
      Read docs/cockpit-v1-plan.md §I (all of it except release.yml) and §X's compose description.
      1. requirements/build.in and scripts/update_image_locks.sh per §I; run it (python3 -m pip install uv first) to
         produce requirements/image.lock.txt and requirements/build.lock.txt with hashes; commit both.
      2. Dockerfile and .dockerignore exactly per §I (base digest
         sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 on both stages).
      3. tests/fixtures/recipe-repo/ per §I (fixture recipe, cockpit.yaml, requirements.txt with -e ./lib/privacyfence-sync-fake,
         .gitignore, the fake package). tests/test_fixture_repo.py: discover() finds imports/10-fixture; load_cockpit_yaml
         validates; pin_problem is None; the fake's main() handles --help, run --help, permissions --help and check
         (import it from its path with importlib, no install).
      4. scripts/smoke_image.sh per §I (executable, bash -n clean, shellcheck-clean if shellcheck is installed).
      5. .github/workflows/image.yml per §I with the action pins from the plan's Current state section (checkout and
         setup-python pins copied from .github/workflows/tests.yml).
      6. docker-compose.yml per §I/§X (port published on 127.0.0.1 only).
      7. .claude/toolkit.yaml: add the ci.dispatchable block from §I verbatim.
      8. Definition-of-done commands. Docker is not available here: do not try to build; the PR's image.yml run is the
         proof (final_checks). Read the Dockerfile line by line against §I before committing. No CHANGELOG line.
      Stop condition: if uv cannot resolve the lock for Python 3.12, stop with status=blocked and quote the error.
    acceptance:
      - 'python3 -m pytest tests/test_fixture_repo.py -q passes'
      - 'bash -n scripts/smoke_image.sh passes'
      - 'grep -c "python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1" Dockerfile prints 2'
      - 'grep -n "127.0.0.1:8080:8080" docker-compose.yml matches'
      - 'grep -n "\-\-hash=sha256:" requirements/image.lock.txt matches'
      - 'python3 -c "import yaml; yaml.safe_load(open(''.github/workflows/image.yml''))" passes'
      - 'grep -n "workflow: image.yml" .claude/toolkit.yaml matches'

  - id: p21-release
    title: Release workflow that cuts the tag and publishes the multi-arch image with provenance
    depends_on: [p20-image]
    complexity: S
    touches:
      - .github/workflows/release.yml
      - scripts/release_check.py
      - tests/test_release_check.py
      - docs/releasing.md
    brief: |
      Read docs/cockpit-v1-plan.md §I (release.yml, release_check.py, docs/releasing.md) and the PrivacyFence pattern it
      follows (privacyfence/privacyfence .github/workflows/release.yml and publish-pypi.yml: env-passed inputs, dry run
      default true, concurrency group).
      1. scripts/release_check.py (stdlib only): `--version X.Y.Z` and `--tag vX.Y.Z` modes with every check in §I, one
         `::error::` line per problem, exit 1 on any.
      2. tests/test_release_check.py against throwaway git repos in tmp_path (a local bare "origin"): each check passing
         and failing.
      3. .github/workflows/release.yml per §I with the pinned actions (attest-build-provenance
         4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2, setup-qemu 99012661954931238ded8c8b007157a8430204e1 # v4.4.0,
         setup-buildx f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1, build-push c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7.4.0,
         login dbcb813823bdd20940b903addbd779551569679f # v4.6.0). Inputs reach shell only through env.
      4. docs/releasing.md per §I.
      5. Definition-of-done commands. No CHANGELOG line.
    acceptance:
      - 'python3 -m pytest tests/test_release_check.py -q passes'
      - 'python3 -c "import yaml; yaml.safe_load(open(''.github/workflows/release.yml''))" passes'
      - 'grep -n "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8" .github/workflows/release.yml matches'
      - 'grep -n "\${{ inputs\." .github/workflows/release.yml | grep -vE "^[0-9]+: +[A-Z_]+: \\$\{\{ inputs\.[a-z_]+ \}\}$" prints nothing'

  - id: p22-docs
    title: README, user guide, recipe-repository reference and the migration guide from the Mac cockpit
    depends_on: [p15-web-runs, p16-web-schedules-code, p17-web-tokens-permissions, p18-web-notifications-settings, p21-release]
    complexity: M
    touches:
      - README.md
      - docs/user-guide.md
      - docs/recipe-repository.md
      - docs/migrating-from-the-mac-cockpit.md
    brief: |
      Read docs/cockpit-v1-plan.md §X, then the code as merged (every message and route quoted in the docs must match
      the code; grep for each one).
      1. README.md, docs/user-guide.md, docs/recipe-repository.md, docs/migrating-from-the-mac-cockpit.md with the
         content listed in §X. Link ADRs by their final file names docs/adr/0002-… to 0007-… only after p23 creates
         them; in this phase link docs/adr/README.md instead.
      2. Every command in the docs is one the CLI accepts (run `python3 -m privacyfence_cockpit.cli --help` style checks
         via `python3 -c "from privacyfence_cockpit.cli import main; main(['--help'])"`).
      No CHANGELOG line.
    acceptance:
      - 'grep -n "openssl rand -base64 32" README.md matches'
      - 'grep -n "127.0.0.1:8080" README.md matches'
      - 'grep -n "launchctl bootout" docs/migrating-from-the-mac-cockpit.md matches'
      - 'grep -n "COCKPIT_REPLAY" docs/recipe-repository.md matches'

  - id: p23-adrs-retire
    title: ADRs 0002–0007, the changelog, and retiring the plan
    depends_on: [p22-docs]
    complexity: S
    touches:
      - docs/adr/**
      - CHANGELOG.md
      - README.md
      - docs/user-guide.md
      - docs/cockpit-v1-plan.md
      - docs/cockpit-v1-plan-manual-steps.html
    brief: |
      Read docs/adr/README.md (rules and template) and docs/cockpit-v1-plan.md "Rejected alternatives" and "ADRs".
      1. Write docs/adr/0002 to 0007 from the template, one decision each, Status "Accepted — <today>. Implemented in
         <the feature branch's merge>", Context/Decision/Alternatives/Consequences from the plan, Related linking source
         files (src/privacyfence_cockpit/web/auth.py, crypto.py, secrets_store.py, gitauth.py, cockpit_yaml.py,
         scheduler.py, gitrepo.py, Dockerfile, .github/workflows/release.yml) and ADR 0001, never the plan. Titles:
         0002 "One owner per container, with a local password bootstrapped by a one-time setup code";
         0003 "Secrets are encrypted on the volume with a key from a Docker secret, and only one recipe token reaches a subprocess";
         0004 "Schedules live in cockpit.yaml in the recipe repository";
         0005 "Missed scheduled runs are recorded and notified, never caught up";
         0006 "The recipe checkout changes only on the owner's request, fast-forward only, and is never committed to";
         0007 "One multi-arch image on GHCR, published with provenance by the run that cuts the tag".
      2. Add the six rows to the index in docs/adr/README.md.
      3. CHANGELOG.md under "## [Unreleased]" (no version heading): an "### Added" list: the container image and compose
         example; the web dashboard (overview, runs with live logs, history and runs.csv, schedules, code browser,
         tokens, permissions, notifications, settings); scheduling from cockpit.yaml; git-backed recipe repository with
         deploy key or PAT; recipe environments with the privacyfence-sync feature gate; encrypted secrets with a master
         key; webhook notifications (JSON, Slack, ntfy); the cockpit CLI; the release workflow with provenance.
      4. In README.md and docs/user-guide.md, link the ADRs where p22 linked docs/adr/README.md for a decision.
      5. Delete docs/cockpit-v1-plan.md and docs/cockpit-v1-plan-manual-steps.html; grep -rn "cockpit-v1-plan" . must print nothing.
    acceptance:
      - 'ls docs/adr/000[2-7]-*.md | wc -l prints 6'
      - 'grep -c "^| \[000[2-7]\]" docs/adr/README.md prints 6'
      - 'grep -rn "cockpit-v1-plan" . --exclude-dir=.git prints nothing'
      - 'grep -c "^## \[" CHANGELOG.md prints 1'
      - 'grep -n "^### Added" CHANGELOG.md matches'
```
