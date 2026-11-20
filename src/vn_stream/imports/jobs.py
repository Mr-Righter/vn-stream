"""One bounded import worker with durable publication and source-cleanup checkpoints.

Passwords are thread arguments only. SQLite records contain no password or backend error
strings; game publication and the job's durable result use the same transaction.
"""

import json
import os
import shutil
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from vn_stream.engines.registry import ADAPTERS
from vn_stream.i18n import text
from vn_stream.imports.archive import supports_archive
from vn_stream.imports.directory_uploads import DirectoryUploads
from vn_stream.imports.publication import ImportPublication
from vn_stream.imports.repository import ImportRepository
from vn_stream.projects.models import Project
from vn_stream.storage.workers import WorkerGroup


class ImportJobs:
    def __init__(self, library, describe=None, fallback=None, *, recover=True):
        self.library = library
        self.publication = ImportPublication(library)
        self.repository = ImportRepository(library.db)
        self.describe = describe or asdict
        self.fallback = fallback or asdict
        self.guard = threading.Lock()
        self.workers = WorkerGroup("import")
        library.maintenance.register("imports", self.active_claims)
        with library.db.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS import_jobs ("
                "id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, "
                "upload_id TEXT UNIQUE, state TEXT NOT NULL, record TEXT NOT NULL, directory_upload_id TEXT)"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS import_jobs_directory_upload "
                "ON import_jobs(directory_upload_id)"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS import_jobs_one_running "
                "ON import_jobs(state) WHERE state = 'running'"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS import_request_aliases "
                "(request_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES import_jobs(id))"
            )
        self.directories = DirectoryUploads(library, self)
        if recover:
            self.recover()

    @contextmanager
    def operation(self):
        if not self.guard.acquire(blocking=False):
            raise ValueError(text("errors.anUploadOrImportRequestIsBeingProcessedPlease"))
        try:
            yield
        finally:
            self.guard.release()

    def rows(self):
        with self.library.db.transaction() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT record FROM import_jobs ORDER BY rowid DESC")
            ]

    def _read(self, uid):
        with self.library.db.transaction() as db:
            row = db.execute("SELECT record FROM import_jobs WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise KeyError(uid)
        return json.loads(row[0])

    def _public(self, record):
        return {
            key: record.get(key)
            for key in (
                "id",
                "request_id",
                "state",
                "phase",
                "completed_bytes",
                "total_bytes",
                "error",
                "messages",
                "warning",
                "project",
                "source_name",
                "engine_id",
                "upload_id",
                "directory_upload_id",
                "abandoned",
                "delete_source",
            )
        }

    def get(self, uid):
        return self._public(self._read(uid))

    def active(self):
        rows = self.rows()
        active = next((r for r in rows if r["state"] == "running"), None)
        failure = next((r for r in rows if r["state"] == "failed" and not r["abandoned"]), None)
        return self._public(active or failure) if active or failure else None

    def bound(self, upload_id):
        with self.library.db.transaction() as db:
            return (
                db.execute(
                    "SELECT 1 FROM import_jobs WHERE upload_id = ? AND state != 'completed'",
                    (upload_id,),
                ).fetchone()
                is not None
            )

    def bound_directory(self, uid):
        with self.library.db.transaction() as db:
            return (
                db.execute(
                    "SELECT 1 FROM import_jobs WHERE directory_upload_id = ? AND state != 'completed'",
                    (uid,),
                ).fetchone()
                is not None
            )

    def _upload_source(self, upload_id):
        if len(upload_id) != 32 or any(c not in "0123456789abcdef" for c in upload_id):
            raise ValueError(text("errors.uploadNotFound"))
        folder = self.library.data / "uploads/archives" / upload_id
        try:
            meta = json.loads((folder / "meta.json").read_text())
            name = meta["filename"]
            if Path(name).name != name or "\\" in name or not supports_archive(name):
                raise ValueError(text("errors.invalidUploadFilename"))
            if (folder / "content").stat().st_size != meta["size"]:
                raise ValueError(text("errors.theUploadIsNotComplete"))
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            raise ValueError(text("errors.theUploadIsMissingOrIncomplete")) from None
        archive = folder / name
        if not archive.exists():
            os.link(folder / "content", archive)
        return archive

    @staticmethod
    def _identity(source):
        stat = source.stat(follow_symlinks=False)
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]

    def create(
        self,
        request_id,
        *,
        path=None,
        upload_id=None,
        directory_upload_id=None,
        password=None,
        delete_source=False,
        engine_id=None,
    ):
        if (
            (engine_id is not None and engine_id not in ADAPTERS)
            or not request_id
            or len(request_id) > 200
            or sum(s is not None for s in (path, upload_id, directory_upload_id)) != 1
        ):
            raise ValueError(text("errors.provideExactlyOneSourceAndARequestId"))
        with self.operation(), self.library.lock:
            with self.library.db.transaction() as db:
                row = db.execute(
                    "SELECT record FROM import_jobs WHERE request_id = ? OR id = "
                    "(SELECT job_id FROM import_request_aliases WHERE request_id = ?)",
                    (request_id, request_id),
                ).fetchone()
                if row:
                    existing = json.loads(row[0])
                    same_source = (
                        existing.get("directory_upload_id") == directory_upload_id
                        if directory_upload_id
                        else existing["upload_id"] == upload_id
                        if upload_id
                        else existing["upload_id"] is None
                        and existing.get("directory_upload_id") is None
                        and existing["source"] == str(Path(path).resolve())
                    )
                    if (
                        not same_source
                        or existing["delete_source"] != bool(delete_source)
                        or existing.get("engine_id") != engine_id
                    ):
                        raise ValueError(text("errors.theRequestIdBelongsToAnotherImportRequest"))
                    return self._public(existing)
                if upload_id or directory_upload_id:
                    column = "directory_upload_id" if directory_upload_id else "upload_id"
                    row = db.execute(
                        f"SELECT record FROM import_jobs WHERE {column} = ?",
                        (directory_upload_id or upload_id,),
                    ).fetchone()
                    if row:
                        existing = json.loads(row[0])
                        if (
                            existing["delete_source"] != bool(delete_source)
                            or existing.get("engine_id") != engine_id
                        ):
                            raise ValueError(
                                text("errors.theUploadIsLinkedToDifferentCleanupOptions")
                            )
                        db.execute(
                            "INSERT INTO import_request_aliases VALUES (?, ?)",
                            (request_id, existing["id"]),
                        )
                        return self._public(existing)
                if db.execute("SELECT 1 FROM import_jobs WHERE state = 'running'").fetchone():
                    raise ValueError(text("errors.anImportTaskIsAlreadyRunning"))
            source = (
                self.directories.source(directory_upload_id)
                if directory_upload_id
                else self._upload_source(upload_id)
                if upload_id
                else self.publication.validate_source(Path(path))
            )
            if source.is_dir() and delete_source:
                raise ValueError(text("errors.sourceFoldersCannotBeDeleted"))
            if not source.is_dir() and not supports_archive(source):
                raise ValueError(text("errors.unsupportedArchiveFormat"))
            uid = uuid4().hex
            directory_size = (
                self.directories.record(directory_upload_id)["total_bytes"]
                if directory_upload_id
                else None
            )
            record = dict(
                id=uid,
                engine_id=engine_id,
                request_id=request_id,
                state="running",
                phase="scanning",
                completed_bytes=directory_size or 0,
                total_bytes=directory_size,
                error=None,
                warning=None,
                project=None,
                source_name=(
                    self.directories.record(directory_upload_id)["name"]
                    if directory_upload_id
                    else source.name
                ),
                upload_id=upload_id,
                directory_upload_id=directory_upload_id,
                abandoned=False,
                source=str(source),
                delete_source=bool(delete_source),
                identity=self._identity(source),
                reserved_project=None,
                registered=False,
                staging=None,
                cleanup_done=False,
            )
            with self.library.db.transaction() as db:
                db.execute(
                    "INSERT INTO import_jobs "
                    "(id, request_id, upload_id, state, record, directory_upload_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        uid,
                        request_id,
                        upload_id,
                        "running",
                        json.dumps(record, ensure_ascii=False),
                        directory_upload_id,
                    ),
                )
            self._start(uid, password)
            return self._public(record)

    def _start(self, uid, password):
        self.workers.start(uid, self._run, uid, password)

    def close(self):
        self.workers.close()

    def wait(self, uid):
        self.workers.wait(uid)
        return self.get(uid)

    def _warn(self, uid, warning):
        previous = self._read(uid)["warning"]
        self.repository.update(uid, warning=(previous + "；" + warning) if previous else warning)

    def _cleanup(self, uid):
        current = self._read(uid)
        if current.get("registered") and getattr(self, "after_register", None):
            self.repository.update(uid, phase="signing")
            project_id = (current.get("result") or current.get("reserved_project"))["project_id"]
            result = self.after_register(project_id, uid)
            if result.get("state") != "completed":
                self.repository.update(uid, state="running", phase="signing")
                return False
            if result.get("failed"):
                self._warn(uid, text("saves.signingFailures", count=result["failed"]))
        record = self._read(uid)
        source = Path(record["source"])
        try:
            if record.get("directory_upload_id"):
                try:
                    self.directories.remove(record["directory_upload_id"])
                except KeyError:
                    pass  # Already cleaned before interruption.
            elif record["upload_id"]:
                shutil.rmtree(
                    self.library.data / "uploads/archives" / record["upload_id"],
                    ignore_errors=False,
                )
            elif record["delete_source"] and source.exists():
                if (
                    self._identity(source) != record["identity"]
                    or source.is_dir()
                    or source.is_symlink()
                ):
                    self._warn(uid, text("errors.theSourceChangedTheOriginalArchiveWasKept"))
                else:
                    source.unlink()
        except FileNotFoundError:
            pass  # Already cleaned before interruption.
        except OSError:
            self._warn(uid, text("errors.theGameWasRegisteredButSourceCleanupFailed"))
        self._remove_staging(record)
        self.repository.update(
            uid,
            state="completed",
            phase="completed",
            error=None,
            cleanup_done=True,
        )

    def resume_signing(self, project_id):
        with self.library.lock:
            for record in self.rows():
                game = record.get("result") or record.get("reserved_project") or {}
                if (
                    record.get("registered")
                    and not record.get("cleanup_done")
                    and record.get("phase") == "signing"
                    and game.get("project_id") == project_id
                ):
                    self._cleanup(record["id"])

    def _remove_staging(self, record):
        if record.get("staging"):
            staging = Path(record["staging"])
            if staging.parent == self.library.library_root and staging.name.startswith(".import-"):
                shutil.rmtree(staging, ignore_errors=True)

    def _describe(self, uid, game):
        try:
            result = self.describe(game)
        except Exception:
            self._warn(uid, text("errors.theGameWasRegisteredButExtraInformationIsTemporarily"))
            result = self.fallback(game)
        self.repository.update(uid, project=result)

    def _run(self, uid, password):
        last = 0.0

        def phase(name):
            self.repository.update(uid, phase=name)

        def progress(done, total):
            nonlocal last
            now = time.monotonic()
            if done == 0 or done == total or now - last >= 0.1:
                self.repository.update(
                    uid, phase="extracting", completed_bytes=done, total_bytes=total
                )
                last = now

        try:
            record = self._read(uid)
            if record.get("directory_upload_id"):
                phase("validating")
                game = self.publication.publish(
                    self.directories.source(record["directory_upload_id"]),
                    record["source_name"],
                    engine_id=record.get("engine_id"),
                    job_id=uid,
                )
            else:
                game = self.publication.import_source(
                    Path(record["source"]),
                    password=password,
                    engine_id=record.get("engine_id"),
                    uploaded=bool(record["upload_id"]),
                    progress=progress,
                    phase=phase,
                    job_id=uid,
                )
            password = None
            self._describe(uid, game)
            self._cleanup(uid)
        except Exception as error:
            # A published manifest must survive failures for reconciliation, never re-extract.
            record = self._read(uid)
            self._remove_staging(record)
            if record.get("registered"):
                self._warn(uid, text("errors.theGameWasRegisteredCleanupWasInterruptedAndWill"))
                self.repository.update(uid, state="failed", phase="failed", error=None)
                return
            message = (
                str(error)
                if isinstance(error, ValueError)
                else text("errors.importFailedCheckTheSourceAndServerStorageThen")
            )
            self.repository.update(uid, state="failed", phase="failed", error=message)
        except BaseException:
            # Process interruption leaves durable checkpoints for startup recovery.
            pass
        finally:
            password = None

    def _recover_one(self, record):
        uid = record["id"]
        reserved = record.get("reserved_project")
        if record.get("registered"):
            # DB commit is authoritative even if metadata or the NAS is now unavailable.
            game = Project(**reserved)
            self._describe(uid, game)
            self._cleanup(uid)
            return True
        if reserved:
            game = Project(**reserved)
            root = Path(game.path)

            def unverified():
                # An existing or inaccessible target may already be published. Retain
                # the only durable reference and the upload until it can be verified.
                self.repository.update(
                    uid,
                    state="failed",
                    phase="failed",
                    error=text("errors.theReservedDestinationExistsOrItsStateIsUncertain"),
                )
                return None

            try:
                root.lstat()
            except FileNotFoundError:
                # ENOENT alone can mean a missing/unavailable parent. Only a readable
                # parent directory makes absence of the reserved target conclusive.
                try:
                    parent = root.parent.stat()
                except OSError:
                    return unverified()
                if not stat.S_ISDIR(parent.st_mode):
                    return unverified()
            except OSError:
                return unverified()
            else:
                try:
                    manifest = json.loads((root / ".vn-stream/project.json").read_text())
                    if (
                        root.is_symlink()
                        or (root / ".vn-stream").is_symlink()
                        or (root / ".vn-stream/project.json").is_symlink()
                        or manifest["format"] != "vn-stream/1"
                        or manifest["project_id"] != game.project_id
                        or (game.engine_id == "renpy" and not (root / "game").is_dir())
                    ):
                        raise ValueError(text("errors.invalidManifest"))
                except (OSError, ValueError, KeyError, TypeError):
                    return unverified()
                self.publication.record(game, job_id=uid)
                self._describe(uid, game)
                self._cleanup(uid)
                return True
        self._remove_staging(record)
        if record["state"] == "running":
            self.repository.update(
                uid,
                state="failed",
                phase="failed",
                error=text("errors.theServiceWasInterruptedEnterTheArchivePasswordAgain"),
            )
        return False

    def recover(self):
        for record in self.rows():
            if (
                record["state"] == "running"
                or (record.get("registered") and not record.get("cleanup_done"))
                or (
                    record["state"] == "failed"
                    and record.get("reserved_project")
                    and not record["abandoned"]
                )
            ):
                self._recover_one(record)

    def retry(self, uid, *, password=None, engine_id=...):
        with self.operation():
            record = self._read(uid)
            if record["state"] != "failed" or record["abandoned"]:
                raise ValueError(text("errors.onlyFailedTasksThatHaveNotBeenAbandonedCan"))
            if any(row["state"] == "running" for row in self.rows()):
                raise ValueError(text("errors.anImportTaskIsAlreadyRunning"))
            recovered = self._recover_one(record)
            if recovered:
                return self.get(uid)
            if recovered is None:
                raise ValueError(self._read(uid)["error"])
            if engine_id is not ...:
                if engine_id is not None and engine_id not in ADAPTERS:
                    raise ValueError(text("errors.invalidRequestParameters"))
                self.repository.update(uid, engine_id=engine_id)
            # Validate availability again; uploaded bytes are retained for password retry.
            if record.get("directory_upload_id"):
                self.directories.source(record["directory_upload_id"])
            elif record["upload_id"]:
                self._upload_source(record["upload_id"])
            self.repository.update(
                uid,
                state="running",
                phase="scanning",
                error=None,
                completed_bytes=(
                    self.directories.record(record["directory_upload_id"])["total_bytes"]
                    if record.get("directory_upload_id")
                    else 0
                ),
                total_bytes=(
                    self.directories.record(record["directory_upload_id"])["total_bytes"]
                    if record.get("directory_upload_id")
                    else None
                ),
                reserved_project=None,
            )
            self._start(uid, password)
            return self.get(uid)

    def abandon(self, uid):
        with self.operation():
            record = self._read(uid)
            if record["state"] != "failed":
                raise ValueError(text("errors.onlyFailedTasksCanBeAbandoned"))
            if record["abandoned"]:
                return {"abandoned": True}
            # Published projects are always reconciled, never abandoned as partial data.
            if record.get("reserved_project"):
                recovered = self._recover_one(record)
                if recovered:
                    raise ValueError(text("errors.theGameIsAlreadyRegisteredAndCannotBeAbandoned"))
                if recovered is None:
                    raise ValueError(self._read(uid)["error"])
            if record.get("directory_upload_id"):
                try:
                    self.directories.remove(record["directory_upload_id"])
                except KeyError:
                    pass  # Cleanup committed before an interrupted abandoned-state update.
            elif record["upload_id"]:
                shutil.rmtree(
                    self.library.data / "uploads/archives" / record["upload_id"], ignore_errors=True
                )
            self._remove_staging(record)
            self.repository.update(uid, abandoned=True, release_upload=True)
            return {"abandoned": True}

    def active_claims(self, project_id):
        path = Path(self.library.get(project_id).path)
        for record in self.rows():
            source = Path(record.get("source") or "/nonexistent-source")
            if (
                record["state"] != "completed"
                and not record.get("abandoned")
                and (
                    source == path
                    or source.is_relative_to(path)
                    or (record.get("reserved_project") or {}).get("project_id") == project_id
                    or (record.get("result") or {}).get("project_id") == project_id
                )
            ):
                yield record["id"]
