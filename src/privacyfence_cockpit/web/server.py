"""`cockpit serve`: opens the services, tidies up after a crash and runs the web server."""

from __future__ import annotations

import logging
import sys

import uvicorn

from privacyfence_cockpit.config import ConfigError, EnvConfig
from privacyfence_cockpit.crypto import MasterKeyError
from privacyfence_cockpit.db import DatabaseError
from privacyfence_cockpit.services import Cockpit
from privacyfence_cockpit.web.app import create_app

log = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def serve(env: EnvConfig) -> int:
    # Only the root level is set here: notify.py pins the httpx2 logger to WARNING, and that stays.
    level = logging.getLevelNamesMapping().get(env.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
    try:
        cockpit = Cockpit.open(env)
    except (MasterKeyError, ConfigError, DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        # No run is active in any process while the lock is free, so every "running" row is an orphan.
        if cockpit.lock.try_acquire():
            try:
                cockpit.history.mark_orphans(cockpit.clock())
            finally:
                cockpit.lock.release()
        app = create_app(cockpit)
        uvicorn.Server(
            uvicorn.Config(app, host=env.listen_host, port=env.listen_port, log_config=None, proxy_headers=False)
        ).run()  # pragma: no cover  # needs a real socket
    finally:
        cockpit.db.close()
    return 0
