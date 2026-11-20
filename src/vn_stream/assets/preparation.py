"""Durable, deduplicated original/preview preparation with session-independent workers."""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from vn_stream.assets.decode_cache import DecodeCache
from vn_stream.i18n import text
from vn_stream.projects.locations import check_busy

LOG = logging.getLogger(__name__)


class Preparations:
    def __init__(self, assets):
        self.assets = assets
        self.cache = DecodeCache(assets.library.data / "cache/asset-decode")
        self.temporary = DecodeCache(
            assets.library.data / "tmp/asset-prepared",
            limit=20 * 1024**3,
            max_files=1,
            max_age=60,
        )
        self.stopping = threading.Event()
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asset-prepare")
        self.futures = {}
        with assets.library.db.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS asset_preparations (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,record TEXT NOT NULL)"
            )

        self.cleaner = threading.Thread(target=self._sweep, name="asset-cache-cleanup", daemon=True)
        self.cleaner.start()

    def _sweep(self):
        while not self.stopping.wait(5):
            try:
                self.temporary.prune()
            except OSError:
                LOG.exception("Temporary asset cleanup failed")

    def _record(self, key):
        with self.assets.library.db.transaction() as db:
            row = db.execute("SELECT record FROM asset_preparations WHERE id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _store(self, record):
        with self.assets.library.db.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO asset_preparations VALUES (?,?,?)",
                (record["task_id"], record["project_id"], json.dumps(record)),
            )

    def _context(self, project_id, uid, representation):
        if representation not in ("original", "preview"):
            raise ValueError(text("content.invalid"))
        root, entry = self.assets.entry(project_id, uid)
        source = self.assets.file(project_id, uid)
        source.validate()
        identity = json.dumps(
            [project_id, root.name, uid, representation, source.record, "decode-1"], sort_keys=True
        )
        key = hashlib.sha256(identity.encode()).hexdigest()
        return key, root, entry, source

    def status(self, project_id, uid, representation="original"):
        key, root, entry, source = self._context(project_id, uid, representation)
        tlg = representation == "preview" and entry["mime"] == "image/x-tlg"
        if representation == "preview" and entry.get("preview_supported") is False:
            return dict(
                task_id=key,
                state="failed",
                progress_percent=0,
                messages={
                    "error": {
                        "code": "assets.formatUnsupported",
                        "params": {"format": entry.get("format", "")},
                    }
                },
            )
        direct = not source.record.get("segments") or all(
            s["codec"] == "raw" for s in source.record["segments"]
        )
        if direct and not tlg:
            return dict(
                task_id=key,
                state="completed",
                progress_percent=100,
                url=f"/api/v2/projects/{project_id}/assets/{uid}/content",
            )
        record = self._record(key) or dict(task_id=key, state="missing", progress_percent=0)
        target = (self.temporary.folder if record.get("temporary") else self.cache.folder) / key
        if record["state"] == "completed" and not target.is_file():
            record = {**record, "state": "missing", "progress_percent": 0}
        return {
            **record,
            "url": f"/api/v2/projects/{project_id}/assets/{uid}/prepared-content?representation={representation}"
            if record["state"] == "completed"
            else None,
        }

    def request(self, project_id, uid, representation="original"):
        with self.assets.library.lock, self.lock:
            state = self.status(project_id, uid, representation)
            if (
                state["state"] == "completed"
                or state["state"] == "failed"
                and state.get("messages", {}).get("error", {}).get("code")
                == "assets.formatUnsupported"
            ):
                return state
            key = state["task_id"]
            if key in self.futures and not self.futures[key].done():
                return state
            if self.stopping.is_set() or sum(not f.done() for f in self.futures.values()) >= 32:
                raise ValueError(text("content.busy"))
            check_busy(
                self.assets.library,
                project_id,
                operation_owners=tuple(k for k, f in self.futures.items() if not f.done()),
            )
            _, root, entry, source = self._context(project_id, uid, representation)
            record = dict(
                task_id=key,
                project_id=project_id,
                asset_id=uid,
                generation=root.name,
                representation=representation,
                state="running",
                progress_percent=0,
            )
            self._store(record)
            with self.assets.library.db.transaction() as db:
                db.execute(
                    "INSERT OR REPLACE INTO content_operations VALUES (?,?,?,?,?)",
                    (key, project_id, "asset_prepare", "running", "{}"),
                )
            future = self.executor.submit(self._run, record, source, root, entry)
            self.futures[key] = future
            future.add_done_callback(lambda done, key=key: self._forget(key, done))
            return record

    def _forget(self, key, future):
        with self.lock:
            if self.futures.get(key) is future:
                self.futures.pop(key, None)

    def recover(self):
        # Startup cleanup must finish before any resumed worker creates output.
        self.cache.prune()
        self.temporary.prune()
        for pending in self.cache.folder.glob("*.tmp"):
            if not pending.is_symlink():
                pending.unlink(missing_ok=True)
        with self.assets.library.db.transaction() as db:
            rows = db.execute("SELECT record FROM asset_preparations").fetchall()
            db.execute(
                "UPDATE content_operations SET state='interrupted' WHERE kind='asset_prepare' AND state='running'"
            )
        for row in rows:
            record = json.loads(row[0])
            if record["state"] == "running":
                try:
                    self.request(record["project_id"], record["asset_id"], record["representation"])
                except (OSError, ValueError, KeyError):
                    self._store({**record, "state": "failed", "progress_percent": 0})

    def _valid(self, record, source, root):
        if self.stopping.is_set():
            raise InterruptedError
        current, _ = self.assets.entry(record["project_id"], record["asset_id"])
        if current != root:
            raise ValueError(text("assets.changed"))
        source.validate()

    def _run(self, record, source, root, entry):
        key = record["task_id"]
        pending = self.cache.folder / (key + "." + uuid4().hex + ".tmp")
        raw = None
        try:
            self._valid(record, source, root)
            if shutil.disk_usage(self.cache.folder).free < source.size + 64 * 1024**2:
                raise ValueError(text("assets.noSpace"))
            with source.open() as incoming, pending.open("wb") as output:
                done, last = 0, 0
                while piece := incoming.read(1024**2):
                    self._valid(record, source, root)
                    output.write(piece)
                    done += len(piece)
                    if time.monotonic() - last > 0.2:
                        record["progress_percent"] = min(90, int(done * 90 / max(1, source.size)))
                        self._store(record)
                        last = time.monotonic()
                output.flush()
                os.fsync(output.fileno())
            if record["representation"] == "preview" and entry["mime"] == "image/x-tlg":
                raw = pending
                pending = self.cache.folder / (key + "." + uuid4().hex + ".tmp")
                tool = os.environ.get("VN_STREAM_TLG_TOOL", "/opt/vn-stream/bin/tlg-preview")
                # The child receives only trusted server paths, no project plugins.
                with self.assets._conversion_process(
                    [tool, str(raw), str(pending)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                ) as process:
                    if process.wait(timeout=15) != 0:
                        raise ValueError(text("assets.previewUnavailable"))
            with self.assets.library.lock, self.cache.lock:
                self._valid(record, source, root)
                self.cache.pin(key)
                try:
                    ephemeral = pending.stat().st_size > self.cache.limit
                    destination = self.temporary.folder if ephemeral else self.cache.folder
                    os.replace(pending, destination / key)
                    record.update(state="completed", progress_percent=100, temporary=ephemeral)
                    self._store(record)
                    self.cache.prune()
                finally:
                    self.cache.unpin(key, prune=False)
                    self.cache.prune(protect=(key,))
                    self.temporary.prune(protect=(key,))
        except InterruptedError:
            # Leave running intent durable; startup recovers it after ownership is released.
            pass
        except Exception:
            LOG.exception(
                "Asset preparation failed: project=%s asset=%s",
                record["project_id"],
                record["asset_id"],
            )
            record.update(
                state="failed",
                progress_percent=0,
                messages={"error": {"code": "assets.previewUnavailable", "params": {}}},
            )
            self._store(record)
        finally:
            pending.unlink(missing_ok=True)
            if raw is not None:
                raw.unlink(missing_ok=True)
            with self.assets.library.db.transaction() as db:
                db.execute("UPDATE content_operations SET state='completed' WHERE id=?", (key,))

    def file(self, project_id, uid, representation="original"):
        key, root, entry, source = self._context(project_id, uid, representation)
        state = self.status(project_id, uid, representation)
        if state["state"] != "completed":
            previous = self._record(key)
            if representation == "original" and previous and previous["state"] == "completed":
                # An evicted cache never prevents later ranges over the original archive.
                return source
            raise ValueError(text("assets.preparationRequired"))
        if state["url"].endswith("/content"):
            return source
        cache = self.temporary if state.get("temporary") else self.cache
        try:
            return cache.source(key, source, bool(state.get("temporary")))
        except FileNotFoundError as error:
            if representation == "original":
                return source
            raise ValueError(text("assets.preparationRequired")) from error

    def close(self):
        self.stopping.set()
        self.executor.shutdown(wait=True, cancel_futures=False)
        self.cleaner.join()
        self.temporary.prune(now=time.time() + 61)
