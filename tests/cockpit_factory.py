"""Builds a Cockpit over a throwaway data directory."""

from __future__ import annotations

import base64
import os
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from privacyfence_cockpit.config import EnvConfig
from privacyfence_cockpit.models import utc_now
from privacyfence_cockpit.services import Cockpit


def make_cockpit(
    tmp_path: Path,
    *,
    clock: Callable[[], datetime] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Cockpit:
    key_file = tmp_path / "cockpit_master_key"
    if not key_file.exists():
        key_file.write_text(base64.b64encode(os.urandom(32)).decode() + "\n")
    values = {
        "COCKPIT_DATA_DIR": str(tmp_path / "data"),
        "COCKPIT_MASTER_KEY_FILE": str(key_file),
        **(environ or {}),
    }
    return Cockpit.open(EnvConfig.from_environ(values), clock=clock or utc_now)
