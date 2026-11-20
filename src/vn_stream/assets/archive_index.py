"""Bounded image/virtual-file reads; RPA indexes are data, never callable pickles."""

import pickletools
import zlib
from functools import lru_cache
from pathlib import Path, PurePosixPath

ASSET_LIMIT = 4 * 1024**2
INDEX_LIMIT = 16 * 1024**2
MARK = object()


def safe_path(project: Path, relative: str, limit: int = ASSET_LIMIT) -> Path | None:
    try:
        parts = PurePosixPath(relative)
        if (
            not parts.parts
            or parts.is_absolute()
            or ".." in parts.parts
            or "\\" in relative
            or ":" in relative
            or "\0" in relative
            or len(parts.parts) > 32
        ):
            return None
        path = project / relative
        if not path.resolve().is_relative_to(project.resolve()):
            return None
        current = project
        for part in parts.parts:
            current /= part
            if current.is_symlink():
                return None
        return path if path.is_file() and path.stat().st_size <= limit else None
    except (OSError, ValueError, RecursionError):
        return None


def image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/vnd.microsoft.icon"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def inflate(data: bytes, limit: int) -> bytes:
    decoder = zlib.decompressobj()
    output = decoder.decompress(data, limit + 1)
    if len(output) > limit or decoder.unconsumed_tail or not decoder.eof:
        raise ValueError("Compressed metadata exceeds its limit or is incomplete")
    return output


def primitive_index(data: bytes):
    """Interpret only the primitive RPA index grammar, without pickle.loads.

    GLOBAL, REDUCE, BUILD, constructors, extensions and persistent IDs are rejected.
    Memo references may only refer to already bounded primitive data containers.
    """
    stack, memo, marks = [], {}, []

    def marked():
        index = marks.pop()
        values = stack[index + 1 :]
        del stack[index:]
        return values

    def put(target, items):
        if type(target) is not dict or len(items) % 2:
            raise ValueError("Invalid index mapping")
        for key, value in zip(items[::2], items[1::2], strict=True):
            if type(key) not in (str, bytes) or key in target:
                raise ValueError("Invalid or repeated archive path")
            target[key] = value

    for count, (op, arg, pos) in enumerate(pickletools.genops(data)):
        name = op.name
        if count >= 1_000_000 or len(stack) > 200_000 or len(memo) > 100_000:
            raise ValueError("Index operation limit exceeded")
        if name in ("PROTO", "FRAME"):
            continue
        if name in (
            "BINUNICODE",
            "SHORT_BINUNICODE",
            "BINUNICODE8",
            "UNICODE",
            "SHORT_BINSTRING",
            "BINSTRING",
            "STRING",
            "BINBYTES",
            "SHORT_BINBYTES",
            "BINBYTES8",
            "BININT",
            "BININT1",
            "BININT2",
            "LONG",
            "LONG1",
            "LONG4",
            "INT",
        ):
            if isinstance(arg, (str, bytes)) and len(arg) > ASSET_LIMIT:
                raise ValueError("Index scalar exceeds limit")
            stack.append(arg)
        elif name == "EMPTY_DICT":
            stack.append({})
        elif name == "EMPTY_LIST":
            stack.append([])
        elif name == "EMPTY_TUPLE":
            stack.append(())
        elif name == "MARK":
            if len(marks) >= 32:
                raise ValueError("Index nesting limit exceeded")
            marks.append(len(stack))
            stack.append(MARK)
        elif name in ("BINPUT", "LONG_BINPUT", "PUT", "MEMOIZE"):
            key = len(memo) if name == "MEMOIZE" else int(arg)
            if not 0 <= key < 100_000 or key in memo:
                raise ValueError("Invalid memo")
            memo[key] = stack[-1]
        elif name in ("BINGET", "LONG_BINGET", "GET"):
            stack.append(memo[int(arg)])
        elif name in ("TUPLE1", "TUPLE2", "TUPLE3"):
            size = int(name[-1])
            items = stack[-size:]
            del stack[-size:]
            stack.append(tuple(items))
        elif name == "TUPLE":
            stack.append(tuple(marked()))
        elif name == "LIST":
            stack.append(marked())
        elif name == "DICT":
            values = marked()
            target = {}
            put(target, values)
            stack.append(target)
        elif name == "APPEND":
            value = stack.pop()
            if type(stack[-1]) is not list:
                raise ValueError("Invalid list")
            stack[-1].append(value)
        elif name == "APPENDS":
            values = marked()
            if type(stack[-1]) is not list:
                raise ValueError("Invalid list")
            stack[-1].extend(values)
        elif name == "SETITEM":
            value, key = stack.pop(), stack.pop()
            put(stack[-1], [key, value])
        elif name == "SETITEMS":
            values = marked()
            put(stack[-1], values)
        elif name == "STOP":
            if len(stack) != 1 or marks or pos != len(data) - 1 or type(stack[0]) is not dict:
                raise ValueError("Invalid index end")
            return stack[0]
        else:
            raise ValueError("Unsupported archive index instruction")
    raise ValueError("Missing index end")


