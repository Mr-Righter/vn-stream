"""Bounded static project inspection; never execute game code."""

from vn_stream.engines.contracts import Capabilities, Inspection
from vn_stream.storage.files import safe as _file

XP3_MAGIC = b"XP3\r\n \n\x1a\x8bg\x01"

CAPABILITIES: Capabilities = {
    "assets": True,
    "save_management": False,
    "save_editing": False,
    "save_migration": False,
    "native_actions": False,
    "exit_mode": "stop",
    "uses_original_engine_version": False,
}


def inspect_project(project):
    startup = _file(project, "startup.tjs")
    archive = _file(project, "data.xp3")
    valid = False
    if archive.is_file():
        with archive.open("rb") as file:
            valid = file.read(len(XP3_MAGIC)) == XP3_MAGIC
    if not startup.is_file() and not valid:
        return None
    evidence = tuple(n for n, ok in [("startup.tjs", startup.is_file()), ("data.xp3", valid)] if ok)
    warnings = ("plugins_require_validation",) if _file(project, "patch.tjs").exists() else ()
    return Inspection(
        "krkr",
        "detected",
        evidence[0],
        evidence=evidence,
        warnings=warnings,
        capabilities=CAPABILITIES.copy(),
    )
