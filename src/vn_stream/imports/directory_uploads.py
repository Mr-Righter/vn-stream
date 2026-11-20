"""Persistent directory manifests and offsets; uploaded trees never register in place."""

import hashlib
import json
import os
import re
import shutil
import time
from uuid import uuid4

from vn_stream.i18n import text

CHUNK = 8 * 1024**2


class DirectoryUploadError(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class DirectoryUploads:
    def __init__(self, library, jobs):
        self.library = library
        self.jobs = jobs
        self.root = library.library_root / ".vn-stream-uploads"
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.maximum = min(
            int(os.environ.get("VN_STREAM_UPLOAD_MAX_BYTES", 20 * 1024**3)), 20 * 1024**3
        )
        with library.db.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS directory_uploads (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, file_count INTEGER NOT NULL,
                    total_bytes INTEGER NOT NULL, state TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS directory_upload_files (
                    id TEXT PRIMARY KEY, upload_id TEXT NOT NULL, path TEXT NOT NULL,
                    size INTEGER NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(upload_id, path));
                CREATE INDEX IF NOT EXISTS directory_upload_files_owner
                    ON directory_upload_files(upload_id);
                CREATE TABLE IF NOT EXISTS directory_upload_batches (
                    upload_id TEXT NOT NULL, batch_id TEXT NOT NULL, digest TEXT NOT NULL,
                    file_ids TEXT NOT NULL, PRIMARY KEY(upload_id, batch_id));
            """)

    @staticmethod
    def safe_path(path, *, name=False):
        parts = path.split("/")
        if (
            not path
            or len(path) > (255 if name else 4096)
            or "\\" in path
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(p in ("", ".", "..", ".vn-stream") for p in parts)
            or any(":" in p for p in parts)
            or len(parts) - 1 > 32
            or (name and len(parts) != 1)
        ):
            raise DirectoryUploadError(400, text("errors.invalidRelativeFolderOrFilePath"))
        return path

    @staticmethod
    def valid_id(uid):
        if not re.fullmatch(r"[0-9a-f]{32}", uid):
            raise KeyError(uid)

    def record(self, uid):
        self.valid_id(uid)
        with self.library.db.transaction() as db:
            row = db.execute("SELECT * FROM directory_uploads WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise KeyError(uid)
        return dict(
            zip(("id", "name", "file_count", "total_bytes", "state", "updated"), row, strict=True)
        )

    def project(self, uid):
        self.valid_id(uid)
        return self.root / uid / "project"

    def _unbound(self, uid):
        if self.jobs.bound_directory(uid):
            raise DirectoryUploadError(409, text("errors.theFolderUploadIsAlreadyLinkedToAnImport"))

    def _touch(self, db, uid):
        db.execute("UPDATE directory_uploads SET updated = ? WHERE id = ?", (time.time(), uid))

    def create(self, name, file_count, total_bytes):
        self.safe_path(name, name=True)
        if total_bytes > self.maximum:
            raise DirectoryUploadError(413, text("errors.theFolderUploadExceedsTheSizeLimit"))
        with self.library.db.transaction() as db:
            old = list(db.execute("SELECT id, updated FROM directory_uploads"))
        for uid, updated in old:
            if time.time() - updated > 86400 and not self.jobs.bound_directory(uid):
                self.remove(uid)
        with self.library.db.transaction() as db:
            if db.execute("SELECT 1 FROM directory_uploads LIMIT 1").fetchone():
                raise DirectoryUploadError(
                    409, text("errors.completeOrCancelTheExistingFolderUploadFirst")
                )
        if shutil.disk_usage(self.root).free < total_bytes + 128 * 1024**2:
            raise DirectoryUploadError(400, text("errors.notEnoughServerStorage"))
        uid = uuid4().hex
        # Commit ownership first so a restart/TTL can reclaim a partial mkdir.
        with self.library.db.transaction() as db:
            db.execute(
                "INSERT INTO directory_uploads VALUES (?, ?, ?, ?, 'manifest', ?)",
                (uid, name, file_count, total_bytes, time.time()),
            )
        self.project(uid).mkdir(parents=True, mode=0o700)
        return self.status(uid)

    def active(self):
        with self.library.db.transaction() as db:
            row = db.execute("SELECT id FROM directory_uploads ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return None
        try:
            return self.status(row[0])
        except KeyError as error:
            if error.args != (row[0],):
                raise
            # Successful import cleanup can remove this record after the ID lookup.
            with self.library.db.transaction() as db:
                if db.execute("SELECT 1 FROM directory_uploads WHERE id = ?", row).fetchone():
                    raise
            return None

    def status(self, uid, cursor=0, limit=1000):
        record = self.record(uid)
        with self.library.db.transaction() as db:
            count, size, uploaded = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0), COALESCE(SUM(offset), 0) "
                "FROM directory_upload_files WHERE upload_id = ?",
                (uid,),
            ).fetchone()
            files = [
                dict(zip(("id", "path", "size", "offset"), row, strict=True))
                for row in db.execute(
                    "SELECT id, path, size, offset FROM directory_upload_files "
                    "WHERE upload_id = ? ORDER BY rowid LIMIT ? OFFSET ?",
                    (uid, limit, cursor),
                )
            ]
        record.pop("updated")
        return {
            **record,
            "manifest_count": count,
            "manifest_bytes": size,
            "uploaded_bytes": uploaded,
            "files": files,
            "next_cursor": cursor + len(files) if cursor + len(files) < count else None,
            "chunk_bytes": CHUNK,
        }

    def manifest(self, uid, batch_id, files):
        record = self.record(uid)
        self._unbound(uid)
        canonical = json.dumps(files, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.library.db.transaction() as db:
            previous = db.execute(
                "SELECT digest, file_ids FROM directory_upload_batches "
                "WHERE upload_id = ? AND batch_id = ?",
                (uid, batch_id),
            ).fetchone()
            if previous:
                if previous[0] != digest:
                    raise DirectoryUploadError(
                        409, text("errors.theBatchNumberBelongsToAnotherManifest")
                    )
                ids = json.loads(previous[1])
                # Preserve the submitted order, with current durable offsets on retries.
                rows = {
                    row[0]: dict(zip(("id", "path", "size", "offset"), row, strict=True))
                    for row in db.execute(
                        "SELECT id, path, size, offset FROM "
                        "directory_upload_files WHERE upload_id = ?",
                        (uid,),
                    )
                }
                self._touch(db, uid)
                return {"files": [rows[file_id] for file_id in ids]}
            if record["state"] != "manifest":
                raise DirectoryUploadError(409, text("errors.theFolderManifestIsSealed"))
            existing = set(
                row[0]
                for row in db.execute(
                    "SELECT path FROM directory_upload_files WHERE upload_id = ?", (uid,)
                )
            )
            # Also index directory names to catch a later file replacing an existing parent.
            directories = {
                "/".join(p.split("/")[:i]) for p in existing for i in range(1, len(p.split("/")))
            }
            output = []
            for file in files:
                path = self.safe_path(file["path"])
                parents = ["/".join(path.split("/")[:i]) for i in range(1, len(path.split("/")))]
                if path in existing or path in directories or any(p in existing for p in parents):
                    raise DirectoryUploadError(
                        400,
                        text("errors.theManifestContainsDuplicatePathsOrConflictingParentFiles"),
                    )
                existing.add(path)
                directories.update(parents)
                output.append({"id": uuid4().hex, **file, "offset": 0})
            count, size = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM "
                "directory_upload_files WHERE upload_id = ?",
                (uid,),
            ).fetchone()
            if (
                count + len(files) > record["file_count"]
                or size + sum(f["size"] for f in files) > record["total_bytes"]
            ):
                raise DirectoryUploadError(
                    409, text("errors.theManifestExceedsTheDeclaredFileCountOrTotal")
                )
            db.executemany(
                "INSERT INTO directory_upload_files VALUES (?, ?, ?, ?, 0)",
                [(f["id"], uid, f["path"], f["size"]) for f in output],
            )
            db.execute(
                "INSERT INTO directory_upload_batches VALUES (?, ?, ?, ?)",
                (uid, batch_id, digest, json.dumps([f["id"] for f in output])),
            )
            self._touch(db, uid)
        return {"files": output}

    def seal(self, uid):
        self.record(uid)
        self._unbound(uid)
        status = self.status(uid, limit=1)
        if (
            status["manifest_count"] != status["file_count"]
            or status["manifest_bytes"] != status["total_bytes"]
        ):
            raise DirectoryUploadError(
                409, text("errors.theManifestDoesNotMatchTheDeclaredFileCount")
            )
        with self.library.db.transaction() as db:
            db.execute(
                "UPDATE directory_uploads SET state = 'uploading' WHERE id = ? "
                "AND state = 'manifest'",
                (uid,),
            )
            self._touch(db, uid)
        return self.status(uid)

    def file(self, uid, file_id):
        self.record(uid)
        self.valid_id(file_id)
        with self.library.db.transaction() as db:
            row = db.execute(
                "SELECT id, path, size, offset FROM directory_upload_files "
                "WHERE upload_id = ? AND id = ?",
                (uid, file_id),
            ).fetchone()
        if row is None:
            raise KeyError(file_id)
        return dict(zip(("id", "path", "size", "offset"), row, strict=True))

    def file_path(self, uid, entry):
        root = self.project(uid)
        path = root / entry["path"]
        if any(p.is_symlink() for p in (self.root, root.parent, root, *path.parents, path)):
            raise DirectoryUploadError(400, text("errors.invalidUploadStagingFolder"))
        return path

    def open_append(self, uid, file_id, offset):
        record = self.record(uid)
        self._unbound(uid)
        if record["state"] != "uploading":
            raise DirectoryUploadError(409, text("errors.sealTheFolderManifestFirst"))
        entry = self.file(uid, file_id)
        if offset != entry["offset"]:
            raise DirectoryUploadError(
                409, text("errors.theUploadOffsetChangedReloadItsStatusAndRetry")
            )
        path = self.file_path(uid, entry)
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        writer = path.open("r+b" if path.exists() else "w+b")
        if writer.seek(0, 2) < offset:
            writer.close()
            raise DirectoryUploadError(
                409, text("errors.stagedFilesAreIncompleteCancelAndUploadAgain")
            )
        # A crash after writing but before committing offset leaves unacknowledged bytes.
        writer.truncate(offset)
        writer.seek(offset)
        return entry, writer

    def commit_append(self, uid, entry, offset):
        with self.library.db.transaction() as db:
            db.execute(
                "UPDATE directory_upload_files SET offset = ? WHERE id = ? AND upload_id = ?",
                (offset, entry["id"], uid),
            )
            self._touch(db, uid)
        return {**entry, "offset": offset}

    def complete(self, uid):
        record = self.record(uid)
        self._unbound(uid)
        if record["state"] == "manifest":
            raise DirectoryUploadError(409, text("errors.sealTheFolderManifestFirst"))
        with self.library.db.transaction() as db:
            entries = [
                dict(zip(("id", "path", "size", "offset"), row, strict=True))
                for row in db.execute(
                    "SELECT id, path, size, offset FROM directory_upload_files WHERE upload_id = ?",
                    (uid,),
                )
            ]
        if (
            len(entries) != record["file_count"]
            or sum(f["size"] for f in entries) != record["total_bytes"]
            or any(f["offset"] != f["size"] for f in entries)
        ):
            raise DirectoryUploadError(409, text("errors.someFolderFilesHaveNotBeenUploaded"))
        for entry in entries:
            path = self.file_path(uid, entry)
            if entry["size"] == 0 and not path.exists():
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                path.touch(mode=0o600)
            if not path.is_file() or path.stat().st_size != entry["size"]:
                raise DirectoryUploadError(
                    409, text("errors.stagedFilesAreIncompleteCancelAndUploadAgain")
                )
        with self.library.db.transaction() as db:
            db.execute("UPDATE directory_uploads SET state = 'complete' WHERE id = ?", (uid,))
            self._touch(db, uid)
        return self.status(uid)

    def source(self, uid):
        if self.record(uid)["state"] != "complete":
            raise DirectoryUploadError(409, text("errors.theFolderUploadIsNotComplete"))
        root = self.project(uid)
        if root.is_symlink() or not root.is_dir():
            raise DirectoryUploadError(409, text("errors.folderUploadStagingFilesAreMissing"))
        return root

    def remove(self, uid):
        self.record(uid)
        shutil.rmtree(self.root / uid, ignore_errors=False) if (self.root / uid).exists() else None
        with self.library.db.transaction() as db:
            db.execute("DELETE FROM directory_upload_files WHERE upload_id = ?", (uid,))
            db.execute("DELETE FROM directory_upload_batches WHERE upload_id = ?", (uid,))
            db.execute("DELETE FROM directory_uploads WHERE id = ?", (uid,))

    def cancel(self, uid):
        self._unbound(uid)
        self.remove(uid)
        return {"cancelled": True}
