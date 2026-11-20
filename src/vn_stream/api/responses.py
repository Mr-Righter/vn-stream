"""Authenticated HTTP ranges over bounded logical resources, including RPA members."""

import hashlib
from email.utils import formatdate
from uuid import uuid4

from starlette.background import BackgroundTask
from starlette.responses import (
    FileResponse,
    MalformedRangeHeader,
    RangeNotSatisfiable,
    Response,
    StreamingResponse,
)


def resource_response(source, request, media_type, filename=None):
    stream = source.open()
    try:
        metadata = FileResponse(filename or "resource", media_type=media_type, filename=filename)
        etag = '"' + hashlib.sha256(repr(source.record).encode()).hexdigest() + '"'
        headers = dict(metadata.headers)
        headers.update(
            {
                "Accept-Ranges": "bytes",
                "ETag": etag,
                "Last-Modified": formatdate(source.record["revision"][3] / 1e9, usegmt=True),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-cache",
            }
        )
        if request.headers.get("if-none-match") == etag:
            stream.close()
            headers.pop("content-length", None)
            return Response(status_code=304, headers=headers)
        http_range = request.headers.get("range")
        if_range = request.headers.get("if-range")
        if if_range and if_range not in (etag, headers["Last-Modified"]):
            http_range = None
        try:
            ranges = FileResponse._parse_range_header(http_range, source.size) if http_range else []
        except (MalformedRangeHeader, RangeNotSatisfiable):
            stream.close()
            return Response(status_code=416, headers={"Content-Range": f"bytes */{source.size}"})
        multipart = len(ranges) > 1
        boundary = uuid4().hex if multipart else ""

        def part_header(start, end):
            return (
                f"--{boundary}\r\nContent-Type: {media_type}\r\n"
                f"Content-Range: bytes {start}-{end - 1}/{source.size}\r\n\r\n"
            ).encode()

        if multipart:
            headers["content-type"] = f"multipart/byteranges; boundary={boundary}"
            length = sum(len(part_header(a, b)) + b - a + 2 for a, b in ranges) + len(
                f"--{boundary}--\r\n".encode()
            )
        elif ranges:
            start, end = ranges[0]
            headers["Content-Range"] = f"bytes {start}-{end - 1}/{source.size}"
            length = end - start
        else:
            length = source.size
        headers["content-length"] = str(length)

        def body():
            try:
                for start, end in ranges or [(0, source.size)]:
                    if multipart:
                        yield part_header(start, end)
                    stream.seek(start)
                    remaining = end - start
                    while remaining:
                        piece = stream.read(min(512 * 1024, remaining))
                        remaining -= len(piece)
                        yield piece
                    if multipart:
                        yield b"\r\n"
                if multipart:
                    yield f"--{boundary}--\r\n".encode()
            finally:
                stream.close()

        return StreamingResponse(
            body(),
            status_code=206 if ranges else 200,
            headers=headers,
            background=BackgroundTask(stream.close),
        )
    except BaseException:
        stream.close()
        raise
