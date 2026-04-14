"""Host-agnostic atomic IO helpers for integration config files."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional


Validator = Callable[[Path], None]


def _timestamp(value: Optional[str] = None) -> str:
    if value:
        return value
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_backup_path(path: Path, host: str, timestamp: Optional[str] = None) -> Path:
    path = Path(path)
    suffixes = "".join(path.suffixes)
    stem = path.name[: -len(suffixes)] if suffixes else path.name
    return path.with_name(f"{stem}.{host}.{_timestamp(timestamp)}.bak{suffixes}")


def atomic_write_text(
    path: Path,
    content: str,
    *,
    host: str = "mempalace",
    timestamp: Optional[str] = None,
    validator: Optional[Validator] = None,
) -> Optional[Path]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    temp_path = Path(tmp_name)
    backup_path: Optional[Path] = None

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)

        if validator is not None:
            validator(temp_path)

        if path.exists():
            backup_path = build_backup_path(path, host, timestamp)
            shutil.copy2(path, backup_path)

        os.replace(temp_path, path)
        return backup_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(
    path: Path,
    updates: Mapping[str, object],
    *,
    host: str = "mempalace",
    timestamp: Optional[str] = None,
    validator: Optional[Validator] = None,
) -> Optional[Path]:
    path = Path(path)
    merged = {}
    if path.exists():
        try:
            merged = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TypeError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(merged, dict):
            raise TypeError(f"Expected JSON object in {path}")
    merged = dict(merged)
    merged.update(dict(updates))
    payload = json.dumps(merged, indent=2) + "\n"
    return atomic_write_text(
        path,
        payload,
        host=host,
        timestamp=timestamp,
        validator=validator,
    )
