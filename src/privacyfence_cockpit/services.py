"""The Cockpit service container: wires every manager together and runs the background loops."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from privacyfence_cockpit.cockpit_yaml import CockpitConfig, CockpitConfigError, load_cockpit_yaml
from privacyfence_cockpit.config import EnvConfig
from privacyfence_cockpit.crypto import load_master_key
from privacyfence_cockpit.db import Database
from privacyfence_cockpit.effective import Effective, effective
from privacyfence_cockpit.gitauth import credentials_factory, write_builtin_known_hosts
from privacyfence_cockpit.gitrepo import GitManager
from privacyfence_cockpit.history import History
from privacyfence_cockpit.jobs import JobQueue, Worker
from privacyfence_cockpit.models import parse_iso, utc_now
from privacyfence_cockpit.notify import Notifier
from privacyfence_cockpit.paths import DataPaths
from privacyfence_cockpit.recipes import DiscoveryError, Recipe, discover
from privacyfence_cockpit.runlock import RunLock
from privacyfence_cockpit.runner import Runner
from privacyfence_cockpit.scheduler import Scheduler
from privacyfence_cockpit.secrets_store import SecretStore
from privacyfence_cockpit.token_check import TokenChecker
from privacyfence_cockpit.tokens import TokenResolver
from privacyfence_cockpit.venvs import VenvManager

log = logging.getLogger(__name__)

TOKEN_CHECK_INTERVAL = timedelta(hours=24)
TOKEN_START_DELAY_SECONDS = 60.0
TOKEN_POLL_SECONDS = 3600.0
RETENTION_POLL_SECONDS = 86400.0


class Cockpit:
    """Everything the web app and the CLI share. Build it with `Cockpit.open`."""

    def __init__(
        self,
        env: EnvConfig,
        paths: DataPaths,
        db: Database,
        store: SecretStore,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.env = env
        self.paths = paths
        self.db = db
        self.store = store
        self.clock = clock
        # None means "use fetch_interval_minutes from the effective settings".
        self.fetch_poll_seconds: float | None = None
        self.token_start_delay_seconds = TOKEN_START_DELAY_SECONDS
        self.token_poll_seconds = TOKEN_POLL_SECONDS
        self.retention_poll_seconds = RETENTION_POLL_SECONDS
        self._tasks: list[asyncio.Task[None]] = []

        self.tokens = TokenResolver(store, env.env_token)
        self.notifier = Notifier(db, store, env.public_url, clock=clock)
        self.git = GitManager(paths, db, credentials_factory(db, store, paths), self.notifier, clock)
        self.venvs = VenvManager(paths, db, self.notifier, clock=clock)
        self.history = History(db)
        self.runner = Runner(
            paths=paths,
            history=self.history,
            venvs=self.venvs,
            git=self.git,
            tokens=self.tokens,
            effective=self.effective,
            clock=clock,
        )
        self.queue = JobQueue()
        self.lock = RunLock(paths.run_lock)
        self.worker = Worker(
            queue=self.queue,
            runner=self.runner,
            git=self.git,
            venvs=self.venvs,
            history=self.history,
            lock=self.lock,
            recipes=self.recipes,
            effective=self.effective,
            events=self.notifier,
        )
        self.scheduler = Scheduler(
            db=db,
            history=self.history,
            queue=self.queue,
            config=self.config,
            effective=self.effective,
            events=self.notifier,
            clock=clock,
        )
        self.token_checker = TokenChecker(db, self.tokens, store, self.effective, self.notifier, clock)

    @classmethod
    def open(cls, env: EnvConfig, *, clock: Callable[[], datetime] = utc_now) -> Cockpit:
        paths = DataPaths(env.data_dir).ensure()
        key = load_master_key(env.master_key_file)
        store = SecretStore(paths.secrets, key)
        store.verify_canary()
        db = Database(paths.db_file)
        db.migrate()
        write_builtin_known_hosts(paths)
        return cls(env, paths, db, store, clock)

    def recipes(self) -> list[Recipe]:
        try:
            return discover(self.paths.repo)
        except DiscoveryError as exc:
            log.warning("recipe discovery failed: %s", exc)
            return []

    def config(self) -> CockpitConfig:
        return load_cockpit_yaml(self.paths.repo)

    def config_or_default(self) -> tuple[CockpitConfig, str | None]:
        try:
            return self.config(), None
        except CockpitConfigError as exc:
            return CockpitConfig(), str(exc)

    def effective(self) -> Effective:
        return effective(self.db, self.config_or_default()[0], self.env)

    async def start(self) -> None:
        if self._tasks:
            return
        loops: dict[str, Callable[[], Awaitable[None]]] = {
            "worker": self.worker.run_forever,
            "scheduler": self.scheduler.run_forever,
            "notifier": self.notifier.run_forever,
            "fetch": self._fetch_loop,
            "tokens": self._token_loop,
            "retention": self._retention_loop,
        }
        self._tasks = [
            asyncio.create_task(self._guarded(name, loop), name=f"cockpit-{name}") for name, loop in loops.items()
        ]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _guarded(self, name: str, loop: Callable[[], Awaitable[None]]) -> None:
        """Restart a loop that dies; the loops catch their own errors, so this is the last line of defence."""
        while True:
            try:
                await loop()
                return
            except Exception:
                log.exception("the %s loop crashed; restarting it", name)
                await asyncio.sleep(1)

    async def _fetch_loop(self) -> None:
        while True:
            try:
                status = await asyncio.to_thread(self.git.status)
                if status.configured and status.cloned:
                    await asyncio.to_thread(self.git.fetch)
            except Exception as exc:
                # GitError already carries the sanitised one-line text; the manager has recorded it.
                log.warning("fetching the recipe repository failed: %s", exc)
            interval = self.fetch_poll_seconds
            if interval is None:
                interval = self.effective().fetch_interval_minutes * 60
            await asyncio.sleep(interval)

    async def _token_loop(self) -> None:
        await asyncio.sleep(self.token_start_delay_seconds)
        while True:
            try:
                if self._token_check_due():
                    await self.token_checker.check_all()
            except Exception:
                # e.g. a stored token that cannot be decrypted; the check is retried at the next poll.
                log.exception("the token check failed")
            await asyncio.sleep(self.token_poll_seconds)

    def _token_check_due(self) -> bool:
        last = self.db.get_setting("tokens.last_check_at")
        if last is None:
            return True
        try:
            return self.clock() - parse_iso(last) >= TOKEN_CHECK_INTERVAL
        except ValueError:
            return True

    async def _retention_loop(self) -> None:
        while True:
            try:
                removed = await asyncio.to_thread(
                    self.history.prune_logs, self.paths, self.effective().retention_days, self.clock()
                )
                if removed:
                    log.info("removed %d expired log folder(s) and file(s)", removed)
            except Exception:
                log.exception("pruning old logs failed")
            await asyncio.sleep(self.retention_poll_seconds)
