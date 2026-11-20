"""Explicit, bounded discovery of complete directories; never executes game code."""

import logging
import os
from pathlib import Path

from vn_stream.engines.registry import inspect_project

log = logging.getLogger(__name__)


def discover(library, *, max_depth=4, max_directories=5000):
    result = dict(added=0, warnings=0, truncated=False, directories=0)
    protected = [
        (library.data / name).resolve()
        for name in ("state", "tmp", "cache", "logs", "runtimes", "backups", "uploads/archives")
    ]
    if not library.library_root.is_relative_to(library.data):
        protected.append(library.data.resolve())
    stack = [(p, 0) for p in reversed(library.discovery_roots)]
    visited = set()
    known = {g.project_id for g in library.list()}
    entries = 0
    with library.lock:
        while stack:
            path, depth = stack.pop()
            if result["directories"] >= max_directories or entries >= 100000:
                result["truncated"] = True
                result["warnings"] += 1
                break
            if path in visited:
                continue
            visited.add(path)
            result["directories"] += 1
            try:
                if path.is_symlink() or any(path == p or path.is_relative_to(p) for p in protected):
                    continue
                if inspect_project(path).status != "unknown" or (path / "game").is_dir():
                    # A candidate is never searched for additional nested games.
                    inspection = inspect_project(path)
                    library.check_source_location(path)
                    library.check_classification(path, inspection.engine_id)
                    game = library.register(path, path.name)
                    if game.project_id not in known:
                        result["added"] += 1
                        known.add(game.project_id)
                    continue
                if depth >= max_depth:
                    with os.scandir(path) as children:
                        for entry in children:
                            entries += 1
                            if entries >= 100000 or (
                                not entry.name.startswith(".")
                                and entry.is_dir(follow_symlinks=False)
                            ):
                                result["truncated"] = True
                                result["warnings"] += 1
                                break
                    continue
                with os.scandir(path) as children:
                    for entry in children:
                        entries += 1
                        if entries >= 100000:
                            result["truncated"] = True
                            break
                        if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False):
                            stack.append((Path(entry.path), depth + 1))
            except (OSError, ValueError) as error:
                result["warnings"] += 1
                log.warning("Discovery skipped %s: %s", path, type(error).__name__)
    return result
