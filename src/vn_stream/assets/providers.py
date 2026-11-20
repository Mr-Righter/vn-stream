"""Common bounded enumeration and media sniffing for engine-owned providers."""

import os
from pathlib import Path, PurePosixPath

from vn_stream.assets.sources import Source
from vn_stream.i18n import text

MAX_ENTRIES = 100_000
MAX_BYTES = 20 * 1024**3
INDEX_LIMIT = 16 * 1024**2
SKIP = {".vn-stream", ".renpy-stream", "save", "saves", "savedata", "cache", "__pycache__"}


def logical_path(value):
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or len(path.parts) > 32
        or any(p in ("", ".", "..") for p in value.split("/"))
        or ":" in value
        or "\0" in value
    ):
        raise ValueError("Invalid resource path")
    return path.as_posix()


def media_type(head, name):
    suffix = Path(name).suffix.lower()
    for magic, kind, mime, format in (
        (b"\x89PNG\r\n\x1a\n", "image", "image/png", "PNG"),
        (b"\xff\xd8\xff", "image", "image/jpeg", "JPEG"),
        (b"GIF8", "image", "image/gif", "GIF"),
        (b"BM", "image", "image/bmp", "BMP"),
        (b"TLG5.0\0raw\x1a", "image", "image/x-tlg", "TLG5"),
        (b"TLG6.0\0raw\x1a", "image", "image/x-tlg", "TLG6"),
        (b"TLG0.0\0sds\x1a", "image", "image/x-tlg", "TLG"),
        (b"fLaC", "audio", "audio/flac", "FLAC"),
        (b"ID3", "audio", "audio/mpeg", "MP3"),
        (b"AJPM", "video", "application/x-amv", "AMV"),
    ):
        if head.startswith(magic):
            return dict(kind=kind, mime=mime, format=format, preview_supported=format != "AMV")
    if head.startswith(b"OggS"):
        # The identification packet is bounded by the header sample; never probe each file.
        audio = any(marker in head for marker in (b"OpusHead", b"\x01vorbis", b"Speex   ", b"fLaC"))
        video = not audio and (b"\x80theora" in head or suffix == ".ogv")
        return dict(
            kind="video" if video else "audio",
            mime="video/ogg" if video else "audio/ogg",
            format="OGG",
            preview_supported=True,
        )
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return dict(kind="image", mime="image/webp", format="WEBP", preview_supported=True)
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return dict(kind="audio", mime="audio/wav", format="WAV", preview_supported=True)
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return dict(kind="video", mime="video/x-msvideo", format="AVI", preview_supported=True)
    if head.startswith(b"\x1aE\xdf\xa3"):
        webm = b"webm" in head.lower()
        return dict(
            kind="video",
            mime="video/webm" if webm else "video/x-matroska",
            format="WEBM" if webm else "MKV",
            preview_supported=True,
        )
    if head.startswith((b"\0\0\x01\xba", b"\0\0\x01\xb3")):
        return dict(kind="video", mime="video/mpeg", format="MPEG", preview_supported=True)
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return dict(kind="image", mime="image/avif", format="AVIF", preview_supported=True)
        audio = brand in (b"M4A ", b"M4B ")
        return dict(
            kind="audio" if audio else "video",
            mime="audio/mp4" if audio else "video/mp4",
            format="M4A" if audio else "MP4",
            preview_supported=True,
        )
    formats = {
        ".png": ("image", "image/png"),
        ".jpg": ("image", "image/jpeg"),
        ".jpeg": ("image", "image/jpeg"),
        ".webp": ("image", "image/webp"),
        ".gif": ("image", "image/gif"),
        ".avif": ("image", "image/avif"),
        ".bmp": ("image", "image/bmp"),
        ".ico": ("image", "image/x-icon"),
        ".tlg": ("image", "image/x-tlg"),
        ".ogg": ("audio", "audio/ogg"),
        ".opus": ("audio", "audio/ogg"),
        ".wav": ("audio", "audio/wav"),
        ".mp3": ("audio", "audio/mpeg"),
        ".m4a": ("audio", "audio/mp4"),
        ".aac": ("audio", "audio/aac"),
        ".flac": ("audio", "audio/flac"),
        ".mp4": ("video", "video/mp4"),
        ".webm": ("video", "video/webm"),
        ".mpg": ("video", "video/mpeg"),
        ".mpeg": ("video", "video/mpeg"),
        ".avi": ("video", "video/x-msvideo"),
        ".mkv": ("video", "video/x-matroska"),
        ".ogv": ("video", "video/ogg"),
        ".mov": ("video", "video/quicktime"),
        ".amv": ("video", "application/x-amv"),
    }
    if suffix in formats:
        kind, mime = formats[suffix]
        return dict(
            kind=kind, mime=mime, format=suffix[1:].upper(), preview_supported=suffix != ".amv"
        )
    return None


