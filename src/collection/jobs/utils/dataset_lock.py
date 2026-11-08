"""OS-managed single-writer lock; released automatically when a process exits."""

import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def dataset_lock(folder):
    folder = Path(folder).resolve()
    folder.parent.mkdir(parents=True, exist_ok=True)
    # Keep the file after unlock: unlinking could let two processes lock different inodes.
    lock_path = folder.parent / f".{folder.name}.lock"
    with lock_path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
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
            raise RuntimeError(
                f"Dataset is already locked by another crawler: {folder}"
            ) from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
