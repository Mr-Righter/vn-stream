"""Resolve engine-owned directory names without a second family registry."""

import re
from pathlib import Path


def engine_directory(root: Path, engine_id: str, engines) -> Path:
    adapter = engines.get(engine_id)
    name = getattr(adapter, "directory_name", None)
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise ValueError("Invalid engine directory declaration")
    target = root / name
    if target.is_symlink():
        raise ValueError("Engine directories must not be symbolic links")
    return target


def directories(root: Path, engines) -> dict[str, Path]:
    result = {key: engine_directory(root, key, engines) for key in engines}
    if len(set(result.values())) != len(result):
        raise ValueError("Engine directory names must be unique")
    return result
