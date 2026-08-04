from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class HomeInstanceLockError(RuntimeError):
    pass


class HomeInstanceLock:
    """Hold one non-blocking process lock for a BILIUP_HOME directory."""

    def __init__(self, home: Path):
        self.path = home / ".biliup.lock"
        self._stream: BinaryIO | None = None

    def acquire(self) -> None:
        if self._stream is not None:
            return
        stream = self.path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise HomeInstanceLockError(
                f"Another biliup server is already using BILIUP_HOME: {self.path.parent}"
            ) from exc
        self._stream = stream

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def __enter__(self) -> HomeInstanceLock:
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()
