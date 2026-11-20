"""Durable game-owned resource snapshots and bounded local media previews."""

import hashlib
import json
import logging
import mimetypes
import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from vn_stream.assets.archive_index import image_type
from vn_stream.assets.sources import Source, decoder_input
from vn_stream.assets.thumbnails import VideoThumbnails
from vn_stream.i18n import MessageText, descriptor, text
from vn_stream.projects.locations import check_busy, initialize, project
from vn_stream.storage.files import atomic_json, safe

FORMATS = "mov,matroska,webm,ogg,mp3,wav,flac,aac,avi,mpeg,mpegts"


@lru_cache(maxsize=8)
def read_index(path, modified, size):
    if size > 64 * 1024**2:
        raise ValueError(text("assets.limit"))
    return json.loads(Path(path).read_text())


KINDS = ("image", "audio", "video", "script", "other")
SKIP = {"saves", "cache", ".vn-stream", "__pycache__"}
LOG = logging.getLogger(__name__)


def identify(path, name, temporary_root=None):
    with path.open() if isinstance(path, Source) else path.open("rb") as f:
        head = f.read(512)
    image = image_type(head)
    ext = Path(name).suffix.lower()
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if ext in (
        ".webm",
        ".mp4",
        ".m4v",
        ".ogv",
        ".avi",
        ".mov",
        ".mkv",
        ".mpeg",
        ".mpg",
        ".ogg",
        ".opus",
        ".mp3",
        ".wav",
        ".flac",
        ".m4a",
        ".aac",
        ".weba",
    ) and shutil.which("ffprobe"):
        try:
            with decoder_input(path, temporary_root) as source:
                probe = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-protocol_whitelist",
                        "file,subfile",
                        "-format_whitelist",
                        FORMATS,
                        "-show_entries",
                        "stream=codec_type",
                        "-of",
                        "json",
                        source.url,
                    ],
                    capture_output=True,
                    pass_fds=source.pass_fds,
                    timeout=5,
                    check=True,
                )
            streams = json.loads(probe.stdout).get("streams", [])
            if any(item.get("codec_type") == "video" for item in streams):
                return "video", mime
            if any(item.get("codec_type") == "audio" for item in streams):
                return "audio", mime
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    if image:
        mime = image[0] if isinstance(image, tuple) else image
        # Existing image_type returns a MIME string.
        return "image", mime
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".bmp", ".ico"):
        return "image", mime
    if ext in (".webm", ".mp4", ".m4v", ".ogv", ".avi", ".mov", ".mkv", ".mpeg", ".mpg"):
        return "video", mime
    if ext in (
        ".ogg",
        ".opus",
        ".mp3",
        ".wav",
        ".flac",
        ".m4a",
        ".aac",
        ".weba",
    ) or head.startswith((b"OggS", b"fLaC", b"ID3")):
        return "audio", mime
    if ext in (".rpy", ".rpyc", ".py", ".txt", ".json", ".md", ".csv"):
        return "script", "text/plain" if ext != ".rpyc" else "application/octet-stream"
    return "other", mime


class Paused(Exception):
    pass


