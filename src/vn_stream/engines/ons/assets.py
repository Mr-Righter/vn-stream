"""Static standard SAR/NSA/NS2 tables; archive paths never become output paths."""

import os
import struct

from vn_stream.assets.providers import INDEX_LIMIT, MAX_BYTES, MAX_ENTRIES, NativeAssets
from vn_stream.assets.sources import revision


def read_archive(path, inspection):
    encoding = {"utf8": "utf-8", "gbk": "gb18030", "sjis": "cp932"}.get(
        inspection.encoding, "gb18030"
    )
    with path.open("rb") as stream:
        identity = revision(os.fstat(stream.fileno()))
        size = identity[2]

        def read(n):
            if stream.tell() + n > min(size, INDEX_LIMIT):
                raise ValueError("Archive index limit")
            value = stream.read(n)
            if len(value) != n:
                raise ValueError("Truncated index")
            return value

        ns2 = path.suffix.lower() == ".ns2"
        sar = path.suffix.lower() == ".sar"
        if ns2:
            base = struct.unpack("<I", read(4))[0]
            count = MAX_ENTRIES + 1
        else:
            count, base = struct.unpack(">HI", read(6))
        if not 4 <= base <= min(size, INDEX_LIMIT) or count > MAX_ENTRIES and not ns2:
            raise ValueError("Invalid archive index")
        offset = base
        for _ in range(count):
            if ns2:
                if read(1) != b'"':
                    break
                stop = b'"'
            else:
                stop = b"\0"
            name = bytearray()
            while True:
                byte = read(1)
                if byte == stop:
                    break
                name.extend(byte)
                if len(name) > 4096 or stream.tell() >= base:
                    raise ValueError("Invalid filename")
            name = bytes(name).decode(encoding, errors="strict")
            if ns2:
                packed = struct.unpack("<I", read(4))[0]
                method, original = 0, packed
                start = offset
                offset += packed
            else:
                method = 0 if sar else read(1)[0]
                start, packed = struct.unpack(">II", read(8))
                start += base
                original = packed if sar else struct.unpack(">I", read(4))[0]
            if (
                stream.tell() > base
                or start < base
                or start + packed > size
                or original > MAX_BYTES
            ):
                raise ValueError("Invalid resource bounds")
            codec = {0: "raw", 1: "spb", 2: "lzss", 4: "nbz"}.get(method)
            if codec is None:
                raise ValueError("Unsupported compression")
            suffix = name.lower().rsplit(".", 1)[-1]
            if codec == "raw" and suffix in ("nbz", "spb"):
                codec = suffix
            if codec in ("nbz", "spb"):
                position = stream.tell()
                stream.seek(start)
                head = stream.read(4)
                stream.seek(position)
                if len(head) != 4:
                    raise ValueError("Truncated resource")
                if codec == "nbz":
                    original = struct.unpack(">I", head)[0]
                else:
                    w, h = struct.unpack(">HH", head)
                    if not w or not h or w * h > 40_000_000:
                        raise ValueError("SPB geometry limit")
                    original = 54 + ((w * 3 + 3) // 4 * 4) * h
            yield name, [dict(offset=start, packed=packed, size=original, codec=codec)], identity
        else:
            if ns2:
                raise ValueError("Archive entry limit")


PROVIDER = NativeAssets(read_archive, {".sar", ".nsa", ".ns2"})
