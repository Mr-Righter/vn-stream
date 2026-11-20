"""Durable, idle-only signing of accepted project saves without loading their state."""

import json
import logging
import sqlite3
import threading
import time
from uuid import uuid4

from vn_stream.i18n import descriptor, text
from vn_stream.projects.locations import project
from vn_stream.saves.renpy import pickle_codec as save_codec
from vn_stream.saves.renpy.signatures import Signatures

LOG = logging.getLogger(__name__)


class SigningJobs:
    def __init__(self, saves):
        self.saves, self.library = saves, saves.library
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        with self.library.db.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS save_signing_jobs (project_id TEXT PRIMARY KEY, id TEXT UNIQUE, state TEXT, record TEXT)"
            )
            rows = db.execute(
                "SELECT record FROM save_signing_jobs WHERE state='running'"
            ).fetchall()
            for row in rows:
                record = json.loads(row[0])
                record.update(state="waiting")
                db.execute(
                    "UPDATE save_signing_jobs SET state=?,record=? WHERE project_id=?",
                    (record["state"], json.dumps(record), record["project_id"]),
                )
        self.library.maintenance.register("signing", self.active_claims)

    def status(self, project_id):
        project(self.library, project_id)
        with self.library.db.transaction() as db:
            row = db.execute(
                "SELECT record FROM save_signing_jobs WHERE project_id=?", (project_id,)
            ).fetchone()
        return (
            json.loads(row[0])
            if row
            else dict(
                project_id=project_id,
                state="idle",
                signed=0,
                skipped=0,
                failed=0,
                total=0,
                errors=[],
            )
        )

    def _save(self, record):
        record["updated_at"] = time.time()
        with self.library.db.transaction() as db:
            db.execute(
                "INSERT INTO save_signing_jobs VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET id=excluded.id,state=excluded.state,record=excluded.record",
                (record["project_id"], record["id"], record["state"], json.dumps(record)),
            )
        return record.copy()

    def ensure(self, project_id):
        if self.library.get(project_id).engine_id != "renpy":
            return dict(
                project_id=project_id,
                state="completed",
                signed=0,
                skipped=0,
                failed=0,
                total=0,
                errors=[],
            )
        with self.library.lock:
            value = self.status(project_id)
            if value["state"] != "idle":
                return value
            value.update(id=uuid4().hex, state="queued", created_at=time.time())
            self._save(value)
        self.wake.set()
        return value

    def retry(self, project_id):
        with self.library.lock:
            value = self.status(project_id)
            if value["state"] in ("queued", "waiting", "running"):
                return value
            value = dict(
                id=uuid4().hex,
                project_id=project_id,
                state="queued",
                signed=0,
                skipped=0,
                failed=0,
                total=0,
                errors=[],
                created_at=time.time(),
            )
            self._save(value)
        self.wake.set()
        return value

    def process(self, project_id, import_owner=None):
        with self.library.lock:
            value = self.ensure(project_id)
            if value["state"] in ("completed", "running"):
                return value
            # A background iteration can hold an older owner snapshot than the importer.
            import_owner = import_owner or value.get("import_owner")
            value["import_owner"] = import_owner
            try:
                self.saves.require_writable(project_id, import_owner=import_owner)
            except ValueError:
                value["state"] = "waiting"
                return self._save(value)
            files = self.saves.save_copies(project_id)
            entries = [(name, path) for name, paths in files.items() for path in paths]
            value.update(
                state="running",
                import_owner=import_owner,
                total=len(entries),
                signed=0,
                skipped=0,
                failed=0,
                errors=[],
            )
            self._save(value)
        try:
            for name, path in entries:
                if self.stop.is_set():
                    value["state"] = "waiting"
                    break
                with self.library.lock:
                    self.saves.require_writable(
                        project_id, signing_owner=value["id"], import_owner=import_owner
                    )
                    try:
                        if path.stat().st_size > save_codec.MAX_FILE:
                            raise ValueError(text("saves.tooLarge"))
                        revision = self.saves.revision([path])
                        data = path.read_bytes()
                        try:
                            modified = Signatures(self.library, project_id).resign(
                                data, name == "persistent"
                            )
                        except save_codec.Invalid as exc:
                            raise ValueError(text("saves.unsupported")) from exc
                        if modified == data:
                            value["skipped"] += 1
                        else:
                            self.saves.commit_files(
                                project_id,
                                self.saves.identifier(name),
                                [path],
                                modified,
                                "sign",
                                preserve_mtime=True,
                                expected_revision=revision,
                            )
                            value["signed"] += 1
                    except (ValueError, OSError) as error:
                        value["failed"] += 1
                        message = descriptor(error.args[0]) if error.args else None
                        code = (
                            message["code"]
                            if message and message["code"] != "common.requestFailed"
                            else "content.operationFailed"
                        )
                        value["errors"].append(dict(name=name, code=code))
                        LOG.warning(
                            "Save signing skipped: game=%s file=%s reason=%s",
                            project_id,
                            name,
                            type(error).__name__,
                        )
                    self._save(value)
            else:
                value["state"] = "completed"
        except (ValueError, OSError):
            value["state"] = "waiting"
        finally:
            self._save(value)
        return value.copy()

    def imported(self, project_id, import_owner):
        if self.library.get(project_id).engine_id != "renpy":
            return self.ensure(project_id)
        with self.library.lock:
            value = self.ensure(project_id)
            if value["state"] == "completed" and value.get("import_owner") != import_owner:
                self.retry(project_id)
        return self.process(project_id, import_owner=import_owner)

    def start(self):
        for game in self.library.list():
            try:
                self.ensure(game.project_id)
            except (KeyError, ValueError, OSError):
                # Deletion recovery may remove an indexed game during application startup.
                continue
        self.thread = threading.Thread(target=self._run, daemon=True, name="save-signing")
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            try:
                with self.library.db.transaction() as db:
                    rows = db.execute(
                        "SELECT project_id FROM save_signing_jobs WHERE state IN ('queued','waiting')"
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
                try:
                    owner = self.status(row[0]).get("import_owner")
                    result = self.process(row[0], import_owner=owner)
                    callback = getattr(self, "on_completed", None)
                    if result["state"] == "completed" and callback:
                        callback(row[0])
                except KeyError:
                    with self.library.db.transaction() as db:
                        db.execute("DELETE FROM save_signing_jobs WHERE project_id=?", (row[0],))
                except (ValueError, OSError):
                    LOG.warning("Signing queue unavailable: game=%s", row[0])
            self.wake.wait(1)
            self.wake.clear()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(20)

    def active_claims(self, project_id):
        with self.library.db.transaction() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT id FROM save_signing_jobs WHERE project_id=? AND state='running'",
                    (project_id,),
                )
            ]
