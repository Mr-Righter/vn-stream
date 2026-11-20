"""Read engine declarations without executing Python, RPYC or archive members."""

import ast
import gzip
import io
import re
import struct
import tarfile
import tokenize
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

METADATA_LIMIT = 256 * 1024
ENGINE_FILES = ("renpy/__init__.py", "renpy/vc_version.py")
VERSION_STRING = re.compile(r"(?:Ren['’]Py\s+)?(\d+\.\d+\.\d+)(?:\.\d+)?(?:[+-][\w.]+)?")
LOG_VERSION = re.compile(r"(?m)^\s*Ren['’]Py\s+(\d+\.\d+\.\d+)(?:\.\d+)?\b")


@dataclass(frozen=True)
class Detection:
    version: str | None
    sources: tuple[str, ...]
    status: str  # metadata, log, unknown, or conflict


def _conditional_versions(data: bytes, python_major: int | None) -> set[str]:
    """7.5/8.0 share source; inspect only their known PY2 branch, never evaluate it."""
    result = set()
    try:
        module = ast.parse(data)
    except (SyntaxError, ValueError, RecursionError):
        return result
    for node in module.body:
        if not (
            isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "PY2"
        ):
            continue
        branches = (
            node.body + node.orelse
            if python_major is None
            else node.body
            if python_major == 2
            else node.orelse
        )
        for assignment in branches:
            if not isinstance(assignment, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == "version_tuple"
                for target in assignment.targets
            ):
                continue
            value = assignment.value
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) >= 3:
                items = value.elts[:3]
                if all(
                    isinstance(item, ast.Constant) and type(item.value) is int for item in items
                ):
                    result.add(".".join(str(item.value) for item in items))
    return result


@lru_cache(maxsize=128)
def _declarations(data: bytes, python_major: int | None = None) -> set[str]:
    """Tokenize Python 2/3 lexically; parse only the assignment's expression."""
    result = _conditional_versions(data, python_major)
    names = {"version_tuple", "version", "version_only"}
    try:
        tokens = tokenize.generate_tokens(
            io.StringIO(data.decode("utf-8", errors="replace")).readline
        )
        for token in tokens:
            if token.type != tokenize.NAME or token.start[1] != 0 or token.string not in names:
                continue
            expression = []
            assigned = False
            for part in tokens:
                if part.type in (tokenize.NEWLINE, tokenize.ENDMARKER):
                    break
                if assigned:
                    expression.append((part.type, part.string))
                elif part.string == "=":
                    assigned = True
            if not expression:
                continue
            try:
                value = ast.parse(tokenize.untokenize(expression), mode="eval").body
            except (SyntaxError, ValueError, RecursionError):
                continue
            if token.string == "version_tuple":
                items = value.elts if isinstance(value, (ast.Tuple, ast.List)) else []
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "VersionTuple"
                ):
                    items = value.args
                if len(items) >= 3 and all(
                    isinstance(x, ast.Constant) and type(x.value) is int for x in items[:3]
                ):
                    result.add(".".join(str(x.value) for x in items[:3]))
            elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                match = VERSION_STRING.fullmatch(value.value)
                if match:
                    result.add(match[1])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set()
    return result


def _read_small(path: Path, project: Path | None = None) -> bytes:
    try:
        if project is not None:
            current = project
            for part in path.relative_to(project).parts:
                current /= part
                if current.is_symlink():
                    return b""
        with path.open("rb") as file:
            data = file.read(METADATA_LIMIT + 1)
        return data if len(data) <= METADATA_LIMIT else b""
    except OSError:
        return b""


def _compiled_versions(data: bytes) -> set[str]:
    """Inspect only length-delimited version strings in generated vc_version.pyc.

    This deliberately does not unmarshal code, import a module or infer a
    release from the interpreter magic/Python version. Unknown layouts stay unknown.
    """
    if len(data) > METADATA_LIMIT or len(data) < 8 or data[2:4] != b"\r\n":
        return set()
    result = set()
    for index in range(8, len(data)):
        kind = data[index] & 0x7F
        if kind in (ord("z"), ord("Z")) and index + 2 <= len(data):
            length, start = data[index + 1], index + 2
        elif kind in map(ord, "aAust") and index + 5 <= len(data):
            length = struct.unpack_from("<I", data, index + 1)[0]
            start = index + 5
        else:
            continue
        if not 5 <= length <= 80 or start + length > len(data):
            continue
        try:
            text = data[start : start + length].decode("ascii")
        except UnicodeError:
            continue
        match = VERSION_STRING.fullmatch(text)
        if match:
            result.add(match[1])
    return result


