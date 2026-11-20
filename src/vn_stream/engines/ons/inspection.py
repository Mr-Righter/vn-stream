"""Bounded static project inspection; never execute game code."""

from functools import lru_cache
from pathlib import Path

from vn_stream.engines.contracts import Capabilities, Inspection
from vn_stream.storage.files import safe as _file


@lru_cache(maxsize=8)
def _ons_script(path: str, identity: tuple, encrypted: bool):
    with Path(path).open("rb") as file:
        raw = file.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        return None, None
    if encrypted:
        raw = bytes(x ^ 0x84 for x in raw)
    encoding = "utf8" if raw.startswith(b"\xef\xbb\xbf") else "gbk"
    return raw.decode("utf-8-sig" if encoding == "utf8" else "gb18030", errors="replace"), encoding


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
    import re

    entry = next(
        (
            n
            for n in (
                "0.txt",
                "00.txt",
                "nscr_sec.dat",
                "nscript.___",
                "nscript.dat",
                "onscript.nt2",
                "onscript.nt3",
            )
            if _file(project, n).is_file()
        ),
        None,
    )
    if entry is None:
        return None
    if entry not in ("0.txt", "00.txt", "nscript.dat"):
        return Inspection(
            "ons",
            "unsupported",
            entry,
            warnings=("unsupported_script",),
            capabilities=CAPABILITIES.copy(),
        )
    script = _file(project, entry)
    stat = script.stat()
    content, encoding = _ons_script(
        str(script),
        (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns),
        entry == "nscript.dat",
    )
    if content is None:
        return Inspection(
            "ons",
            "unsupported",
            entry,
            warnings=("script_limit",),
            capabilities=CAPABILITIES.copy(),
        )
    if not re.search(r"^\s*\*define\b", content, re.M | re.I):
        return None
    size = re.search(r"^;\$S(\d{1,5}),(\d{1,5})", content, re.M)
    canvas = tuple(map(int, size.groups())) if size else None
    # Only a literal directive is safe to resolve without executing the script.
    define = re.split(r"^\s*game\s*$", content, maxsplit=1, flags=re.M | re.I)[0]
    matches = re.findall(r"^\s*savedir\s+([^\r\n]+)", define, re.M | re.I)
    save = None
    status, warnings = "detected", []
    if matches:
        literal = re.fullmatch(r'"([^"\r\n]+)"\s*(?:;.*)?', matches[-1])
        if literal:
            save = literal[1].replace("\\", "/")
            try:
                _file(project, save)
                if save == "." or any(p.startswith(".vn-stream") for p in Path(save).parts):
                    raise ValueError
            except ValueError:
                status, warnings, save = "unsupported", ["unsafe_save_directory"], None
        else:
            status, warnings = "unsupported", ["dynamic_save_directory"]
    # A project-supplied argument file can override the server's approved paths.
    if _file(project, "ons_args").exists():
        status, warnings = "unsupported", [*warnings, "project_arguments_require_review"]
    return Inspection(
        "ons",
        status,
        entry,
        evidence=(entry,),
        warnings=tuple(warnings),
        canvas=canvas,
        save_directory=save,
        encoding=encoding,
        capabilities=CAPABILITIES.copy(),
    )