class NativeAssets:
    categories = ("image", "audio", "video")

    def __init__(self, archive_reader, extensions):
        self.archive_reader, self.extensions = archive_reader, extensions

    def enumerate(self, project, inspection, check):
        sources, warnings, count, total = {}, [], 0, 0
        excluded = set(SKIP)
        if inspection.save_directory:
            excluded.add(Path(inspection.save_directory).parts[0])

        def collect(name, record):
            nonlocal count, total
            check()
            count += 1
            total += record["size"]
            if count > MAX_ENTRIES or total > MAX_BYTES:
                raise ValueError(text("assets.limit"))
            name = logical_path(name)
            if set(Path(record.get("internal_path", name)).parts) & excluded:
                return
            if name in sources:
                raise ValueError("Repeated resource path")
            try:
                with Source(project, record).open() as stream:
                    header_limit = (
                        54 if any(s["codec"] == "spb" for s in record.get("segments", [])) else 512
                    )
                    head = stream.read(min(header_limit, record["size"]))
                media = media_type(head, name)
                if media:
                    record["media"] = media
                    sources[name] = record
                    if not media["preview_supported"]:
                        warnings.append(
                            dict(
                                code="assets.resourcePreviewUnsupported",
                                params={"source": name, "format": media["format"]},
                            )
                        )
            except (OSError, ValueError, EOFError) as error:
                warnings.append(
                    dict(
                        code="assets.resourceUnsupported",
                        params={"source": name},
                        diagnostic=type(error).__name__,
                    )
                )

        for base, dirs, names in os.walk(project, followlinks=False):
            check()
            if Path(base) != project:
                count += 1
                if count > MAX_ENTRIES:
                    raise ValueError(text("assets.limit"))
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in excluded and not d.startswith(".") and not (Path(base) / d).is_symlink()
            )
            if len(Path(base).relative_to(project).parts) >= 32 and dirs:
                raise ValueError(text("assets.limit"))
            for name in sorted(names):
                check()
                path = Path(base) / name
                if path.is_symlink() or not path.is_file() or name.startswith("."):
                    continue
                relative = path.relative_to(project).as_posix()
                if path.suffix.lower() in self.extensions:
                    try:
                        for member, segments, identity in self.archive_reader(path, inspection):
                            member = logical_path(member)
                            record = Source.encoded(path, relative, segments)
                            record["revision"] = identity
                            record.update(archive=relative, internal_path=member)
                            collect(relative + "/" + member, record)
                    except (OSError, ValueError, EOFError, UnicodeError) as error:
                        if error.args and str(error) == str(text("assets.limit")):
                            raise
                        warnings.append(
                            dict(
                                code="assets.archiveUnsupported",
                                params={"source": relative},
                                diagnostic=type(error).__name__,
                            )
                        )
                else:
                    record = Source.describe(path, relative)
                    record.update(archive=None, internal_path=relative)
                    collect("loose/" + relative, record)
        return sources, warnings
