"""Bounded primitive archive decoders; no game code or external plugins."""

import bz2
import struct
import zlib

BLOCK = 64 * 1024


def decompressed(pieces, codec, expected):
    """Yield bounded blocks, rejecting truncated streams and incorrect lengths."""
    if codec == "raw":
        count = 0
        for piece in pieces:
            count += len(piece)
            if count > expected:
                raise ValueError("Resource exceeds declared size")
            yield piece
        if count != expected:
            raise ValueError("Truncated resource")
        return
    decoder = zlib.decompressobj() if codec == "zlib" else bz2.BZ2Decompressor()
    count = 0
    for piece in pieces:
        if decoder.eof:
            raise ValueError("Unexpected trailing compressed data")
        while True:
            output = decoder.decompress(piece, BLOCK)
            count += len(output)
            if count > expected:
                raise ValueError("Resource exceeds declared size")
            if output:
                yield output
            if codec == "zlib":
                piece = decoder.unconsumed_tail
                if not piece:
                    break
            elif decoder.eof or decoder.needs_input:
                break
            else:
                piece = b""
    if not decoder.eof or decoder.unused_data or count != expected:
        raise ValueError("Truncated or invalid compressed resource")


class Bits:
    def __init__(self, pieces):
        self.pieces = iter(pieces)
        self.chunk = b""
        self.index = self.value = self.remaining = 0

    def get(self, count):
        value = 0
        for _ in range(count):
            if not self.remaining:
                if self.index == len(self.chunk):
                    self.chunk = next(self.pieces, b"")
                    self.index = 0
                if self.index == len(self.chunk):
                    raise ValueError("Truncated bitstream")
                self.value = self.chunk[self.index]
                self.index += 1
                self.remaining = 8
            self.remaining -= 1
            value = (value << 1) | ((self.value >> self.remaining) & 1)
        return value


def lzss_decode(pieces, expected):
    bits, ring = Bits(pieces), bytearray(256)
    cursor, count, output = 239, 0, bytearray()
    while count < expected:
        if bits.get(1):
            values = [bits.get(8)]
        else:
            offset, length = bits.get(8), bits.get(4) + 2
            values = None
        for i in range(1 if values else length):
            value = values[0] if values else ring[(offset + i) & 255]
            ring[cursor] = value
            cursor = (cursor + 1) & 255
            output.append(value)
            count += 1
            if count > expected:
                raise ValueError("LZSS exceeds declared size")
            if len(output) >= BLOCK:
                yield bytes(output)
                output.clear()
    if output:
        yield bytes(output)


def spb_parts(pieces, expected):
    bits = Bits(pieces)
    width, height = bits.get(16), bits.get(16)
    stride = (width * 3 + 3) // 4 * 4
    size = 54 + stride * height
    if not width or not height or width * height > 40_000_000 or size != expected:
        raise ValueError("Invalid SPB geometry")
    header = bytearray(54)
    header[:2] = b"BM"
    struct.pack_into("<I", header, 2, size)
    struct.pack_into("<IIIIHH", header, 10, 54, 40, width, height, 1, 24)
    struct.pack_into("<I", header, 34, size - 54)
    yield bytes(header)
    pixels = bytearray(size - 54)
    for channel in range(3):
        value = bits.get(8)
        plane = bytearray([value])
        while len(plane) < width * height:
            mode = bits.get(3)
            if mode == 0:
                plane.extend([value] * 4)
                continue
            count = bits.get(1) + 1 if mode == 7 else mode + 2
            for _ in range(4):
                if count == 8:
                    value = bits.get(8)
                else:
                    delta = bits.get(count)
                    value = (value + ((delta >> 1) + 1 if delta & 1 else -(delta >> 1))) & 255
                plane.append(value)
        for y in range(height):
            row = plane[y * width : (y + 1) * width]
            if y & 1:
                row.reverse()
            start = (height - y - 1) * stride + channel
            pixels[start : start + width * 3 : 3] = row
    for start in range(0, len(pixels), BLOCK):
        yield bytes(pixels[start : start + BLOCK])


def spb_decode(data, expected):
    return b"".join(spb_parts([data], expected))
