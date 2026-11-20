"""Read config literals and compiled literal operands; never restore Ren'Py objects."""

import ast
import os
import pickletools
import re
import struct
import zlib
from pathlib import Path, PurePosixPath

from vn_stream.assets.archive_index import Resources, inflate

FIELDS = {"name": "display_name", "version": "release_version", "window_icon": "window_icon"}
SKIP = {"tl", "saves", "cache", "images", "audio", "video", "movies", ".vn-stream"}
NAMES = {"options", "gui", "config", "settings", "version"}


def literal_text(expression):
    if not isinstance(expression, str) or len(expression) > 2048:
        return None
    try:
        value = ast.parse(expression, mode="eval").body
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_"
            and len(value.args) == 1
            and not value.keywords
        ):
            value = value.args[0]
        result = ast.literal_eval(value)
        return result if isinstance(result, str) and 0 < len(result) <= 512 else None
    except (ValueError, SyntaxError, TypeError, RecursionError):
        return None


def source_config(data):
    text = data.decode("utf-8-sig", errors="replace")
    result = []
    for field, expression in re.findall(
        r"(?m)^\s*(?:define(?:\s+-?\d+)?\s+)?config\.(name|version|window_icon)\s*=\s*([^\n]+)",
        text,
    ):
        value = literal_text(expression)
        if value is not None:
            result.append((FIELDS[field], value))
    return result


def _operands(data):
    """Resolve string/global labels in a bounded token stream, not pickle objects.

    NEWOBJ/REDUCE/BUILD/etc are inert tokens. No classes are imported, no object
    state is rebuilt, and no pickle/marshal loader or callable is invoked.
    """
    memo = {}
    last = None
    strings = []
    for count, (op, arg, _) in enumerate(pickletools.genops(data)):
        if count >= 200_000 or len(memo) > 100_000:
            raise ValueError("Compiled metadata limit")
        name = op.name
        if name in (
            "BINUNICODE",
            "SHORT_BINUNICODE",
            "BINUNICODE8",
            "UNICODE",
            "SHORT_BINSTRING",
            "BINSTRING",
            "STRING",
        ):
            last = ("text", arg) if isinstance(arg, str) and len(arg) <= 4096 else None
        elif name == "GLOBAL":
            last = ("global", arg)
        elif name in ("BINGET", "LONG_BINGET", "GET"):
            last = memo.get(int(arg))
        elif name in ("BINPUT", "LONG_BINPUT", "PUT", "MEMOIZE"):
            key = len(memo) if name == "MEMOIZE" else int(arg)
            if not 0 <= key < 100_000:
                raise ValueError("Invalid compiled memo")
            memo[key] = last
            continue
        elif name == "STACK_GLOBAL":
            last = ("global", strings[-2] + " " + strings[-1]) if len(strings) >= 2 else None
        else:
            last = None
        if last:
            if last[0] == "text":
                strings = (strings + [last[1]])[-2:]
            yield last


def _compiled_block(tokens):
    def after(key):
        for index, (kind, value) in enumerate(tokens[:-1]):
            if (kind, value) == ("text", key) and tokens[index + 1][0] == "text":
                return tokens[index + 1][1]
        return None

    field = after("varname")
    namespace = after("store")
    operator = after("operator")
    if (
        field not in FIELDS
        or namespace not in ("config", "store.config")
        or operator not in (None, "=")
    ):
        return None
    values = set()
    in_code = False
    for kind, value in tokens:
        if (kind, value) == ("text", "code"):
            in_code = True
        if in_code and kind == "text":
            literal = literal_text(value)
            if literal is not None:
                values.add(literal)
    if len(values) == 1:
        return FIELDS[field], values.pop()
    return None


def compiled_config(data):
    try:
        if data.startswith(b"RENPY RPC2"):
            payload = None
            for offset in range(10, min(len(data), 10 + 12 * 8), 12):
                slot, start, length = struct.unpack_from("<III", data, offset)
                if slot == 0:
                    break
                if slot == 1:
                    if not offset + 12 <= start <= len(data) or length > len(data) - start:
                        return []
                    payload = inflate(data[start : start + length], 8 * 1024**2)
                    break
            if payload is None:
                return []
        else:
            payload = inflate(data, 8 * 1024**2)
        result = []
        block = None
        for token in _operands(payload):
            if token == ("global", "renpy.ast Define"):
                if block:
                    item = _compiled_block(block)
                    if item:
                        result.append(item)
                block = []
            elif block is not None:
                if len(block) >= 1024:
                    return []
                block.append(token)
        if block:
            item = _compiled_block(block)
            if item:
                result.append(item)
        return result
    except (ValueError, IndexError, KeyError, TypeError, UnicodeError, struct.error, zlib.error):
        return []


def configuration(resources: Resources):
    project = resources.project
    paths = set()
    for count, (root, dirs, files) in enumerate(os.walk(project / "game", followlinks=False)):
        rel = Path(root).relative_to(project / "game")
        dirs[:] = [
            d
            for d in dirs
            if d not in SKIP and not d.startswith(".") and not (Path(root) / d).is_symlink()
        ]
        if len(rel.parts) >= 4:
            dirs[:] = []
        if count >= 256:
            break
        for name in files[:4096]:
            path = PurePosixPath(rel.as_posix()) / name
            if path.stem in NAMES and path.suffix in (".rpy", ".rpyc"):
                paths.add(str(path))
    for name in resources.names():
        path = PurePosixPath(name)
        if (
            path.stem in NAMES
            and path.suffix in (".rpy", ".rpyc")
            and len(path.parts) <= 5
            and not SKIP.intersection(path.parts[:-1])
        ):
            paths.add(name)
    values = {key: set() for key in FIELDS.values()}
    sources = {}
    consumed = 0
    for name in sorted(paths, key=lambda p: (len(PurePosixPath(p).parts), p))[:64]:
        if name.endswith(".rpyc") and name[:-1] in paths:
            continue
        found = resources.read(name, 1024**2)
        if not found:
            continue
        data, source = found
        consumed += len(data)
        if consumed > 8 * 1024**2:
            break
        items = source_config(data) if name.endswith(".rpy") else compiled_config(data)
        for field, value in items:
            values[field].add(value)
            sources.setdefault(field, []).append(source)
    return {field: next(iter(items)) for field, items in values.items() if len(items) == 1}, sources
