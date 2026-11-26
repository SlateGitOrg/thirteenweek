"""Frozen weekly snapshots: the forecast exactly as it stood on the as-of date."""

from __future__ import annotations

import json
from pathlib import Path

from .reference import snapshot_hash


class SnapshotExistsError(FileExistsError):
    """Raised instead of overwriting a frozen snapshot."""


def snapshot_name(snapshot: dict) -> str:
    suffix = "" if snapshot["scenario"] == "base" else f"-{snapshot['scenario']}"
    return f"{snapshot['as_of']}{suffix}.json"


def write_snapshot(snapshot: dict, directory: Path, force: bool = False) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / snapshot_name(snapshot)
    if path.exists() and not force:
        raise SnapshotExistsError(f"{path} is frozen; pass --force to replace it")
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    return path


def verify_snapshot(path: Path) -> bool:
    """True when the stored hash matches the stored content."""
    data = json.loads(Path(path).read_text())
    stored = data.pop("sha256")
    return snapshot_hash(data) == stored
