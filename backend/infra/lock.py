import os
import sys
from pathlib import Path
from types import TracebackType

PID_OFFSET = 1

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    _OPEN_FLAGS = getattr(os, "O_BINARY", 0)
else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    _OPEN_FLAGS = 0


class InstanceLockError(Exception):
    def __init__(self, holder_pid: str | None) -> None:
        self.holder_pid = holder_pid
        super().__init__(f"已有后端实例在运行（PID {holder_pid or '未知'}）")


class SingleInstanceLock:
    """操作系统级别的单实例锁。

    锁由进程持有，进程结束（包括被强制结束或崩溃）时系统自动释放，
    因此不会因残留的锁文件而无法启动。文件内容为持锁进程的 PID，仅用于错误提示。
    """

    def __init__(self, lock_file: Path) -> None:
        self._path = lock_file
        self._fd: int | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT | _OPEN_FLAGS, 0o644)
        try:
            _try_lock(fd)
        except OSError:
            holder = _read_holder(self._path)
            os.close(fd)
            raise InstanceLockError(holder) from None

        pid = str(os.getpid()).encode("ascii")
        os.lseek(fd, PID_OFFSET, os.SEEK_SET)
        os.write(fd, pid)
        os.ftruncate(fd, PID_OFFSET + len(pid))
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def _read_holder(path: Path) -> str | None:
    try:
        with open(path, "rb") as file:
            file.seek(PID_OFFSET)
            text = file.read().decode("ascii", errors="ignore").strip()
    except OSError:
        return None
    return text or None
