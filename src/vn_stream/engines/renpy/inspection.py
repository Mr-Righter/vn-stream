"""Bounded static project inspection; never execute game code."""

from pathlib import Path

from vn_stream.engines.contracts import Capabilities, Inspection
from vn_stream.engines.renpy.version_detection import detect_engine
from vn_stream.projects.manifest import read_manifest
from vn_stream.storage.files import safe as _file

CAPABILITIES: Capabilities = {
    "assets": True,
    "save_management": True,
    "save_editing": True,
    "save_migration": True,
    "native_actions": True,
    "exit_mode": "native",
    "uses_original_engine_version": True,
}


def inspect_project(project):
    game = _file(project, "game")
    if not game.is_dir():
        return None
    # Bound discovery even for externally registered, untrusted trees.
    import os

    count = 0
    found = False
    for folder, dirs, files in os.walk(game, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not (Path(folder) / d).is_symlink()]
        if len(Path(folder).relative_to(game).parts) >= 32:
            dirs.clear()
        count += len(files) + len(dirs)
        if count > 100000:
            break
        if any(Path(f).suffix.lower() in (".rpy", ".rpyc", ".rpa") for f in files):
            found = True
            break
    if not found:
        return None
    try:
        record = read_manifest(project)
    except (OSError, ValueError):
        record = {}
    detected = detect_engine(project, allow_logs=not bool(record.get("runtime_id")))
    return Inspection(
        "renpy",
        detected.status,
        "game",
        detected.version,
        tuple(detected.sources),
        capabilities=CAPABILITIES.copy(),
    )
