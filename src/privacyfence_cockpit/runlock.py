"""The cross-process lock that keeps recipe runs serial, whether the server or `docker exec cockpit` starts them."""

from __future__ import annotations

import fcntl
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import IO


class RunLock:
    """An exclusive `flock` on one file. One object holds it at most once: a second descriptor in the same
    process would block against the first."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: IO[str] | None = None

    def acquire(self, on_wait: Callable[[], None] | None = None) -> None:
        handle = self._open()
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if on_wait is not None:
                on_wait()
            fcntl.flock(handle, fcntl.LOCK_EX)
        self._handle = handle

    def try_acquire(self) -> bool:
        handle = self._open()
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()  # closing the descriptor drops the lock

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    def _open(self) -> IO[str]:
        if self._handle is not None:
            raise RuntimeError("the run lock is already held by this object")
        return self._path.open("a+")