@lru_cache(maxsize=32)
def _private_versions(path: str, stamp: int, size: int, limit: int) -> tuple[tuple[str, str], ...]:
    # Cache only parsed evidence, not archive payloads. Never extract private.mp3 to disk.
    if size > 16 * 1024**2:
        return ()
    evidence = []
    modules = []
    families = set()
    try:
        with gzip.open(path, "rb") as compressed:
            payload = compressed.read(limit + 1)
        if len(payload) > limit:
            return ()
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for count, member in enumerate(archive):
                if count >= 10_000:
                    return ()
                name = member.name.removeprefix("./")
                # RAPT moves lib/ into private.mp3 as well as engine source.
                # Collect hints before interpreting shared PY2 source branches.
                family = re.match(r"^lib/py([23])-[^/]+(?:/|$)", name)
                if family:
                    families.add(int(family[1]))
                if (
                    name not in (*ENGINE_FILES, "renpy/vc_version.pyc", "renpy/vc_version.pyo")
                    or not member.isfile()
                    or member.size > METADATA_LIMIT
                ):
                    continue
                with archive.extractfile(member) as reader:
                    modules.append((name, reader.read(METADATA_LIMIT + 1)))
        python_major = next(iter(families)) if len(families) == 1 else None
        for name, content in modules:
            versions = (
                _compiled_versions(content)
                if name.endswith((".pyc", ".pyo"))
                else _declarations(content, python_major)
            )
            for version in versions:
                evidence.append((version, "private.mp3:" + name))
    except (OSError, EOFError, tarfile.TarError, zlib.error):
        return ()
    return tuple(evidence)


def _result(evidence: list[tuple[str, str]], status: str) -> Detection:
    versions = {version for version, _ in evidence}
    sources = tuple(sorted({source for _, source in evidence}))
    return Detection(
        next(iter(versions)) if len(versions) == 1 else None,
        sources,
        status if len(versions) == 1 else "conflict",
    )


def detect_engine(
    project: Path, *, private_limit: int = 64 * 1024**2, allow_logs: bool = True
) -> Detection:
    evidence = []
    # Directory names are platform-neutral evidence of the game's own interpreter.
    # Our service's Python 3 must not decide a shared 7.x/8.x source branch.
    families = {
        int(path.name[2])
        for path in project.glob("lib/py[23]-*")
        if path.is_dir() and not path.is_symlink()
    }
    python_major = next(iter(families)) if len(families) == 1 else None
    for name in ENGINE_FILES:
        evidence.extend(
            (version, name)
            for version in _declarations(_read_small(project / name, project), python_major)
        )
    compiled = []
    candidates = [project / "renpy/vc_version.pyc", project / "renpy/vc_version.pyo"]
    candidates.extend(sorted((project / "renpy/__pycache__").glob("vc_version.*.pyc"))[:8])
    for path in candidates:
        if not path.is_symlink():
            compiled.extend(
                (version, str(path.relative_to(project)))
                for version in _compiled_versions(_read_small(path, project))
            )
    for relative in ("private.mp3", "assets/private.mp3"):
        private = project / relative
        try:
            if private.is_symlink() or private.parent.is_symlink():
                continue
            stat = private.stat()
            found = _private_versions(str(private), stat.st_mtime_ns, stat.st_size, private_limit)
            for version, source in found:
                item = (version, source.replace("private.mp3:", relative + ":", 1))
                (compiled if source.endswith((".pyc", ".pyo")) else evidence).append(item)
        except OSError:
            pass
    # Source declarations outrank stale compiled caches; compiled-only packages
    # still provide original-engine evidence even after service runtime selection.
    if evidence or compiled:
        return _result(evidence or compiled, "metadata")
    if allow_logs:
        for name in ("log.txt", "traceback.txt"):
            text = _read_small(project / name, project).decode("utf-8", errors="replace")
            evidence.extend((match[1], name) for match in LOG_VERSION.finditer(text))
    return _result(evidence, "log") if evidence else Detection(None, (), "unknown")
