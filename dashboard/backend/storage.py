"""Thread-safe, crash-resilient atomic storage utilities for AlphaQuant AI Pro."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("storage")

_FILE_LOCKS: dict[str, threading.Lock] = {}
_META_LOCK = threading.Lock()


def _get_lock(path: Path) -> threading.Lock:
    """Retrieve or create a thread lock specific to a canonical file path."""
    canonical = str(path.resolve())
    with _META_LOCK:
        if canonical not in _FILE_LOCKS:
            _FILE_LOCKS[canonical] = threading.Lock()
        return _FILE_LOCKS[canonical]


def safe_load_json(
    file_path: Path | str,
    default: Any = None,
    fallback_backup: bool = True,
) -> Any:
    """Safely loads a JSON file with automatic fallback to backup (.bak) on corruption."""
    path = Path(file_path).resolve()
    bak_path = path.with_suffix(path.suffix + ".bak")
    lock = _get_lock(path)

    with lock:
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logger.error("Failed to parse JSON from %s (%s). Checking backup...", path.name, exc)

        if fallback_backup and bak_path.exists():
            try:
                with bak_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("Successfully restored valid data from backup file: %s", bak_path.name)
                # Attempt to restore the primary file from good backup
                try:
                    shutil.copy2(bak_path, path)
                except Exception:
                    pass
                return data
            except Exception as exc:
                logger.critical("Backup file %s is also corrupt or unreadable: %s", bak_path.name, exc)

    return default() if callable(default) else default


def atomic_write_json(
    file_path: Path | str,
    data: Any,
    backup: bool = True,
    indent: int = 2,
) -> bool:
    """Atomically writes JSON data to disk using temporary files, fsync, and atomic rename.
    
    Prevents corrupt/zero-byte files on sudden power loss or process kill.
    Maintains a .bak copy of the previously valid data.
    """
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    bak_path = path.with_suffix(path.suffix + ".bak")
    lock = _get_lock(path)

    # Unique temp file in the SAME directory to guarantee atomic rename on the same filesystem
    pid = os.getpid()
    tid = threading.get_ident()
    now_ns = time.time_ns()
    temp_path = path.parent / f"{path.name}.{pid}.{tid}.{now_ns}.tmp"

    with lock:
        try:
            # 1. Create a backup of the existing valid file if it exists and has content
            if backup and path.exists() and path.stat().st_size > 0:
                try:
                    shutil.copy2(path, bak_path)
                except Exception as b_exc:
                    logger.warning("Could not create backup for %s: %s", path.name, b_exc)

            # 2. Write serialized data to temporary file
            serialized = json.dumps(data, indent=indent, default=str)
            with temp_path.open("w", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())

            # 3. Atomically replace target file
            os.replace(temp_path, path)
            return True

        except Exception as exc:
            logger.error("Failed atomic write for %s: %s", path.name, exc)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            return False
