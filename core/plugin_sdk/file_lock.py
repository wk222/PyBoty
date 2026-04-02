"""File Lock utility for PyBoty Plugin SDK.

Provides a cross-platform file locking mechanism to prevent concurrent
plugin operations (like installing dependencies or writing to shared state).
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


class FileLockTimeout(Exception):
    """Raised when a file lock cannot be acquired within the timeout."""
    pass


@contextmanager
def acquire_file_lock(lock_file: str | Path, timeout: float = 10.0, retry_interval: float = 0.1) -> Iterator[None]:
    """Acquire a cross-platform file lock.

    Uses an exclusive file creation strategy (O_CREAT | O_EXCL).
    Automatically cleans up the lock file upon exit.
    """
    lock_path = Path(lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    fd = None

    while True:
        try:
            # O_EXCL ensures this call fails if the file already exists
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            fd = os.open(str(lock_path), flags)
            break
        except FileExistsError:
            if time.time() - start_time >= timeout:
                raise FileLockTimeout(f"Could not acquire lock {lock_path} within {timeout}s")
            time.sleep(retry_interval)
        except Exception as exc:
            logger.error("Unexpected error acquiring lock %s: %s", lock_path, exc)
            raise

    try:
        # Write PID to lock file for debugging
        os.write(fd, str(os.getpid()).encode("utf-8"))
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Failed to remove lock file %s: %s", lock_path, exc)
