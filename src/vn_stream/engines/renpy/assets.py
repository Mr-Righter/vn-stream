"""Ren'Py resource enumeration, preserving existing RPA overlay semantics."""

import os
from pathlib import Path

from vn_stream.assets.archive_index import Resources
from vn_stream.assets.sources import Source
from vn_stream.i18n import text
from vn_stream.storage.files import safe

SKIP = {"saves", "cache", ".vn-stream", "__pycache__"}


def enumerate_sources(game, inspection, check):
    sources, warnings = {}, 0
    resources = Resources(game)
    for archive, index in reversed(resources.archives):
        check()
        for name, chunks in index.items():
            check()
            if SKIP.intersection(Path(name).parts):
                continue
            captured = Source.describe(archive, str(archive.relative_to(game)), chunks)
            if captured["revision"] != resources.archive_revisions[archive]:
                raise ValueError(text("assets.changed"))
            sources[name] = captured
    known = {p for p, _ in resources.archives if _}
    warnings += sum(1 for p in (game / "game").glob("*.rpa") if p not in known)
    for base, dirs, names in os.walk(game / "game", followlinks=False):
        dirs[:] = [
            d
            for d in dirs
            if d not in SKIP and not d.startswith(".") and not (Path(base) / d).is_symlink()
        ]
        if len(Path(base).relative_to(game / "game").parts) >= 32 and dirs:
            raise ValueError(text("assets.limit"))
        for name in names:
            check()
            path = Path(base) / name
            if (
                path.is_symlink()
                or name.endswith((".rpa", ".rpyb", ".pyc", ".tmp"))
                or not path.is_file()
            ):
                continue
            relative = path.relative_to(game / "game").as_posix()
            if len(Path(relative).parts) > 32:
                raise ValueError(text("assets.limit"))
            safe(game / "game", relative)
            sources[relative] = Source.describe(path, str(path.relative_to(game)))
            if len(sources) > 100_000:
                raise ValueError(text("assets.limit"))
    total = sum(source["size"] for source in sources.values())
    if total > 20 * 1024**3 or len(sources) > 100_000:
        raise ValueError(text("assets.limit"))
    return sources, [
        dict(code="assets.archiveUnsupported", params={"source": "RPA"}) for _ in range(warnings)
    ]


class RenPyAssets:
    categories = ("image", "audio", "video", "script", "other")
    enumerate = staticmethod(enumerate_sources)


PROVIDER = RenPyAssets()
