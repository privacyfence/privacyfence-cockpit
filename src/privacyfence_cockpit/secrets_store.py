"""Encrypted secrets on the data volume: tokens, git credentials and webhook URLs."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from privacyfence_cockpit.crypto import MasterKeyError, SecretDecryptError, decrypt, encrypt

KINDS = frozenset({"token", "git", "webhook"})
CANARY_PLAINTEXT = b"privacyfence-cockpit"
CANARY_AAD = "canary"


class SecretStore:
    def __init__(self, root: Path, key: bytes) -> None:
        self._root = root
        self._key = key

    def __repr__(self) -> str:
        return f"SecretStore(root={self._root!r})"

    def _dir(self, kind: str) -> Path:
        if kind not in KINDS:
            raise ValueError(f"unknown secret kind {kind!r}")
        return self._root / kind

    def _file(self, kind: str, name: str) -> Path:
        encoded = base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")
        return self._dir(kind) / f"{encoded}.bin"

    def _write(self, path: Path, blob: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(blob)
        os.replace(tmp, path)

    def set(self, kind: str, name: str, value: str) -> None:
        self._write(self._file(kind, name), encrypt(self._key, value.encode(), f"{kind}:{name}"))

    def get(self, kind: str, name: str) -> str | None:
        path = self._file(kind, name)
        if not path.is_file():
            return None
        return decrypt(self._key, path.read_bytes(), f"{kind}:{name}").decode()

    def delete(self, kind: str, name: str) -> bool:
        path = self._file(kind, name)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def has(self, kind: str, name: str) -> bool:
        return self._file(kind, name).is_file()

    def names(self, kind: str) -> list[str]:
        folder = self._dir(kind)
        if not folder.is_dir():
            return []
        found = []
        for path in folder.glob("*.bin"):
            encoded = path.stem
            found.append(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode())
        return sorted(found)

    def verify_canary(self) -> None:
        path = self._root / "canary.bin"
        if not path.exists():
            self._write(path, encrypt(self._key, CANARY_PLAINTEXT, CANARY_AAD))
            return
        try:
            plaintext = decrypt(self._key, path.read_bytes(), CANARY_AAD)
        except SecretDecryptError:
            plaintext = b""
        if plaintext != CANARY_PLAINTEXT:
            raise MasterKeyError(
                f"The master key does not match the one that encrypted {self._root}. "
                f"Use the original key file, or move {self._root} away to start without the stored secrets."
            )
