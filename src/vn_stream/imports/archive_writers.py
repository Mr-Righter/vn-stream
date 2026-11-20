"""Validated paths and bounded streaming sinks shared by archive readers."""

from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from threading import RLock

from py7zr.io import Py7zIO, WriterFactory

from vn_stream.i18n import text


class ArchiveSafetyError(ValueError):
    """A safe, fixed diagnostic suitable for returning to callers."""


class BoundedTree:
    def __init__(self, destination, limits, progress=None):
        self.progress = progress
        self.destination = destination
        self.limits = limits
        self.entries = {}
        self.parents = set()
        self.declared_bytes = 0
        self.written_bytes = 0
        self.lock = RLock()

    def add(
        self, name: str, size: int, directory: bool, *, allow_root_directory: bool = False
    ) -> Path:
        path = PurePosixPath(name)
        root_directory = allow_root_directory and name in (".", "./") and directory and size == 0
        if (
            (not path.parts and not root_directory)
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or ":" in name
            or "\x00" in name
            or len(path.parts) > self.limits.max_depth
            or size < 0
            or (directory and size != 0)
        ):
            raise ArchiveSafetyError(text("errors.theArchiveContainsUnsafePathsOrSpecialFiles"))
        if path in self.entries:
            raise ArchiveSafetyError(text("errors.theArchiveContainsDuplicatePaths"))
        if len(self.entries) >= self.limits.max_files:
            raise ArchiveSafetyError(text("errors.theArchiveContainsTooManyFiles"))
        if any(parent in self.entries and not self.entries[parent][1] for parent in path.parents):
            raise ArchiveSafetyError(text("errors.theArchiveContainsConflictingFileAndFolderPaths"))
        if not directory and path in self.parents:
            raise ArchiveSafetyError(text("errors.theArchiveContainsConflictingFileAndFolderPaths"))
        self.declared_bytes += size
        if self.declared_bytes > self.limits.max_bytes:
            raise ArchiveSafetyError(text("errors.theArchiveExceedsTheExtractionSizeLimit"))
        self.parents.update(path.parents)
        self.entries[path] = (size, directory)
        return self.destination.joinpath(*path.parts)

    def open(self, name, stack, *, close_when_complete=False):
        path = PurePosixPath(name)
        size, directory = self.entries[path]
        if directory:
            raise ArchiveSafetyError(text("errors.invalidArchiveMemberType"))
        target = self.destination.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return BoundedWriter(
            stack.enter_context(target.open("xb")), size, self, close_when_complete
        )

    def directories(self):
        if self.progress:
            self.progress(0, self.declared_bytes)
        for path, (_, directory) in self.entries.items():
            if directory:
                self.destination.joinpath(*path.parts).mkdir(parents=True, exist_ok=True)


class BoundedWriter(Py7zIO):
    def __init__(self, file, expected, tree, close_when_complete=False):
        self.file = file
        self.expected = expected
        self.tree = tree
        self.written = 0
        self.close_when_complete = close_when_complete
        if close_when_complete and expected == 0:
            self.file.close()

    def write(self, data):
        with self.tree.lock:
            if (
                self.written + len(data) > self.expected
                or self.tree.written_bytes + len(data) > self.tree.limits.max_bytes
            ):
                raise ArchiveSafetyError(text("errors.extractedDataExceedsTheSizeLimit"))
            count = self.file.write(data)
            self.written += count
            self.tree.written_bytes += count
            if self.tree.progress:
                self.tree.progress(self.tree.written_bytes, self.tree.declared_bytes)
            if self.close_when_complete and self.written == self.expected:
                self.file.close()
            return count

    def read(self, size=None):
        return self.file.read(-1 if size is None else size)

    def seek(self, offset, whence=0):
        # py7zr rewinds after writing, but extraction never needs random writes.
        return offset if self.file.closed else self.file.seek(offset, whence)

    def flush(self):
        if not self.file.closed:
            self.file.flush()

    def size(self):
        return self.written

    def finish(self):
        if self.written != self.expected:
            raise ArchiveSafetyError(text("errors.archiveMemberSizesDoNotMatchTheirDeclarations"))


class SevenZipFactory(WriterFactory):
    def __init__(self, tree: BoundedTree, stack: ExitStack):
        self.tree = tree
        self.stack = stack
        self.writers = {}

    def create(self, filename):
        with self.tree.lock:
            name = PurePosixPath(filename)
            if name in self.writers or name not in self.tree.entries:
                raise ArchiveSafetyError(text("errors.theArchiveContainsDuplicateOrUnknownPaths"))
            writer = self.tree.open(name, self.stack, close_when_complete=True)
            self.writers[name] = writer
            return writer

    def finish(self):
        expected = {path for path, (_, directory) in self.tree.entries.items() if not directory}
        if set(self.writers) != expected:
            raise ArchiveSafetyError(text("errors.archiveMembersAreIncomplete"))
        for writer in self.writers.values():
            writer.finish()
