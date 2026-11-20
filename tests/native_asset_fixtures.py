"""Small independently generated archives; never bundle commercial resources."""

import bz2
import struct
import zlib
from io import BytesIO

from PIL import Image


def png(color=(20, 70, 130, 255)):
    output = BytesIO()
    Image.new("RGBA", (8, 6), color).save(output, format="PNG")
    return output.getvalue()


def ns2(entries, encoding="gbk"):
    header = b"".join(
        b'"' + n.encode(encoding) + b'"' + struct.pack("<I", len(d)) for n, d in entries
    )
    return struct.pack("<I", 5 + len(header)) + header + b"\x00" + b"".join(d for _, d in entries)


def nsa(entries, sar=False):
    """entries: name, original bytes, method (0 raw, 1 SPB, 2 LZSS, 4 NBZ)."""
    packed = []
    for _name, data, method in entries:
        if method == 4:
            value = struct.pack(">I", len(data)) + bz2.compress(data)
        elif method == 2:
            bits = "".join("1" + f"{v:08b}" for v in data)
            value = int(bits.ljust((len(bits) + 7) // 8 * 8, "0") or "0", 2).to_bytes(
                (len(bits) + 7) // 8, "big"
            )
        else:
            value = data
        packed.append(value)
    header_size = 6 + sum(len(n.encode("gbk")) + 1 + (8 if sar else 13) for n, _, _ in entries)
    header, offset = bytearray(struct.pack(">HI", len(entries), header_size)), 0
    for (name, data, method), value in zip(entries, packed, strict=True):
        header.extend(name.encode("gbk") + b"\0")
        if not sar:
            header.extend(bytes([method]))
        header.extend(struct.pack(">II", offset, len(value)))
        if not sar:
            header.extend(struct.pack(">I", len(data)))
        offset += len(value)
    return bytes(header) + b"".join(packed)


def xp3(entries, compressed_index=True):
    """entries: name, sequence of (unpacked bytes, compress flag)."""

    def chunk(tag, data):
        return tag + struct.pack("<Q", len(data)) + data

    payload, records = bytearray(), []
    for name, pieces in entries:
        segments, original = bytearray(), b""
        for data, compressed in pieces:
            packed = zlib.compress(data) if compressed else data
            segments.extend(
                struct.pack("<IQQQ", int(compressed), 19 + len(payload), len(data), len(packed))
            )
            payload.extend(packed)
            original += data
        encoded = name.encode("utf-16le")
        info = (
            struct.pack(
                "<IQQH",
                0x80000000,
                len(original),
                sum(
                    struct.unpack_from("<Q", segments, i + 20)[0]
                    for i in range(0, len(segments), 28)
                ),
                len(encoded) // 2,
            )
            + encoded
        )
        records.append(
            chunk(
                b"File",
                chunk(b"info", info)
                + chunk(b"segm", segments)
                + chunk(b"adlr", struct.pack("<I", zlib.adler32(original))),
            )
        )
    index = b"".join(records)
    raw = zlib.compress(index) if compressed_index else index
    header = bytes([int(compressed_index)]) + struct.pack("<Q", len(raw))
    if compressed_index:
        header += struct.pack("<Q", len(index))
    return (
        b"XP3\r\n \n\x1a\x8bg\x01" + struct.pack("<Q", 19 + len(payload)) + payload + header + raw
    )


def tlg5():
    # One RGBA pixel: encoded channel differences reconstruct B20,G40,R60,A128.
    planes = [236, 40, 20, 128]
    return (
        b"TLG5.0\0raw\x1a"
        + bytes([4])
        + struct.pack("<III", 1, 1, 1)
        + struct.pack("<I", 24)
        + b"".join(b"\x01" + struct.pack("<I", 1) + bytes([n]) for n in planes)
    )


def tlg6():
    # One black RGB pixel, zero residual run length one in each channel.
    return (
        b"TLG6.0\0raw\x1a"
        + bytes([3, 0, 0, 0])
        + struct.pack("<III", 1, 1, 2)
        + struct.pack("<I", 2)
        + b"\0\0"
        + (struct.pack("<I", 2) + b"\x02") * 3
    )
