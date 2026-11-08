"""Atomically publish artifacts within one collection run."""

import csv
import json
import os
import tempfile
import time
from pathlib import Path


def path_reference(path, root):
    """Store repository paths relative to root with portable separators."""
    path = Path(path).resolve()
    root = Path(root).resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


def replace_with_retry(source, target):
    """Allow brief Windows reader/antivirus locks without losing atomic writes."""
    for attempt in range(6):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.1 * 2 ** attempt)


def write_json(path: Path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def write_csv(path: Path, rows, fields):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8-sig', newline='', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='raise')
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
