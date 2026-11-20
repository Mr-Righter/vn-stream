"""Project-specific restrictions for ONS branches; no script execution."""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=32)
def _utf8(path, identity):
    with Path(path).open("rb") as stream:
        raw = stream.read(8 * 1024**2 + 1)
    if len(raw) > 8 * 1024**2:
        return False
    try:
        raw.decode("utf-8-sig")
    except UnicodeError:
        return False
    return b"\0" not in raw


def runtime_compatibility(project, inspection, runtime):
    flavor = runtime.get("flavor")
    if flavor not in {"yuri", "jh", "zero", "new"} or (
        flavor == "zero" and inspection.encoding not in {"gbk", "sjis"}
    ):
        return {"code": "engine.runtimeUnavailable", "params": {}}
    if flavor != "new":
        return None
    if inspection.entry not in ("0.txt", "00.txt"):
        return {"code": "engine.onsNewScript", "params": {}}
    try:
        for index in range(100):
            names = [inspection.entry] if index == 0 else [f"{index}.txt", f"{index:02}.txt"]
            path = next((project / name for name in names if (project / name).is_file()), None)
            if path is None:
                break
            info = path.stat()
            if path.is_symlink() or not _utf8(
                str(path), (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            ):
                return {"code": "engine.onsNewScript", "params": {}}
    except OSError:
        return {"code": "engine.onsNewScript", "params": {}}
    if not (project / "fonts/default.ttf").is_file():
        return {"code": "engine.onsNewFont", "params": {}}
    return None
