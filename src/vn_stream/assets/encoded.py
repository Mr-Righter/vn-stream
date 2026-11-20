"""Seekable logical archive streams with bounded decompression buffers."""

import io
import os
import zlib

from vn_stream.assets.codecs import BLOCK, decompressed, lzss_decode, spb_parts


class EncodedReader(io.RawIOBase):
    chunks = ()

    def __init__(self, fd, segments, expected, revision_check):
        self.fd, self.segments, self.expected = fd, segments, expected
        self.check = revision_check
        self.size = sum(s["size"] for s in segments)
        self.position, self.buffer = 0, b""
        self.iterator = self._pieces()

    def _pieces(self):
        try:
            yield from self._decoded_pieces()
        except (zlib.error, EOFError) as error:
            raise ValueError("Invalid compressed resource") from error

    def _decoded_pieces(self):
        for segment in self.segments:
            offset, length = segment["offset"], segment["packed"]
            codec = segment["codec"]
            if codec == "nbz":
                offset, length = offset + 4, length - 4

            def incoming(offset=offset, length=length):
                for start in range(0, length, BLOCK):
                    piece = os.pread(self.fd, min(BLOCK, length - start), offset + start)
                    if len(piece) != min(BLOCK, length - start):
                        raise ValueError("Truncated archive")
                    yield piece

            if codec == "lzss":
                yield from lzss_decode(incoming(), segment["size"])
            elif codec == "spb":
                yield from spb_parts(incoming(), segment["size"])
            else:
                yield from decompressed(
                    incoming(), "bz2" if codec == "nbz" else codec, segment["size"]
                )

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        target = offset + (self.position if whence == 1 else self.size if whence == 2 else 0)
        if whence not in (0, 1, 2) or target < 0:
            raise ValueError("Invalid seek")
        if target < self.position:
            self.position, self.buffer, self.iterator = 0, b"", self._pieces()
        while self.position < min(target, self.size):
            self.read(min(BLOCK, target - self.position))
        self.position = target
        return target

    def read(self, size=-1):
        if self.closed:
            raise ValueError("Closed resource")
        self.check(self.fd, self.expected)
        remaining = max(0, self.size - self.position)
        count = remaining if size < 0 else min(size, remaining)
        output = bytearray()
        while len(output) < count:
            if not self.buffer:
                self.buffer = next(self.iterator, b"")
                if not self.buffer:
                    raise ValueError("Truncated logical resource")
            take = min(count - len(output), len(self.buffer))
            output.extend(self.buffer[:take])
            self.buffer = self.buffer[take:]
        self.position += len(output)
        if self.position == self.size and next(self.iterator, None) is not None:
            raise ValueError("Resource exceeds declared size")
        return bytes(output)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def close(self):
        if not self.closed:
            self.iterator.close()
            os.close(self.fd)
        super().close()
