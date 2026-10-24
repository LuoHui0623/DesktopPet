import os
from pathlib import Path

import pytest

from infra.lock import InstanceLockError, SingleInstanceLock


def test_second_holder_is_rejected_and_reports_pid(tmp_path: Path):
    lock_file = tmp_path / "backend.lock"
    first = SingleInstanceLock(lock_file)
    first.acquire()
    try:
        second = SingleInstanceLock(lock_file)
        with pytest.raises(InstanceLockError) as exc:
            second.acquire()
        assert exc.value.holder_pid == str(os.getpid())
    finally:
        first.release()


def test_lock_can_be_reacquired_after_release(tmp_path: Path):
    lock_file = tmp_path / "backend.lock"
    first = SingleInstanceLock(lock_file)
    first.acquire()
    first.release()
    second = SingleInstanceLock(lock_file)
    second.acquire()
    second.release()


def test_stale_lock_file_does_not_block(tmp_path: Path):
    lock_file = tmp_path / "backend.lock"
    lock_file.write_bytes(b"\x00999999")
    lock = SingleInstanceLock(lock_file)
    lock.acquire()
    lock.release()


def test_context_manager_releases_lock(tmp_path: Path):
    lock_file = tmp_path / "backend.lock"
    with SingleInstanceLock(lock_file):
        with pytest.raises(InstanceLockError):
            SingleInstanceLock(lock_file).acquire()
    SingleInstanceLock(lock_file).acquire()


def test_release_without_acquire_is_noop(tmp_path: Path):
    SingleInstanceLock(tmp_path / "backend.lock").release()


def test_creates_parent_directory(tmp_path: Path):
    lock_file = tmp_path / "nested" / "backend.lock"
    lock = SingleInstanceLock(lock_file)
    lock.acquire()
    lock.release()
    assert lock_file.exists()