class Assets:
    def __init__(self, library, *, recover=True):
        self.library = library
        initialize(library)
        self.worker = threading.Lock()
        self.preview_worker = threading.Lock()
        self.threads = {}
        self.stopping = threading.Event()
        self.lifecycle = threading.RLock()
        self.processes = set()
        self.thumbnails = VideoThumbnails(self, FORMATS)
        from vn_stream.assets.preparation import Preparations

        self.preparations = Preparations(self)
        with library.db.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS asset_jobs "
                "(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, project_id TEXT, record TEXT)"
            )
        if recover:
            self.recover()

    def recover(self):
        self.preparations.recover()
        with self.library.db.transaction() as db:
            rows = db.execute("SELECT record FROM asset_jobs").fetchall()
        with self.library.db.transaction() as db:
            db.execute(
                "UPDATE content_operations SET state='interrupted' WHERE kind='preview' AND state='running'"
            )
        for row in rows:
            record = json.loads(row[0])
            if record["state"] == "running":
                self._start(record["id"])

    def job(self, uid):
        with self.library.db.transaction() as db:
            row = db.execute("SELECT record FROM asset_jobs WHERE id=?", (uid,)).fetchone()
        if not row:
            raise KeyError(uid)
        return json.loads(row[0])

    def _update(self, uid, **changes):
        with self.library.lock:
            record = self.job(uid)
            record.update(changes)
            with self.library.db.transaction() as db:
                db.execute("UPDATE asset_jobs SET record=? WHERE id=?", (json.dumps(record), uid))
                db.execute(
                    "UPDATE content_operations SET state=? WHERE id=?", (record["state"], uid)
                )
        return record

    def extract(self, project_id, request_id, mode="index"):
        with self.library.lock:
            with self.library.db.transaction() as db:
                row = db.execute(
                    "SELECT id,project_id FROM asset_jobs WHERE request_id=?", (request_id,)
                ).fetchone()
                if row:
                    if row[1] != project_id:
                        raise ValueError(text("content.invalid"))
                    return self.job(row[0])
                rows = db.execute(
                    "SELECT id FROM content_operations WHERE kind=? AND state=?",
                    ("assets", "running"),
                ).fetchall()
                if rows:
                    raise ValueError(text("content.busy"))
            project(self.library, project_id)
            check_busy(self.library, project_id)
            uid = uuid4().hex
            record = dict(
                id=uid,
                project_id=project_id,
                state="running",
                phase="scanning",
                completed_bytes=0,
                total_bytes=0,
                completed_files=0,
                total_files=0,
                cancelled=False,
                warnings=0,
                messages={},
                mode=mode,
            )
            with self.library.db.transaction() as db:
                db.execute(
                    "INSERT INTO asset_jobs VALUES (?,?,?,?)",
                    (uid, request_id, project_id, json.dumps(record)),
                )
                db.execute(
                    "INSERT INTO content_operations VALUES (?,?,?,?,?)",
                    (uid, project_id, "assets", "running", "{}"),
                )
            self._start(uid)
            return record

    def migrate(self, project_id, request_id):
        return self.extract(project_id, request_id, mode="migrate")

    def _start(self, uid):
        with self.lifecycle:
            if self.stopping.is_set():
                raise RuntimeError("Asset workers are closed")
            thread = self.threads.get(uid)
            if thread and thread.is_alive():
                return
            thread = threading.Thread(target=self._run, args=(uid,), daemon=True)
            self.threads[uid] = thread
            thread.start()

    def cancel(self, uid):
        return self._update(uid, cancelled=True)

    def retry(self, uid):
        record = self.job(uid)
        if record["state"] == "running":
            return record
        return self.extract(record["project_id"], uuid4().hex, record.get("mode", "index"))

    def _root(self, project_id):
        p = project(self.library, project_id)
        return safe(p, ".vn-stream/assets")

    def _categories(self, project_id):
        from vn_stream.engines.registry import ADAPTERS

        provider = getattr(
            ADAPTERS.get(self.library.get(project_id).engine_id), "asset_provider", None
        )
        return list(provider.categories) if provider else []

    def summary(self, project_id):
        root = self._root(project_id)
        current = root / "current.json"
        result = dict(
            ready=False,
            counts={k: 0 for k in KINDS},
            size_bytes=0,
            updated_at=None,
            categories=self._categories(project_id),
        )
        if current.is_file():
            info = json.loads(safe(root, "current.json").read_text())
            result.update(info, ready=True)
        with self.library.db.transaction() as db:
            row = db.execute(
                "SELECT record FROM asset_jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        result["job"] = json.loads(row[0]) if row else None
        return result

    def _snapshot(self, project_id):
        info = self.summary(project_id)
        if not info["ready"]:
            raise ValueError(text("assets.notReady"))
        root = safe(self._root(project_id), info["generation"])
        index = safe(root, "index.json")
        stat = index.stat()
        return root, read_index(str(index), stat.st_mtime_ns, stat.st_size)

    def list(self, project_id, *, category="", folder="", search="", page=1):
        _, entries = self._snapshot(project_id)
        selected = [
            e
            for e in entries
            if (not category or e["kind"] == category)
            and (not search or search.casefold() in e["path"].casefold())
        ]
        folders = {}
        files = []
        prefix = folder.rstrip("/") + "/" if folder else ""
        for entry in selected:
            if not entry["path"].startswith(prefix):
                continue
            relative = entry["path"][len(prefix) :]
            if "/" in relative and not search:
                name = relative.split("/")[0]
                folders[name] = folders.get(name, 0) + 1
            else:
                files.append(self._public(entry))
        records = [
            dict(id="folder:" + prefix + n, name=n, path=prefix + n, directory=True, count=c)
            for n, c in sorted(folders.items())
        ] + sorted(files, key=lambda e: e["path"].casefold())
        return dict(entries=records[(page - 1) * 100 : page * 100], total=len(records), page=page)

    def entry(self, project_id, uid):
        root, entries = self._snapshot(project_id)
        entry = next((e for e in entries if e["id"] == uid), None)
        if not entry:
            raise KeyError(uid)
        return root, entry

    @staticmethod
    def _public(entry):
        return {
            **{key: value for key, value in entry.items() if key not in {"source", "retire"}},
            "requires_preparation": "archive" in entry,
        }

    def detail(self, project_id, uid, *, category="", folder="", search=""):
        _, entries = self._snapshot(project_id)
        entry = next((e for e in entries if e["id"] == uid), None)
        if entry is None:
            raise KeyError(uid)
        kind = category or entry["kind"]
        prefix = folder.rstrip("/") + "/" if folder else ""
        candidates = sorted(
            (
                e
                for e in entries
                if e["kind"] == kind
                and e["path"].startswith(prefix)
                and (
                    search.casefold() in e["path"].casefold()
                    if search
                    else "/" not in e["path"][len(prefix) :]
                )
            ),
            key=lambda e: e["path"].casefold(),
        )
        index = next((i for i, e in enumerate(candidates) if e["id"] == uid), None)
        if index is None:
            raise KeyError(uid)
        return dict(
            self._public(entry),
            position=index + 1,
            total=len(candidates),
            previous_id=candidates[index - 1]["id"] if index else None,
            next_id=candidates[index + 1]["id"] if index + 1 < len(candidates) else None,
        )

    def file(self, project_id, uid):
        root, entry = self.entry(project_id, uid)
        if entry.get("source"):
            return Source(project(self.library, project_id), entry["source"]).validate()
        path = safe(root, "files/" + entry["path"])
        if not path.is_file():
            raise KeyError(uid)
        return Source(root, Source.describe(path, "files/" + entry["path"])).validate()

    def _sources(self, game, check=lambda: None):
        from vn_stream.engines.registry import ADAPTERS, inspect_project

        inspection = inspect_project(game)
        adapter = ADAPTERS.get(inspection.engine_id)
        provider = getattr(adapter, "asset_provider", None)
        if provider is None:
            raise ValueError(text("engine.unsupported"))
        sources, warnings = provider.enumerate(game, inspection, check)
        return sources, warnings, sum(s["size"] for s in sources.values())

    def _check_task(self, uid):
        if self.stopping.is_set():
            raise Paused
        if self.job(uid)["cancelled"]:
            raise InterruptedError

    def _run(self, uid):
        with self.worker:
            record = self.job(uid)
            temporary = None
            try:
                root = self._root(record["project_id"])
                temporary = safe(root, ".staging-" + uid)
                if temporary.exists():
                    shutil.rmtree(temporary)
                temporary.mkdir(parents=True)
                game = project(self.library, record["project_id"])
                if record.get("mode") == "migrate":
                    self._migrate(uid, record["project_id"], game)
                    return
                sources, warnings, total = self._sources(game, lambda: self._check_task(uid))
                if (root / "current.json").is_file():
                    old_root, old_entries = self._snapshot(record["project_id"])
                    for old in old_entries:
                        if not old.get("source") or old["source"].get("retained"):
                            old_source = self.file(record["project_id"], old["id"])
                            source_record = dict(old_source.record)
                            source_record["path"] = str(
                                (old_source.root / source_record["path"]).relative_to(game)
                            )
                            source_record["retained"] = True
                            sources[old["path"]] = source_record
                total = sum(source["size"] for source in sources.values())
                if total > 20 * 1024**3 or len(sources) > 100_000:
                    raise ValueError(text("assets.limit"))
                if shutil.disk_usage(root).free < 64 * 1024**2:
                    raise ValueError(text("assets.noSpace"))
                self._update(
                    uid,
                    phase="indexing",
                    total_bytes=total,
                    total_files=len(sources),
                    warnings=len(warnings),
                    warning_details=warnings,
                )
                entries, done, last = [], 0, 0
                for name, description in sources.items():
                    self._check_task(uid)
                    source = Source(game, description).validate()
                    media = description.get("media")
                    kind, mime = (
                        (media["kind"], media["mime"])
                        if media
                        else identify(source, name, self.library.data / "tmp")
                    )
                    source.validate()
                    entries.append(
                        dict(
                            id=hashlib.sha256(name.encode()).hexdigest()[:32],
                            name=Path(name).name,
                            path=name,
                            kind=kind,
                            mime=mime,
                            size_bytes=source.size,
                            directory=False,
                            source=description,
                            **(
                                {
                                    **{k: v for k, v in media.items() if k not in ("kind", "mime")},
                                    "archive": description.get("archive"),
                                    "internal_path": description.get("internal_path"),
                                }
                                if media
                                else {}
                            ),
                        )
                    )
                    done += source.size
                    if time.monotonic() - last > 0.2:
                        self._update(uid, completed_bytes=done, completed_files=len(entries))
                        last = time.monotonic()
                self._update(
                    uid, phase="indexing", completed_bytes=done, completed_files=len(entries)
                )
                atomic_json(temporary / "index.json", entries)
                if (temporary / "index.json").stat().st_size > 64 * 1024**2:
                    raise ValueError(text("assets.limit"))
                generation = "snapshot-" + uid
                info = dict(
                    generation=generation,
                    counts={k: sum(e["kind"] == k for e in entries) for k in KINDS},
                    size_bytes=total,
                    updated_at=time.time(),
                    storage="reference-v1",
                    categories=self._categories(record["project_id"]),
                    warning_details=warnings,
                    retained_files=sum(bool(e["source"].get("retained")) for e in entries),
                    retained_bytes=sum(
                        e["size_bytes"] for e in entries if e["source"].get("retained")
                    ),
                )
                with self.library.lock:
                    self._check_task(uid)
                    for entry in entries:
                        Source(game, entry["source"]).validate()
                    os.replace(temporary, root / generation)
                    atomic_json(root / "current.json", info)
                    self._update(uid, state="completed", phase="completed")
                # Readers retain the published generation until their request ends.
                self._cleanup(root, generation)
                timer = threading.Timer(86405, self._cleanup, args=(root, generation))
                timer.daemon = True
                timer.start()
            except Paused:
                pass
            except InterruptedError:
                self._update(uid, state="cancelled", phase="cancelled")
            except Exception as error:
                LOG.exception("Asset extraction failed: task=%s", uid)
                message = (
                    descriptor(error)
                    if error.args and isinstance(error.args[0], MessageText)
                    else dict(code="assets.failed", params={})
                )
                self._update(uid, state="failed", messages={"error": message})
            finally:
                if temporary is not None and temporary.exists():
                    shutil.rmtree(temporary)

    def _migrate(self, uid, project_id, game):
        from vn_stream.assets.reindex import migrate

        migrate(self, uid, project_id, game)

    @staticmethod
    def _snapshot_from_root(root, generation):
        snapshot = safe(root, generation)
        index = safe(snapshot, "index.json")
        value = index.stat()
        return snapshot, read_index(str(index), value.st_mtime_ns, value.st_size)

    def _cleanup(self, root, generation):
        try:
            if not root.exists() or any(path.is_symlink() for path in (root, *root.parents)):
                return
            current = json.loads(safe(root, "current.json").read_text())["generation"]
            protected = set()
            _, records = self._snapshot_from_root(root, current)
            for entry in records:
                locator = entry.get("source", {}).get("path", "")
                prefix = ".vn-stream/assets/"
                if locator.startswith(prefix):
                    protected.add(locator[len(prefix) :].split("/")[0])
            for old in root.glob("snapshot-*"):
                if (
                    old.name != current
                    and old.name not in protected
                    and not old.is_symlink()
                    and time.time() - old.stat().st_mtime > 86400
                ):
                    try:
                        shutil.rmtree(old)
                    except OSError:
                        LOG.warning("Retired asset snapshot cleanup failed: snapshot=%s", old.name)
        except (OSError, ValueError):
            LOG.warning("Asset snapshot cleanup deferred")

    def prepare(self, project_id, uid, representation="original"):
        return self.preparations.request(project_id, uid, representation)

    def prepare_status(self, project_id, uid, representation="original"):
        return self.preparations.status(project_id, uid, representation)

    def prepared_file(self, project_id, uid, representation="original"):
        return self.preparations.file(project_id, uid, representation)

    def thumbnail(self, project_id, uid):
        from PIL import Image, ImageOps

        root, entry = self.entry(project_id, uid)
        if entry["kind"] == "video":
            return self.thumbnails.request(project_id, uid)
        if entry.get("mime") == "image/x-tlg" or entry.get("source", {}).get("segments"):
            state = self.prepare(project_id, uid, "preview")
            if state["state"] != "completed":
                return state
            source = self.prepared_file(project_id, uid, "preview")
        else:
            source = self.file(project_id, uid)
        cache = safe(root, "thumbnails/" + uid + ".jpg")
        if cache.exists():
            return cache
        cache.parent.mkdir(exist_ok=True)
        temp = None
        try:
            if source.size > 64 * 1024**2:
                raise ValueError(text("assets.limit"))
            with source.open() as stream, Image.open(stream) as image:
                if image.width * image.height > 40_000_000:
                    raise ValueError("Image dimensions exceed preview limit")
                image.thumbnail((320, 320))
                image = ImageOps.exif_transpose(image).convert("RGB")
                temp = cache.with_name(cache.name + "." + uuid4().hex + ".tmp")
                image.save(temp, format="JPEG", quality=80)
                with self.library.lock:
                    source.validate()
                    if self.entry(project_id, uid)[0] != root:
                        raise ValueError(text("assets.changed"))
                    os.replace(temp, cache)
        except Exception as exc:
            raise ValueError(text("assets.previewUnavailable")) from exc
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        return cache

    def preview(self, project_id, uid):
        with self.library.lock:
            root, entry = self.entry(project_id, uid)
            self.file(project_id, uid).validate()
            if entry["kind"] not in ("audio", "video"):
                raise ValueError(text("content.invalid"))
            folder = safe(root, "previews/" + uid)
            project(self.library, project_id)
            safe(root, "previews").mkdir(exist_ok=True)
            folder.mkdir(exist_ok=True)
            target = folder / ("preview.mp4" if entry["kind"] == "video" else "preview.m4a")
            status = folder / "status.json"
            if target.is_file():
                return dict(state="completed", progress=100)
            if status.is_file():
                value = json.loads(status.read_text())
                if value["state"] == "running" and (project_id, uid) in self.threads:
                    return value
            with self.library.lock:
                check_busy(self.library, project_id)
                operation = uuid4().hex
                with self.library.db.transaction() as db:
                    db.execute(
                        "INSERT INTO content_operations VALUES (?,?,?,?,?)",
                        (operation, project_id, "preview", "running", "{}"),
                    )
                atomic_json(status, dict(state="running", progress=0, operation=operation))
            with self.lifecycle:
                if self.stopping.is_set():
                    raise RuntimeError("Asset workers are closed")
                thread = threading.Thread(
                    target=self._convert,
                    args=(project_id, uid, folder, target, operation),
                    daemon=True,
                )
                self.threads[(project_id, uid)] = thread
                thread.start()
            return dict(state="running", progress=0)

    def _convert(self, project_id, uid, folder, target, operation):
        with self.preview_worker:
            temp = target.with_name("pending" + target.suffix)
            try:
                if self.stopping.is_set():
                    raise InterruptedError
                source = self.file(project_id, uid)
                with decoder_input(source, self.library.data / "tmp") as input_file:
                    probe = subprocess.run(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-protocol_whitelist",
                            "file,subfile",
                            "-format_whitelist",
                            FORMATS,
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "json",
                            input_file.url,
                        ],
                        capture_output=True,
                        pass_fds=input_file.pass_fds,
                        timeout=30,
                        check=True,
                    )
                    duration = float(json.loads(probe.stdout)["format"].get("duration", 0))
                    command = [
                        "ffmpeg",
                        "-nostdin",
                        "-y",
                        "-v",
                        "error",
                        "-protocol_whitelist",
                        "file,subfile",
                        "-format_whitelist",
                        FORMATS,
                        "-i",
                        input_file.url,
                        "-threads",
                        "2",
                        "-map_metadata",
                        "-1",
                        "-sn",
                        "-dn",
                    ]
                    if target.suffix == ".mp4":
                        command += [
                            "-vf",
                            "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                            "-r",
                            "30",
                            "-c:v",
                            "libx264",
                            "-preset",
                            "veryfast",
                            "-crf",
                            "20",
                            "-pix_fmt",
                            "yuv420p",
                        ]
                    else:
                        command += ["-vn"]
                    command += [
                        "-c:a",
                        "aac",
                        "-b:a",
                        "160k",
                        "-movflags",
                        "+faststart",
                        "-progress",
                        "pipe:1",
                        str(temp),
                    ]
                    with self._conversion_process(
                        command,
                        pass_fds=input_file.pass_fds,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        start_new_session=True,
                    ) as process:
                        timeout = threading.Timer(3600, process.kill)
                        timeout.daemon = True
                        timeout.start()
                        deadline = time.monotonic() + 3600
                        for line in process.stdout:
                            if time.monotonic() > deadline:
                                process.kill()
                                raise TimeoutError
                            if line.startswith("out_time_us=") and duration > 0:
                                self._preview_update(
                                    project_id,
                                    uid,
                                    folder,
                                    dict(
                                        state="running",
                                        progress=min(
                                            99, int(int(line.split("=")[1]) / duration / 10000)
                                        ),
                                    ),
                                )
                        timeout.cancel()
                        if process.wait() != 0:
                            raise ValueError("Media conversion failed")
                with self.library.lock:
                    if self.entry(project_id, uid)[0] != folder.parent.parent:
                        raise ValueError(text("assets.changed"))
                    source.validate()
                    os.replace(temp, target)
                    self._preview_update(
                        project_id, uid, folder, dict(state="completed", progress=100)
                    )
            except Exception:
                LOG.exception("Media preview failed: game=%s asset=%s", project_id, uid)
                self._preview_update(
                    project_id,
                    uid,
                    folder,
                    dict(
                        state="failed",
                        progress=0,
                        messages={"error": {"code": "assets.previewUnavailable", "params": {}}},
                    ),
                )
                temp.unlink(missing_ok=True)
            finally:
                with self.library.db.transaction() as db:
                    db.execute(
                        "UPDATE content_operations SET state='completed' WHERE id=?", (operation,)
                    )

    def _preview_update(self, project_id, uid, folder, value):
        with self.library.lock:
            try:
                if self.entry(project_id, uid)[0] != folder.parent.parent or not folder.is_dir():
                    return
            except (KeyError, ValueError, OSError):
                return
            atomic_json(folder / "status.json", value)

    @contextmanager
    def _conversion_process(self, command, **kwargs):
        with self.lifecycle:
            if self.stopping.is_set():
                raise InterruptedError
            process = subprocess.Popen(command, **kwargs)
            self.processes.add(process)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout:
                process.stdout.close()
            with self.lifecycle:
                self.processes.discard(process)

    def close(self):
        with self.lifecycle:
            self.stopping.set()
            processes = list(self.processes)
            threads = list(self.threads.values())
        errors = []
        try:
            self.preparations.close()
        except Exception as error:
            errors.append(error)
        try:
            self.thumbnails.close()
        except Exception as error:
            errors.append(error)
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            except OSError as error:
                errors.append(error)
        for thread in threads:
            thread.join()
        if errors:
            raise ExceptionGroup("Asset shutdown failed", errors)

    def preview_status(self, project_id, uid):
        root, _ = self.entry(project_id, uid)
        self.file(project_id, uid).validate()
        path = safe(root, "previews/" + uid + "/status.json")
        value = json.loads(path.read_text()) if path.exists() else dict(state="missing", progress=0)
        if value["state"] == "running":
            thread = self.threads.get((project_id, uid))
            if thread is None or not thread.is_alive():
                value = dict(state="failed", progress=0)
        return value

    def preview_file(self, project_id, uid):
        root, entry = self.entry(project_id, uid)
        self.file(project_id, uid).validate()
        path = safe(
            root,
            "previews/" + uid + ("/preview.mp4" if entry["kind"] == "video" else "/preview.m4a"),
        )
        if not path.is_file():
            raise KeyError(uid)
        return path
