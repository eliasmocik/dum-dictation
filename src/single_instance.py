#!/usr/bin/env python3
"""
Single-instance guard — refuse to start a second dum on the same machine.

Two live copies would FIGHT over shared, single-owner resources:
  * the global hotkey — two pynput listeners both grab the double-tap; on macOS the
    OS can abort the second process outright (the same TIS/TSM-from-two-threads abort
    live.py already guards against, but now across processes).
  * the microphone — two recognizers racing the same input stream.
  * the overlay — two robots typing into the focused app at once = corrupted text.

So we take an exclusive, advisory OS lock on a file in ~/.dum and the loser exits
cleanly. The lock is released automatically when the holder dies (even on crash/kill),
so a previous hard-stop never wedges the next launch.

Locking is OS-specific but behind one interface: `fcntl.flock` on macOS + Linux,
`msvcrt.locking` (a non-blocking byte-range lock) on Windows. Either way the lock is
held by the open file handle and the OS drops it when the process dies, so the call
sites in live.py never change.
"""
import os
import sys
from pathlib import Path

DEFAULT_LOCK = Path.home() / ".dum" / "dum.lock"


def _lock_exclusive(fd):
    """Take a non-blocking exclusive lock on `fd`; raise OSError if already held."""
    if sys.platform == "win32":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)   # 1 byte at offset 0; raises on contention
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd):
    if sys.platform == "win32":
        import msvcrt
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


class AlreadyRunning(Exception):
    """Raised by acquire() when another live dum already holds the lock."""

    def __init__(self, lock_path, holder_pid=None):
        self.lock_path = str(lock_path)
        self.holder_pid = holder_pid
        msg = f"another dum already holds {self.lock_path}"
        if holder_pid:
            msg += f" (pid {holder_pid})"
        super().__init__(msg)


class SingleInstance:
    """Hold an exclusive lock for the life of the process.

    Use as a context manager (released on exit) or call acquire()/release()
    explicitly. acquire() raises AlreadyRunning if a live copy already holds it.
    """

    def __init__(self, lock_path=DEFAULT_LOCK):
        self.lock_path = Path(lock_path)
        self._fd = None

    def _read_holder_pid(self):
        """Best-effort: whose pid is in the lock file (for a friendlier message)."""
        try:
            return int(self.lock_path.read_text().strip() or 0) or None
        except (OSError, ValueError):
            return None

    def acquire(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # O_RDWR|O_CREAT WITHOUT O_TRUNC: a losing contender must not blank the holder's
        # pid before it discovers the lock is taken. We truncate+write our own pid only
        # AFTER the lock is ours.
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _lock_exclusive(fd)
        except OSError:
            holder = self._read_holder_pid()
            os.close(fd)
            raise AlreadyRunning(self.lock_path, holder)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        os.fsync(fd)
        self._fd = fd
        return self

    def release(self):
        if self._fd is None:
            return
        try:
            _unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
