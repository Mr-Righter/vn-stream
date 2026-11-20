"""Standard XP3 primitive chunk parser; deliberately never loads game filters."""

import os
import struct

from vn_stream.assets.archive_index import inflate
from vn_stream.assets.providers import INDEX_LIMIT, MAX_BYTES, MAX_ENTRIES, NativeAssets
from vn_stream.assets.sources import revision

MAGIC = b"XP3\r\n \n\x1a\x8bg\x01"


def chunks(data):
    position = 0
    while position < len(data):
        if position + 12 > len(data):
            raise ValueError("Truncated chunk")
        kind = data[position : position + 4]
        length = struct.unpack_from("<Q", data, position + 4)[0]
        start = position + 12
        if start + length > len(data):
            raise ValueError("Invalid chunk length")
        yield kind, data[start : start + length]
        position = start + length


def read_archive(path, inspection):
    with path.open("rb") as stream:
        identity = revision(os.fstat(stream.fileno()))
        size = identity[2]

        def read(n):
            data = stream.read(n)
            if len(data) != n:
                raise ValueError("Truncated archive")
            return data

        if read(11) != MAGIC:
            raise ValueError("Invalid XP3 magic")
        offset = struct.unpack("<Q", read(8))[0]
        seen, total, count = set(), 0, 0
        while True:
            if offset in seen or not 19 <= offset < size or len(seen) >= 1024:
                raise ValueError("Invalid XP3 index chain")
            seen.add(offset)
            stream.seek(offset)
            flag = read(1)[0]
            packed = struct.unpack("<Q", read(8))[0]
            unpacked = struct.unpack("<Q", read(8))[0] if flag & 7 == 1 else packed
            total += unpacked
            if (
                flag & 7 not in (0, 1)
                or max(packed, total) > INDEX_LIMIT
                or stream.tell() + packed > size
            ):
                raise ValueError("XP3 index limit")
            data = read(packed)
            data = inflate(data, unpacked) if flag & 7 == 1 else data
            if len(data) != unpacked:
                raise ValueError("Invalid XP3 index size")
            next_offset = struct.unpack("<Q", read(8))[0] if flag & 128 else None
            for kind, body in chunks(data):
                if kind != b"File":
                    continue
                count += 1
                if count > MAX_ENTRIES:
                    raise ValueError("XP3 entry limit")
                fields = {}
                for tag, value in chunks(body):
                    if tag in fields:
                        raise ValueError("Duplicate XP3 field")
                    fields[tag] = value
                info = fields.get(b"info", b"")
                raw = fields.get(b"segm", b"")
                if len(info) < 22 or not raw or len(raw) % 28 or len(raw) // 28 > 1024:
                    raise ValueError("Invalid XP3 file")
                _, original, stored, chars = struct.unpack_from("<IQQH", info)
                if len(info) != 22 + chars * 2 or original > MAX_BYTES:
                    raise ValueError("Invalid XP3 metadata")
                name = info[22:].decode("utf-16le", errors="strict")
                segments = []
                for i in range(0, len(raw), 28):
                    method, start, length, packed = struct.unpack_from("<IQQQ", raw, i)
                    if method & 7 not in (0, 1) or start + packed > size or start < 19:
                        raise ValueError("Unsupported XP3 segment")
                    segments.append(
                        dict(
                            offset=start,
                            packed=packed,
                            size=length,
                            codec="zlib" if method & 7 else "raw",
                        )
                    )
                if (
                    sum(s["size"] for s in segments) != original
                    or sum(s["packed"] for s in segments) != stored
                ):
                    raise ValueError("Invalid XP3 totals")
                yield name, segments, identity
            if next_offset is None:
                break
            offset = next_offset


PROVIDER = NativeAssets(read_archive, {".xp3"})
