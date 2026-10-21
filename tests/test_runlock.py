import subprocess
import sys
import threading

import pytest

from privacyfence_cockpit.runlock import RunLock

HOLDER = """
import fcntl, sys
handle = open(sys.argv[1], "a+")
fcntl.flock(handle, fcntl.LOCK_EX)
print("held", flush=True)
sys.stdin.readline()
"""


@pytest.fixture
def holder(tmp_path):
    """A separate process that holds the lock until its stdin gets a line."""
    path = tmp_path / "run.lock"
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    yield path, proc
    release_holder(proc)
    proc.stdout.close()


def release_holder(proc):
    if not proc.stdin.closed:
        proc.stdin.write("\n")
        proc.stdin.close()
        proc.wait(timeout=5)


def test_try_acquire_and_release(tmp_path):
    first, second = RunLock(tmp_path / "run.lock"), RunLock(tmp_path / "run.lock")
    assert first.try_acquire()
    assert not second.try_acquire()
    first.release()
    assert second.try_acquire()
    second.release()


def test_second_lock_fails_while_another_process_holds_it(holder):
    path, proc = holder
    lock = RunLock(path)
    assert not lock.try_acquire()
    release_holder(proc)
    assert lock.try_acquire()
    lock.release()


def test_acquire_calls_on_wait_then_blocks_until_released(holder):
    path, proc = holder
    waited = threading.Event()

    def on_wait():
        waited.set()
        threading.Timer(0.1, release_holder, args=(proc,)).start()

    lock = RunLock(path)
    lock.acquire(on_wait)
    assert waited.is_set()
    lock.release()


def test_acquire_without_contention_skips_on_wait(tmp_path):
    calls = []
    lock = RunLock(tmp_path / "run.lock")
    lock.acquire(lambda: calls.append(1))
    assert calls == []
    lock.release()


def test_context_manager_releases(tmp_path):
    path = tmp_path / "run.lock"
    with RunLock(path):
        assert not RunLock(path).try_acquire()
    other = RunLock(path)
    assert other.try_acquire()
    other.release()


def test_one_object_never_takes_the_lock_twice(tmp_path):
    lock = RunLock(tmp_path / "run.lock")
    lock.acquire()
    with pytest.raises(RuntimeError, match="already held"):
        lock.acquire()
    with pytest.raises(RuntimeError, match="already held"):
        lock.try_acquire()
    lock.release()
    lock.release()  # releasing an unheld lock is a no-op
