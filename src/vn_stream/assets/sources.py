"""Seekable, revision-checked views of original loose files and RPA members."""

import base64
import io
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from vn_stream.i18n import text


def revision(value):
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def open_beneath(root, relative):
    parts = Path(relative).parts
    if (
        not parts
        or Path(relative).is_absolute()
        or any(p in ("..", ".") for p in parts)
        or "\\" in relative
    ):
        raise ValueError(text("content.invalid"))
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError(text("content.invalid"))
    return fd


class Reader(io.RawIOBase):
    def __init__(self, fd, chunks, expected):
        self.fd, self.chunks, self.expected = fd, chunks, expected
        self.size = sum(len(prefix) + length for _, length, prefix in chunks)
        self.position = 0

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
        self.position = target
        return target

    def read(self, size=-1):
        if self.closed:
            raise ValueError("Closed resource")
        if revision(os.fstat(self.fd)) != self.expected:
            raise ValueError(text("assets.changed"))
        remaining = max(0, self.size - self.position)
        remaining = remaining if size < 0 else min(size, remaining)
        result = bytearray()
        start = 0
        for offset, length, prefix in self.chunks:
            end = start + len(prefix) + length
            if self.position < end and remaining:
                local = max(0, self.position - start)
                if local < len(prefix):
                    piece = prefix[local : local + remaining]
                    result.extend(piece)
                    remaining -= len(piece)
                    self.position += len(piece)
                    local += len(piece)
                if remaining and local >= len(prefix):
                    count = min(remaining, end - self.position)
                    piece = os.pread(self.fd, count, offset + local - len(prefix))
                    if len(piece) != count:
                        raise ValueError(text("assets.changed"))
                    result.extend(piece)
                    remaining -= len(piece)
                    self.position += len(piece)
            start = end
            if not remaining:
                break
        return bytes(result)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def close(self):
        if not self.closed:
            os.close(self.fd)
        super().close()


class Source:
    def __init__(self, root, record):
        self.root, self.record = Path(root), record
        self.size = record["size"]

    @staticmethod
    def describe(path, relative, chunks=None):
        value = path.stat()
        pieces = chunks if chunks is not None else [(0, value.st_size, b"")]
        return dict(
            path=relative,
            revision=revision(value),
            size=sum(n + len(p) for _, n, p in pieces),
            chunks=[[o, n, base64.b64encode(p).decode("ascii")] for o, n, p in pieces],
        )

    @staticmethod
    def encoded(path, relative, segments):
        return dict(
            path=relative,
            revision=revision(path.stat()),
            size=sum(s["size"] for s in segments),
            segments=segments,
        )

    def open(self):
        fd = open_beneath(self.root, self.record["path"])
        try:
            value = os.fstat(fd)
            if revision(value) != self.record["revision"]:
                raise ValueError(text("assets.changed"))
            if "segments" in self.record:
                from vn_stream.assets.encoded import EncodedReader

                segments = self.record["segments"]
                if not 0 < len(segments) <= 1024:
                    raise ValueError(text("content.invalid"))
                for segment in segments:
                    if (
                        segment.get("codec") not in ("raw", "zlib", "nbz", "lzss", "spb")
                        or any(
                            type(segment.get(k)) is not int or segment[k] < 0
                            for k in ("offset", "packed", "size")
                        )
                        or (segment["codec"] == "raw" and segment["size"] != segment["packed"])
                        or segment["size"] > 20 * 1024**3
                        or segment["offset"] + segment["packed"] > value.st_size
                    ):
                        raise ValueError(text("content.invalid"))
                if sum(s["size"] for s in segments) != self.size:
                    raise ValueError(text("content.invalid"))

                def check(fd, expected):
                    if revision(os.fstat(fd)) != expected:
                        raise ValueError(text("assets.changed"))

                if all(segment["codec"] == "raw" for segment in segments):
                    return Reader(
                        fd, [(s["offset"], s["packed"], b"") for s in segments], revision(value)
                    )
                return EncodedReader(fd, segments, revision(value), check)
            raw = self.record["chunks"]
            if not 0 < len(raw) <= 1024:
                raise ValueError(text("content.invalid"))
            chunks = []
            for offset, length, encoded in raw:
                prefix = base64.b64decode(encoded, validate=True)
                if (
                    type(offset) is not int
                    or type(length) is not int
                    or offset < 0
                    or length < 0
                    or offset + length > value.st_size
                    or len(prefix) > 16 * 1024**2
                ):
                    raise ValueError(text("content.invalid"))
                chunks.append((offset, length, prefix))
            if sum(n + len(p) for _, n, p in chunks) != self.size:
                raise ValueError(text("content.invalid"))
            return Reader(fd, chunks, revision(value))
        except BaseException:
            os.close(fd)
            raise

    def validate(self):
        try:
            with self.open():
                pass
        except OSError as exc:
            raise ValueError(text("assets.changed")) from exc
        return self

    def read_bytes(self, limit=None):
        if limit is not None and self.size > limit:
            raise ValueError(text("assets.limit"))
        with self.open() as source:
            return source.read()

    @contextmanager
    def ffmpeg(self, temporary_root):
        with self.open() as stream:
            # Linux inherited descriptors avoid a second path lookup and symlink races.
            if len(stream.chunks) == 1 and not stream.chunks[0][2]:
                offset, length, _ = stream.chunks[0]
                if length == 0:
                    raise ValueError(text("assets.previewUnavailable"))
                url = f"subfile,,start,{offset},end,{offset + length},,:/proc/self/fd/{stream.fd}"
                yield SimpleNamespace(url=url, pass_fds=(stream.fd,))
            else:
                # Rare prefixed/multipart members require a seekable temporary decoder input.
                if shutil.disk_usage(temporary_root).free < self.size + 16 * 1024**2:
                    raise ValueError(text("assets.noSpace"))
                with tempfile.TemporaryFile(
                    prefix="asset-input-", suffix=".media", dir=temporary_root
                ) as output:
                    while piece := stream.read(1024**2):
                        output.write(piece)
                    output.flush()
                    yield SimpleNamespace(
                        url=f"/proc/self/fd/{output.fileno()}", pass_fds=(output.fileno(),)
                    )
            if revision(os.fstat(stream.fd)) != stream.expected:
                raise ValueError(text("assets.changed"))


@contextmanager
def decoder_input(source, temporary_root):
    if isinstance(source, Source):
        with source.ffmpeg(temporary_root) as value:
            yield value
    else:
        yield SimpleNamespace(url=str(source), pass_fds=())
