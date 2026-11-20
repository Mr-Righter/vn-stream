"""Process-owned LRU cache with reader pins; source revisions remain authoritative."""

import os
import threading
import time
from pathlib import Path

from vn_stream.assets.sources import Reader, Source


class DecodeCache:
    def __init__(self, folder, limit=None, *, max_files=None, max_age=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.limit = max(
            0,
            int(
                os.environ.get("VN_STREAM_ASSET_DECODE_CACHE_BYTES", 2 * 1024**3)
                if limit is None
                else limit
            ),
        )
        self.lock = threading.RLock()
        self.max_files, self.max_age = max_files, max_age
        self.active = {}
        self.used = {}

    def pin(self, key):
        with self.lock:
            self.active[key] = self.active.get(key, 0) + 1
            self.used[key] = time.monotonic()

    def unpin(self, key, *, prune=True, remove=False):
        with self.lock:
            self.active[key] -= 1
            if not self.active[key]:
                del self.active[key]
                if remove:
                    (self.folder / key).unlink(missing_ok=True)
                    self.used.pop(key, None)
            if prune:
                self.prune()

    def prune(self, *, now=None, protect=()):
        with self.lock:
            files = [
                p
                for p in self.folder.iterdir()
                if p.is_file() and not p.is_symlink() and not p.name.endswith(".tmp")
            ]
            total = sum(p.stat().st_size for p in files)
            remaining = len(files)
            now = time.time() if now is None else now
            for path in sorted(files, key=lambda p: self.used.get(p.name, 0)):
                expired = self.max_age is not None and now - path.stat().st_mtime > self.max_age
                too_many = self.max_files is not None and remaining > self.max_files
                if total <= self.limit and not expired and not too_many:
                    continue
                if path.name in self.active or path.name in protect:
                    continue
                try:
                    size = path.stat().st_size
                    path.unlink()
                    self.used.pop(path.name, None)
                    total -= size
                    remaining -= 1
                except FileNotFoundError:
                    pass

    def source(self, key, original, ephemeral=False):
        with self.lock:
            return CachedSource(self, key, original, ephemeral)


class CachedSource(Source):
    def __init__(self, cache, key, original, ephemeral=False):
        self.cache, self.key, self.original, self.ephemeral = cache, key, original, ephemeral
        self.reserved = False
        original.validate()
        super().__init__(cache.folder, Source.describe(cache.folder / key, key))
        cache.pin(key)
        self.reserved = True
        self.record["origin"] = original.record

    def close(self):
        if self.reserved:
            self.reserved = False
            self.cache.unpin(self.key, remove=self.ephemeral)

    def __del__(self):
        self.close()

    def validate(self):
        self.original.validate()
        from vn_stream.assets.sources import revision

        if revision((self.cache.folder / self.key).stat()) != self.record["revision"]:
            raise ValueError("Prepared resource changed")
        return self

    def open(self):
        self.original.validate()
        self.cache.pin(self.key)
        try:
            base = super().open()
            reader = PinnedReader(base, self.cache, self.key, self.ephemeral)
            self.close()
            return reader
        except BaseException:
            self.cache.unpin(self.key)
            raise


class PinnedReader(Reader):
    def __init__(self, base, cache, key, ephemeral):
        self.base, self.cache, self.key, self.ephemeral = base, cache, key, ephemeral
        super().__init__(base.fd, base.chunks, base.expected)
        # The wrapper owns the descriptor; suppress the original owner's finalizer.
        base.fd = os.dup(base.fd)
        base.close()

    def close(self):
        if not self.closed:
            try:
                super().close()
            finally:
                self.cache.unpin(self.key, remove=self.ephemeral)
        else:
            super().close()
