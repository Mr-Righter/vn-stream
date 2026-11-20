"""Bounded extraction into a private staging directory; never use filesystem extractall."""

import shutil
import stat
import tarfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import py7zr
import pyzipper
import rarfile

from vn_stream.i18n import text
from vn_stream.imports.archive_writers import ArchiveSafetyError, BoundedTree, SevenZipFactory

SUPPORTED_EXTENSIONS = (".zip", ".7z", ".rar", ".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".apk")


def supports_archive(path: Path | str) -> bool:
    return str(path).lower().endswith(SUPPORTED_EXTENSIONS)


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 20 * 1024**3
    max_files: int = 100_000
    max_depth: int = 32


def _copy(reader, writer):
    while chunk := reader.read(1024 * 1024):
        writer.write(chunk)
    writer.finish()


def _zip(source, tree, password):
    with pyzipper.AESZipFile(source) as archive:
        for entry in archive.infolist():
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ArchiveSafetyError(text("errors.theArchiveContainsUnsafePathsOrSpecialFiles"))
            tree.add(entry.filename, entry.file_size, entry.is_dir())
        tree.directories()
        for entry in archive.infolist():
            if not entry.is_dir():
                with (
                    ExitStack() as stack,
                    archive.open(
                        entry, pwd=password.encode() if password is not None else None
                    ) as r,
                ):
                    _copy(r, tree.open(entry.filename, stack))


def _tar(source, tree):
    # Validate in a bounded first pass; discard tarfile's otherwise growing header cache.
    with tarfile.open(source, "r|*") as archive:
        for entry in archive:
            if not entry.isdir() and not entry.isreg():
                raise ArchiveSafetyError(text("errors.theArchiveContainsUnsafePathsOrSpecialFiles"))
            tree.add(entry.name, entry.size, entry.isdir(), allow_root_directory=True)
            archive.members.clear()
    tree.directories()
    seen = set()
    with tarfile.open(source, "r|*") as archive:
        for entry in archive:
            path = PurePosixPath(entry.name)
            if (
                path in seen
                or path not in tree.entries
                or tree.entries[path] != (entry.size, entry.isdir())
                or not (entry.isdir() or entry.isreg())
            ):
                raise ArchiveSafetyError(text("errors.theArchiveChangedAfterScanning"))
            seen.add(path)
            if entry.isreg():
                with ExitStack() as stack, archive.extractfile(entry) as reader:
                    _copy(reader, tree.open(entry.name, stack))
            archive.members.clear()
    if seen != set(tree.entries):
        raise ArchiveSafetyError(text("errors.theArchiveChangedAfterScanning"))


def _sevenzip(source, tree, password):
    with py7zr.SevenZipFile(source, password=password) as archive, ExitStack() as stack:
        entries = archive.list()
        for entry in entries:
            if entry.is_symlink or not (entry.is_file or entry.is_directory):
                raise ArchiveSafetyError(text("errors.theArchiveContainsUnsafePathsOrSpecialFiles"))
            tree.add(entry.filename, entry.uncompressed, entry.is_directory)
        tree.directories()
        factory = SevenZipFactory(tree, stack)
        # Every target is validated above; all data goes through our bounded sink.
        archive.extract(targets=[e.filename for e in entries if e.is_file], factory=factory)
        factory.finish()


def _rar(source, tree, password):
    with rarfile.RarFile(source, errors="strict") as archive:
        if archive.needs_password() and password is None:
            raise ArchiveSafetyError(text("errors.anArchivePasswordIsRequired"))
        archive.setpassword(password)
        entries = archive.infolist()
        for entry in entries:
            if (
                entry.is_symlink()
                or not (entry.is_file() or entry.is_dir())
                or getattr(entry, "file_redir", None)
                or (
                    entry.host_os == rarfile.RAR_OS_UNIX
                    and stat.S_IFMT(entry.mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                )
            ):
                raise ArchiveSafetyError(text("errors.theArchiveContainsUnsafePathsOrSpecialFiles"))
            tree.add(entry.filename, entry.file_size, entry.is_dir())
        tree.directories()
        for entry in entries:
            if entry.is_file():
                with ExitStack() as stack, archive.open(entry, pwd=password) as reader:
                    _copy(reader, tree.open(entry.filename, stack))


def extract_archive(
    source: Path,
    destination: Path,
    limits: Limits | None = None,
    *,
    password: str | None = None,
    progress=None,
) -> None:
    if not supports_archive(source):
        raise ValueError(text("errors.unsupportedArchiveFormat"))
    destination.mkdir(parents=True, exist_ok=False)
    tree = BoundedTree(destination, limits or Limits(), progress)
    name = source.name.lower()
    try:
        if name.endswith((".zip", ".apk")):
            _zip(source, tree, password)
        elif name.endswith(".7z"):
            _sevenzip(source, tree, password)
        elif name.endswith(".rar"):
            _rar(source, tree, password)
        else:
            _tar(source, tree)
    except Exception as error:
        shutil.rmtree(destination)
        if isinstance(error, ArchiveSafetyError):
            raise
        # Library exceptions may contain backend command arguments (including passwords).
        raise ValueError(text("errors.unableToExtractTheArchiveDamagedFormatMissingOr")) from None


def normalize_project(staging: Path, *, apk: bool, engine_id=None) -> Path:
    if apk:
        if engine_id not in (None, "renpy"):
            raise ValueError(text("engine.unsupported"))
        assets = staging / "assets"
        if not (assets / "x-game").is_dir():
            raise ValueError(text("errors.theApkIsMissingCompleteRenPyResourcesOr"))
        output = staging / "normalized"
        output.mkdir()
        # Only recognized RAPT asset trees carry the prefix; remove exactly one per segment.
        for tree in assets.iterdir():
            if not tree.name.startswith("x-"):
                continue
            for item in [tree, *tree.rglob("*")] if tree.is_dir() else [tree]:
                parts = item.relative_to(assets).parts
                if any(not part.startswith("x-") or len(part) == 2 for part in parts):
                    raise ValueError(text("errors.apkResourcePathsDoNotMatchASupportedRapt"))
                decoded = tuple(part[2:] for part in parts)
                # A safe archive path can become unsafe after removing RAPT prefixes.
                if any(part in (".", "..") for part in decoded):
                    raise ValueError(text("errors.theRestoredApkPathContainsRelativeSegments"))
                target = output.joinpath(*decoded)
                if not target.resolve().is_relative_to(output.resolve()):
                    raise ValueError(text("errors.theRestoredApkPathEscapesItsDestination"))
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, target)
        private = assets / "private.mp3"
        if private.is_file():
            shutil.copyfile(private, output / "private.mp3")
        return output
    from vn_stream.engines.registry import inspect_project

    candidates = []
    stack = [(staging, 0)]
    examined = 0
    while stack:
        path, depth = stack.pop()
        examined += 1
        if examined > 100000 or depth > 32:
            raise ValueError(text("engine.unrecognized"))
        result = inspect_project(path)
        if result.engine_id is not None or result.status == "conflict":
            candidates.append(path)
            continue
        stack.extend(
            (p, depth + 1)
            for p in path.iterdir()
            if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
        )
    if len(candidates) != 1:
        raise ValueError(text("errors.exactlyOneRenPyProjectRootIsRequired"))
    if engine_id and inspect_project(candidates[0], engine_id=engine_id).engine_id is None:
        raise ValueError(text("engine.unrecognized"))
    return candidates[0]
