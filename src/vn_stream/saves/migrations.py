"""Durable cross-project save copies and independent whole-batch undo journals."""

import hashlib
import json
import logging
import lzma
import os
import shutil
import sqlite3
import threading
import time
import zipfile
import zlib
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from vn_stream.i18n import descriptor, durable_messages, text
from vn_stream.projects.locations import project, require_capability
from vn_stream.saves.renpy import pickle_codec as save_codec
from vn_stream.saves.renpy.signatures import Signatures
from vn_stream.storage.files import atomic_json, safe, sync_folder

LOG = logging.getLogger(__name__)
ACTIVE = ("queued", "running", "recovering")


def kind(name):
    return (
        "persistent"
        if name == "persistent"
        else "auto"
        if name.startswith("auto-")
        else "quick"
        if name.startswith("quick-")
        else "manual"
    )


class SaveMigrations:
    def __init__(self, saves):
        self.saves, self.library = saves, saves.library
        self.root = self.library.data / "tasks/save-migrations"
        self.root.mkdir(parents=True, exist_ok=True)
        self.stop, self.wake = threading.Event(), threading.Event()
        self.thread = None
        with self.library.db.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS save_migration_previews (id TEXT PRIMARY KEY, record TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS save_migrations (id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT NOT NULL, request_id TEXT NOT NULL, state TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(target_id,request_id))"
            )
        self.recover()
        self.library.maintenance.register("migration", self.active_claims)

    def _save(self, record):
        record["updated_at"] = time.time()
        record.update(durable_messages(record))
        with self.library.db.transaction() as db:
            db.execute(
                "INSERT INTO save_migrations VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,record=excluded.record",
                (
                    record["id"],
                    record["source_id"],
                    record["target_id"],
                    record["request_id"],
                    record["state"],
                    json.dumps(record),
                ),
            )
        return self._public_task(record)

    @staticmethod
    def _public_task(record):
        return {
            key: value
            for key, value in record.items()
            if key
            not in (
                "journal",
                "target_root",
                "target_identity",
                "created_directories",
                "directory_identities",
                "mutation_started",
            )
        }

    def _get(self, uid):
        with self.library.db.transaction() as db:
            row = db.execute("SELECT record FROM save_migrations WHERE id=?", (uid,)).fetchone()
        if not row:
            raise KeyError(uid)
        return json.loads(row[0])

    def get(self, uid):
        return self._public_task(self._get(uid))

    def history(self, target_id):
        project(self.library, target_id)
        with self.library.db.transaction() as db:
            rows = db.execute(
                "SELECT record FROM save_migrations WHERE target_id=? ORDER BY rowid DESC",
                (target_id,),
            ).fetchall()
        return [self._public_task(json.loads(row[0])) for row in rows]

    @staticmethod
    def _identity(path):
        if not path.exists():
            return None
        stat = path.stat()
        return [stat.st_dev, stat.st_ino]

    def _snapshot(self, project_id):
        root = project(self.library, project_id)
        locations = self.saves.locations(project_id)
        candidates = list(dict.fromkeys([safe(root, "game/saves"), *locations]))
        # Bind the report itself, including confirmed locations that do not exist yet.
        report = safe(root, ".vn-stream/save-locations.json")
        report_revision = self.saves.revision([report]) if report.is_file() else None
        files = self.saves.save_copies(project_id)
        return dict(
            root=str(root),
            identity=self._identity(root),
            locations=[dict(path=str(p), identity=self._identity(p)) for p in candidates],
            report=report_revision,
            files={
                name: dict(
                    paths=[str(p) for p in sorted(paths)], revision=self.saves.revision(paths)
                )
                for name, paths in sorted(files.items())
            },
        )

    def _validate(self, preview):
        for project_id, snapshot in preview["snapshots"].items():
            if self._snapshot(project_id) != snapshot:
                raise ValueError(text("migration.changed"))

    def _free(self, project_id, owner=None):
        require_capability(self.library, project_id, "save_migration")
        self.saves.require_writable(project_id, migration_owner=owner)

    def _metadata(self, project_id):
        value = {k: v for k, v in asdict(self.library.get(project_id)).items() if k != "path"}
        cache = self.library.data / "cache/projects" / (project_id + ".json")
        info = {}
        try:
            if cache.stat().st_size <= 1024**2:
                info = json.loads(cache.read_text())
        except (OSError, ValueError):
            pass
        if not isinstance(info, dict):
            info = {}
        value["display_name"] = info.get("display_name")
        value["release_version"] = info.get("release_version")
        value["name"] = value["display_name"] or value["name"]
        return value

    def sources(self, target_id):
        require_capability(self.library, target_id, "save_migration")
        project(self.library, target_id)
        result = []
        for game in self.library.list():
            if game.project_id == target_id:
                continue
            try:
                require_capability(self.library, game.project_id, "save_migration")
                files = self.saves.save_copies(game.project_id)
                counts = {
                    k: sum(kind(name) == k for name in files)
                    for k in ("manual", "auto", "quick", "persistent")
                }
                result.append(
                    dict(
                        self._metadata(game.project_id),
                        counts=dict(counts, total=len(files)),
                        available=self.saves.writable(game.project_id),
                    )
                )
            except (KeyError, ValueError, OSError):
                continue
        return result

    @staticmethod
    def _public_preview(record):
        return {k: v for k, v in record.items() if k not in ("snapshots", "plans")}

    def _store_preview(self, record):
        with self.library.db.transaction() as db:
            db.execute(
                "INSERT INTO save_migration_previews VALUES(?,?)",
                (record["id"], json.dumps(record)),
            )
        return self._public_preview(record)

    def _preview(self, uid):
        with self.library.db.transaction() as db:
            row = db.execute(
                "SELECT record FROM save_migration_previews WHERE id=?", (uid,)
            ).fetchone()
        if not row:
            raise KeyError(uid)
        return json.loads(row[0])

    def _accepted(self, project_id, data, persistent):
        try:
            log, signatures, _ = save_codec.unpack(data, persistent)
            # Reading every member verifies CRC and compression without loading objects.
            if not persistent:
                import io

                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for entry in archive.infolist():
                        with archive.open(entry) as stream:
                            while stream.read(1024**2):
                                pass
        except (
            zlib.error,
            lzma.LZMAError,
            EOFError,
            OSError,
            zipfile.BadZipFile,
            NotImplementedError,
        ) as error:
            # Decompressors use different exception types for damaged in-memory members.
            raise save_codec.Invalid("Invalid compressed save member") from error
        try:
            trusted = Signatures(self.library, project_id).verify(log, signatures, readonly=True)
        except ValueError:
            trusted = False
        if not trusted:
            save_codec.parse(log)
        return trusted

    def preview(self, target_id, source_id, include_persistent=True):
        with self.library.lock:
            if target_id == source_id:
                raise ValueError(text("migration.sameGame"))
            self._free(source_id)
            self._free(target_id)
            Signatures(self.library, target_id).keys(readonly=True)
            snapshots = {uid: self._snapshot(uid) for uid in (source_id, target_id)}
            source, target = self.saves.save_copies(source_id), self.saves.save_copies(target_id)
            entries, plans = [], []
            for name, paths in sorted(source.items()):
                if name == "persistent" and not include_persistent:
                    continue
                primary = max(paths, key=lambda p: p.stat().st_mtime_ns)
                stat = primary.stat()
                reason = None
                try:
                    if stat.st_size > save_codec.MAX_FILE:
                        raise ValueError(text("saves.tooLarge"))
                    self._accepted(source_id, primary.read_bytes(), name == "persistent")
                except (ValueError, OSError, zipfile.BadZipFile, NotImplementedError):
                    reason = descriptor(
                        text(
                            "saves.tooLarge"
                            if stat.st_size > save_codec.MAX_FILE
                            else "saves.unsupported"
                        )
                    )
                entries.append(
                    dict(
                        name=name,
                        kind=kind(name),
                        size_bytes=stat.st_size,
                        updated_at=stat.st_mtime,
                        source_copies=len(paths),
                        target_copies=len(target.get(name, [])),
                        action="overwrite" if name in target else "add",
                        valid=reason is None,
                        reason=reason,
                    )
                )
                if reason is None:
                    plans.append(dict(name=name, source=str(primary), mtime_ns=stat.st_mtime_ns))
            record = dict(
                id=uuid4().hex,
                kind="migration",
                source=self._metadata(source_id),
                target=self._metadata(target_id),
                include_persistent=include_persistent,
                created_at=time.time(),
                entries=entries,
                plans=plans,
                snapshots=snapshots,
                warnings=[descriptor(text("migration.compatibility"))],
            )
            self._finish_preview(record)
            self._validate(record)
            return self._store_preview(record)

    def _destinations(self, target_id, name):
        root = project(self.library, target_id)
        locations = self.saves.locations(target_id)
        # Include the canonical location even before the target has made its first save.
        locations = list(dict.fromkeys([safe(root, "game/saves"), *locations]))
        return [safe(location, name) for location in locations]

    def _finish_preview(self, record):
        entries = record["entries"]
        record["counts"] = dict(
            total=len(entries),
            valid=sum(e["valid"] for e in entries),
            invalid=sum(not e["valid"] for e in entries),
            add=sum(e["action"] == "add" for e in entries),
            overwrite=sum(e["action"] == "overwrite" for e in entries),
        )
        required = 0
        for entry in entries:
            if entry["valid"]:
                paths = self._destinations(record["target"]["project_id"], entry["name"])
                # Stage plus simultaneous destination temporary files and original backups.
                required += (entry["size_bytes"] + 65536) * (len(paths) + 1)
                required += sum(p.stat().st_size for p in paths if p.exists())
        record["required_bytes"] = required

    def _space(self, preview):
        target = project(self.library, preview["target"]["project_id"])
        if any(
            shutil.disk_usage(path).free < preview["required_bytes"]
            for path in (self.root, self.saves.root, target)
        ):
            raise ValueError(text("migration.noSpace"))

    def _duplicate(self, target_id, request_id, preview_id, kind_value, skip_invalid):
        with self.library.db.transaction() as db:
            row = db.execute(
                "SELECT record FROM save_migrations WHERE target_id=? AND request_id=?",
                (target_id, request_id),
            ).fetchone()
        if row:
            record = json.loads(row[0])
            if (
                record["preview_id"] != preview_id
                or record["kind"] != kind_value
                or record["skip_invalid"] != skip_invalid
            ):
                raise ValueError(text("migration.requestConflict"))
            return self._public_task(record)

    def commit(self, target_id, preview_id, request_id, skip_invalid=False):
        return self._enqueue(target_id, preview_id, request_id, skip_invalid, "migration")

    def _enqueue(
        self, target_id, preview_id, request_id, skip_invalid, kind_value, migration_id=None
    ):
        with self.library.lock:
            previous = self._duplicate(target_id, request_id, preview_id, kind_value, skip_invalid)
            if previous:
                return previous
            preview = self._preview(preview_id)
            if (
                preview["target"]["project_id"] != target_id
                or preview["kind"] != kind_value
                or preview.get("migration_id") != migration_id
            ):
                raise ValueError(text("content.invalid"))
            if not preview["counts"]["valid"]:
                raise ValueError(text("migration.noValid"))
            if preview["counts"]["invalid"] and not skip_invalid:
                raise ValueError(text("migration.skipRequired"))
            for project_id in preview["snapshots"]:
                self._free(project_id)
            self._validate(preview)
            Signatures(self.library, target_id).keys(readonly=True)
            self._space(preview)
            root = project(self.library, target_id)
            record = dict(
                id=uuid4().hex,
                kind=kind_value,
                source_id=preview["source"]["project_id"] if kind_value == "migration" else None,
                target_id=target_id,
                source=preview["source"],
                target=preview["target"],
                preview_id=preview_id,
                request_id=request_id,
                skip_invalid=skip_invalid,
                state="queued",
                phase="queued",
                prepared=0,
                backed_up=0,
                results=[
                    dict(
                        name=entry["name"],
                        action=entry["action"],
                        state="pending" if entry["valid"] else "skipped",
                        reason=entry["reason"],
                    )
                    for entry in preview["entries"]
                ],
                created_at=time.time(),
                total=preview["counts"]["valid"],
                completed=0,
                skipped=preview["counts"]["invalid"],
                error=None,
                migration_id=migration_id,
                undo_id=None,
                journal=[],
                target_root=str(root),
                target_identity=self._identity(root),
                mutation_started=False,
                created_directories=[],
                directory_identities={
                    entry["path"]: entry["identity"]
                    for entry in preview["snapshots"][target_id]["locations"]
                },
            )
            if migration_id:
                original = self._get(migration_id)
                if (
                    original["state"] != "completed"
                    or original["kind"] != "migration"
                    or original.get("undo_id")
                ):
                    raise ValueError(text("migration.undoUnavailable"))
            result = self._save(record)
        self.wake.set()
        return result

    @staticmethod
    def _digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def _verify_backups(self, task):
        for record in task["journal"]:
            for entry in record["entries"]:
                if entry["existed"]:
                    path = self.saves.root / task["target_id"] / record["id"] / entry["backup"]
                    if self._digest(path) != entry["sha256"]:
                        raise ValueError(text("migration.backupDamaged"))

    def _backup(self, task, name, paths):
        uid = uuid4().hex
        folder = self.saves.root / task["target_id"] / uid
        folder.mkdir(parents=True)
        entries = []
        for index, path in enumerate(paths):
            existed = path.exists()
            if existed:
                if not path.is_file() or path.is_symlink():
                    raise ValueError(text("content.invalid"))
                shutil.copy2(path, folder / str(index))
                with (folder / str(index)).open("rb") as stream:
                    os.fsync(stream.fileno())
            entries.append(
                dict(
                    path=str(path),
                    backup=str(index),
                    existed=existed,
                    mtime_ns=path.stat().st_mtime_ns if existed else None,
                    sha256=self._digest(folder / str(index)) if existed else None,
                )
            )
        primary = max(
            (entry for entry in entries if entry["existed"]),
            key=lambda e: e["mtime_ns"],
            default={"backup": None},
        )["backup"]
        record = dict(
            id=uid,
            project_id=task["target_id"],
            save_id=self.saves.identifier(name),
            kind="migration" if task["kind"] == "migration" else "migration-undo",
            batch_id=task["id"],
            created_at=time.time(),
            entries=entries,
            primary=primary,
            state="running",
            request_id=task["request_id"],
        )
        atomic_json(folder / "record.json", record)
        sync_folder(folder.parent)
        sync_folder(self.saves.root)
        return record

    def _write(self, path, staged, stamp, operation):
        if staged is None:
            path.unlink(missing_ok=True)
        else:
            pending = path.with_name(path.name + "." + operation + ".new")
            shutil.copyfile(staged, pending)
            os.utime(pending, ns=(stamp, stamp))
            with pending.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(pending, path)
        sync_folder(path.parent)

    def _mark_backups(self, task, state):
        for record in task["journal"]:
            record["state"] = state
            atomic_json(self.saves.root / task["target_id"] / record["id"] / "record.json", record)

    def _rollback(self, task):
        if not task.get("mutation_started"):
            self._mark_backups(task, "failed")
            return
        root = project(self.library, task["target_id"])
        if str(root) != task["target_root"] or self._identity(root) != task["target_identity"]:
            raise ValueError(text("migration.changed"))
        for directory, identity in task.get("directory_identities", {}).items():
            path = safe(root, str(Path(directory).relative_to(root)))
            current = self._identity(path)
            removed_by_recovery = current is None and directory in task.get(
                "created_directories", []
            )
            if identity is not None and current != identity and not removed_by_recovery:
                raise ValueError(text("migration.changed"))
        self._verify_backups(task)
        for record in reversed(task["journal"]):
            recoverable = []
            for entry in record["entries"]:
                path = safe(root, str(Path(entry["path"]).relative_to(root)))
                if not path.parent.exists() and not entry["existed"]:
                    continue
                path.with_name(path.name + "." + task["id"] + ".new").unlink(missing_ok=True)
                recoverable.append(entry)
            self.saves.rollback_files(
                dict(record, entries=recoverable),
                self.saves.root / task["target_id"] / record["id"],
            )
        for directory in reversed(task.get("created_directories", [])):
            path = safe(root, str(Path(directory).relative_to(root)))
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
                sync_folder(path.parent)
        self._mark_backups(task, "failed")

    def process(self, uid):
        with self.library.lock:
            task = self._get(uid)
            if task["state"] != "queued":
                return self._public_task(task)
            try:
                task["phase"] = "validating"
                self._save(task)
                preview = self._preview(task["preview_id"])
                for project_id in preview["snapshots"]:
                    self._free(project_id, uid)
                self._validate(preview)
                self._space(preview)
                task["state"] = "running"
                self._save(task)
                stage = self.root / uid
                stage.mkdir()
                writes = []
                for index, plan in enumerate(preview["plans"]):
                    task["phase"] = "preparing"
                    self._save(task)
                    name = plan["name"]
                    path = Path(plan["source"]) if plan.get("source") else None
                    staged = None
                    if path:
                        data = path.read_bytes()
                        if (
                            task["kind"] == "undo"
                            and self.saves.revision([path]) != plan["revision"]
                        ):
                            raise ValueError(text("migration.changed"))
                        try:
                            trusted = self._accepted(
                                task["source_id"] or task["target_id"], data, name == "persistent"
                            )
                            data = Signatures(self.library, task["target_id"]).resign(
                                data, name == "persistent", trusted_source=trusted, force_local=True
                            )
                        except save_codec.Invalid as error:
                            raise ValueError(text("saves.unsupported")) from error
                        staged = stage / str(index)
                        with staged.open("xb") as stream:
                            stream.write(data)
                            stream.flush()
                            os.fsync(stream.fileno())
                    task["prepared"] += 1
                    task["phase"] = "backing_up"
                    self._save(task)
                    paths = self._destinations(task["target_id"], name)
                    backup = self._backup(task, name, paths)
                    task["journal"].append(backup)
                    task["backed_up"] += 1
                    writes.append((paths, staged, plan["mtime_ns"]))
                self._validate(preview)
                sync_folder(stage)
                # The complete rollback journal is durable before creating any save directory or replacing any slot.
                for paths, _, _ in writes:
                    for path in paths:
                        missing = []
                        directory = path.parent
                        while not directory.exists():
                            missing.append(str(directory))
                            directory = directory.parent
                        for directory in reversed(missing):
                            if directory not in task["created_directories"]:
                                task["created_directories"].append(directory)
                task["mutation_started"] = True
                self._save(task)
                for directory in task["created_directories"]:
                    Path(directory).mkdir(exist_ok=True)
                    sync_folder(Path(directory).parent)
                for directory in task["directory_identities"]:
                    task["directory_identities"][directory] = self._identity(Path(directory))
                task["phase"] = "writing"
                self._save(task)
                for index, (paths, staged, stamp) in enumerate(writes):
                    for path in paths:
                        self._write(path, staged, stamp, uid)
                    task["completed"] += 1
                    for result in task["results"]:
                        if result["name"] == preview["plans"][index]["name"]:
                            result["state"] = "completed"
                    self._save(task)
                self._mark_backups(task, "completed")
                task.update(state="completed", phase="completed", error=None)
                # Parent undo linkage and the terminal task state share one database transaction.
                if task["migration_id"]:
                    original = self._get(task["migration_id"])
                    original["undo_id"] = uid
                    with self.library.db.transaction() as db:
                        db.execute(
                            "UPDATE save_migrations SET record=? WHERE id=?",
                            (json.dumps(original), original["id"]),
                        )
                        task["updated_at"] = time.time()
                        db.execute(
                            "UPDATE save_migrations SET state=?,record=? WHERE id=?",
                            ("completed", json.dumps(task), uid),
                        )
                else:
                    self._save(task)
            except Exception as error:
                task.update(
                    state="recovering", phase="rolling_back", error=text("migration.failed")
                )
                self._save(task)
                try:
                    self._rollback(task)
                    task.update(state="failed", phase="failed", completed=0)
                except Exception:
                    task["error"] = text("migration.recoveryFailed")
                    LOG.exception("Migration rollback failed: %s", uid)
                else:
                    message = descriptor(error)
                    if message["code"] != "common.requestFailed":
                        task["error"] = text(message["code"], **message["params"])
                self._failure_results(task)
                self._save(task)
            return self._public_task(task)

    def undo_preview(self, uid):
        with self.library.lock:
            original = self._get(uid)
            if (
                original["state"] != "completed"
                or original["kind"] != "migration"
                or original.get("undo_id")
            ):
                raise ValueError(text("migration.undoUnavailable"))
            target_id = original["target_id"]
            self._free(target_id)
            self._verify_backups(original)
            snapshot = self._snapshot(target_id)
            entries, plans = [], []
            for record in original["journal"]:
                name = Path(record["entries"][0]["path"]).name
                before = next(
                    (
                        entry
                        for entry in record["entries"]
                        if entry["backup"] == record["primary"] and entry["existed"]
                    ),
                    None,
                )
                source = (
                    self.saves.root / target_id / record["id"] / before["backup"]
                    if before
                    else None
                )
                if source:
                    try:
                        self._accepted(target_id, source.read_bytes(), name == "persistent")
                    except save_codec.Invalid as error:
                        raise ValueError(text("saves.unsupported")) from error
                entries.append(
                    dict(
                        name=name,
                        kind=kind(name),
                        size_bytes=source.stat().st_size if source else 0,
                        updated_at=before["mtime_ns"] / 1e9 if before else None,
                        source_copies=1 if before else 0,
                        target_copies=len(self.saves.save_copies(target_id).get(name, [])),
                        action="overwrite" if before else "remove",
                        valid=True,
                        reason=None,
                    )
                )
                plans.append(
                    dict(
                        name=name,
                        source=str(source) if source else None,
                        revision=self.saves.revision([source]) if source else None,
                        mtime_ns=before["mtime_ns"] if before else 0,
                    )
                )
            record = dict(
                id=uuid4().hex,
                kind="undo",
                migration_id=uid,
                source=original["source"],
                target=self._metadata(target_id),
                include_persistent=True,
                created_at=time.time(),
                entries=entries,
                plans=plans,
                snapshots={target_id: snapshot},
                warnings=[descriptor(text("migration.undoWarning"))],
            )
            self._finish_preview(record)
            self._validate(record)
            return self._store_preview(record)

    def undo(self, uid, preview_id, request_id):
        original = self._get(uid)
        return self._enqueue(original["target_id"], preview_id, request_id, False, "undo", uid)

    @staticmethod
    def _failure_results(task):
        for result in task.get("results", []):
            if result["state"] != "skipped":
                result["state"] = (
                    "recovering"
                    if task["state"] == "recovering"
                    else "rolled_back"
                    if task.get("mutation_started")
                    else "not_written"
                )
                result["reason"] = descriptor(task["error"])

    def recover(self):
        with self.library.lock, self.library.db.transaction() as db:
            rows = db.execute(
                "SELECT record FROM save_migrations WHERE state IN ('queued','running','recovering')"
            ).fetchall()
        for row in rows:
            task = json.loads(row[0])
            with self.library.lock:
                try:
                    self._rollback(task)
                    task.update(
                        state="failed",
                        phase="failed",
                        completed=0,
                        error=text("migration.interrupted"),
                    )
                except Exception:
                    task.update(
                        state="recovering",
                        phase="rolling_back",
                        error=text("migration.recoveryFailed"),
                    )
                    LOG.exception("Migration startup recovery failed: %s", task["id"])
                self._failure_results(task)
                self._save(task)

    def start(self):
        self.thread = threading.Thread(target=self._run, name="tasks/save-migrations", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            try:
                with self.library.db.transaction() as db:
                    rows = db.execute(
                        "SELECT id FROM save_migrations WHERE state='queued' ORDER BY rowid"
                    ).fetchall()
            except sqlite3.OperationalError as error:
                if (getattr(error, "sqlite_errorcode", 0) & 255) not in (5, 6) and str(
                    error
                ) not in ("database is locked", "database table is locked"):
                    raise
                LOG.warning("Task queue database busy; retrying read")
                self.stop.wait(1)
                continue
            for row in rows:
                if self.stop.is_set():
                    return
                self.process(row[0])
            self.wake.wait(1)
            self.wake.clear()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join()

    def active_claims(self, project_id):
        with self.library.db.transaction() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT id FROM save_migrations WHERE (source_id=? OR target_id=?) AND state IN ('queued','running','recovering')",
                    (project_id, project_id),
                )
            ]