@lru_cache(maxsize=8)
def _index(path: str, stamp: int, size: int, inode: int, changed: int = 0) -> dict:
    try:
        with open(path, "rb") as file:
            header = file.readline(128)
            fields = header.split()
            if len(fields) not in (2, 3) or fields[0] not in (b"RPA-2.0", b"RPA-3.0"):
                return {}
            if fields[0] == b"RPA-3.0" and len(fields) != 3:
                return {}
            offset = int(fields[1], 16)
            key = int(fields[2], 16) if len(fields) == 3 else 0
            if not len(header) <= offset < size or not 0 <= key <= 0xFFFFFFFF:
                return {}
            file.seek(offset)
            compressed = file.read(8 * 1024**2 + 1)
            if len(compressed) > 8 * 1024**2:
                return {}
        raw = primitive_index(inflate(compressed, INDEX_LIMIT))
        result = {}
        if len(raw) > 100_000:
            return {}
        chunk_count = 0
        prefix_bytes = 0
        for name, chunks in raw.items():
            if isinstance(name, bytes):
                name = name.decode("utf-8")
            parts = PurePosixPath(name)
            if (
                len(name) > 2048
                or not parts.parts
                or parts.is_absolute()
                or ".." in parts.parts
                or "\\" in name
                or ":" in name
                or "\0" in name
                or len(parts.parts) > 32
            ):
                return {}
            name = str(parts)
            if name in result or type(chunks) is not list or not 0 < len(chunks) <= 1024:
                return {}
            chunk_count += len(chunks)
            if chunk_count > 100_000:
                return {}
            entries = []
            for chunk in chunks:
                if type(chunk) is not tuple or len(chunk) not in (2, 3):
                    return {}
                start, length = chunk[:2]
                if type(start) is not int or type(length) is not int:
                    return {}
                start, length = start ^ key, length ^ key
                prefix = chunk[2] if len(chunk) == 3 else b""
                if type(prefix) not in (str, bytes):
                    return {}
                prefix_bytes += len(prefix)
                if prefix_bytes > INDEX_LIMIT:
                    return {}
                if isinstance(prefix, str):
                    prefix = prefix.encode("latin1")
                if (
                    type(prefix) is not bytes
                    or len(prefix) > ASSET_LIMIT
                    or start < len(header)
                    or length < 0
                    or start + length > offset
                ):
                    return {}
                entries.append((start, length, prefix))
            result[name] = tuple(entries)
        return result
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        OverflowError,
        zlib.error,
        UnicodeError,
    ):
        return {}


class Resources:
    def __init__(self, project: Path):
        self.project = project
        self.archives = []
        self.archive_revisions = {}
        total = 0
        total_chunks = 0
        total_prefix_bytes = 0
        # Ren'Py's default archive precedence is reverse filename order.
        for path in sorted((project / "game").glob("*.rpa"), reverse=True)[:32]:
            safe = safe_path(project, str(path.relative_to(project)), 32 * 1024**3)
            if safe:
                stat = safe.stat()
                captured = [
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                ]
                index = _index(
                    str(safe), stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns
                )
                after = safe.stat()
                if captured != [
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ]:
                    raise ValueError("Archive changed while reading its index")
                self.archive_revisions[safe] = captured
                total += len(index)
                total_chunks += sum(len(chunks) for chunks in index.values())
                total_prefix_bytes += sum(
                    len(prefix) for chunks in index.values() for _, _, prefix in chunks
                )
                if total > 100_000 or total_chunks > 100_000 or total_prefix_bytes > INDEX_LIMIT:
                    break
                self.archives.append((safe, index))

    def names(self):
        return {name for _, index in self.archives for name in index}

    def read(self, relative: str, limit: int = ASSET_LIMIT):
        # Never turn a rejected/oversized loose file into an arbitrary filesystem read.
        path = safe_path(self.project, "game/" + relative, limit)
        if path:
            try:
                return path.read_bytes(), "game/" + relative
            except OSError:
                return None
        for archive, index in self.archives:
            chunks = index.get(relative)
            if not chunks:
                continue
            if sum(length + len(prefix) for _, length, prefix in chunks) > limit:
                return None
            try:
                data = bytearray()
                with archive.open("rb") as file:
                    for offset, length, prefix in chunks:
                        file.seek(offset)
                        piece = file.read(length)
                        if len(piece) != length:
                            return None
                        data.extend(prefix)
                        data.extend(piece)
                return bytes(data), str(archive.relative_to(self.project)) + ":" + relative
            except OSError:
                return None
        return None
