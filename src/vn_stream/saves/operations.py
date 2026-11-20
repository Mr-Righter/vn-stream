"""Revision-checked save management with small independent backups and recovery journals."""

import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from uuid import uuid4

from vn_stream.i18n import text
from vn_stream.projects.locations import initialize, project
from vn_stream.saves.renpy import pickle_codec as save_codec
from vn_stream.saves.renpy.signatures import Signatures
from vn_stream.storage.files import atomic_json, safe, sync_folder

LOG = logging.getLogger(__name__)


class Saves:
    def __init__(self, library, sessions, imports=None):
        self.library, self.sessions, self.imports = library, sessions, imports
        initialize(library)
        library.maintenance.bind_session(lambda: self.sessions.status())
        self.root = library.data / "save-backups"
        self.root.mkdir(exist_ok=True)
        self.recover()

    def locations(self, project_id):
        from vn_stream.projects.locations import require_capability

        require_capability(self.library, project_id, "save_management")
        p = project(self.library, project_id)
        # Ren'Py 7.8+ automatically adds this local sync location, including imported saves.
        result = [safe(p, "game/saves"), safe(p, "game/saves/sync")]
        report = safe(p, ".vn-stream/save-locations.json")
        if report.is_file() and report.stat().st_size <= 64 * 1024:
            value = json.loads(report.read_text())
            for relative in value.get("locations", [])[:16]:
                if type(relative) is str:
                    candidate = safe(p, relative)
                    if candidate not in result and (
                        candidate.is_relative_to(p / ".vn-stream/home")
                        or candidate.is_relative_to(p / "game/saves")
                    ):
                        result.append(candidate)
        return [path for path in result if path.is_dir()]

    @staticmethod
    def identifier(name):
        return (
            "persistent" if name == "persistent" else hashlib.sha256(name.encode()).hexdigest()[:32]
        )

    def save_copies(self, project_id):
        files = {}
        for location in self.locations(project_id):
            for path in location.iterdir():
                if path.is_symlink() or not path.is_file():
                    continue
                if path.name == "persistent" or path.name.endswith(".save"):
                    files.setdefault(path.name, []).append(path)
        return files

    def resolve_save(self, project_id, uid):
        for name, paths in self.save_copies(project_id).items():
            if self.identifier(name) == uid:
                return name, sorted(paths, key=lambda p: p.stat().st_mtime_ns, reverse=True)
        raise KeyError(uid)

    def revision(self, paths):
        digest = hashlib.sha256()
        for path in sorted(paths):
            digest.update(str(path).encode())
            stat = path.stat()
            digest.update(f"{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}".encode())
            with path.open("rb") as source:
                while piece := source.read(1024**2):
                    digest.update(piece)
        return digest.hexdigest()

    def detail(self, project_id, uid):
        listing = self.list(project_id)
        item = next((s for s in listing["saves"] if s["id"] == uid), None)
        if item:
            signature = "Unknown"
            try:
                data = self.read(project_id, uid)
                save_codec.unpack(data, item["kind"] == "persistent")
                signature = (
                    "Local"
                    if self._trusted(project_id, data, item["kind"] == "persistent")
                    else "Pending"
                )
            except ValueError:
                signature = "Unsupported"
            return dict(item, archived=False, writable=listing["writable"], signature=signature)
        archived = next((s for s in listing["archived"] if s["id"] == uid), None)
        if archived:
            return dict(archived, archived=True, writable=listing["writable"])
        raise KeyError(uid)

    def list(self, project_id):
        result = []
        p = project(self.library, project_id)
        for name, paths in self.save_copies(project_id).items():
            latest = max(paths, key=lambda x: x.stat().st_mtime_ns)
            kind = (
                "persistent"
                if name == "persistent"
                else "auto"
                if name.startswith("auto-")
                else "quick"
                if name.startswith("quick-")
                else "manual"
            )
            metadata = {}
            thumbnail = False
            try:
                if name != "persistent" and latest.stat().st_size <= save_codec.MAX_FILE:
                    with zipfile.ZipFile(latest) as z:
                        if z.getinfo("json").file_size <= 64 * 1024:
                            value = json.loads(z.read("json"))
                            if isinstance(value, dict):
                                metadata = {
                                    k: value[k]
                                    for k in ("_save_name", "_version", "_ctime")
                                    if type(value.get(k)) in (str, int, float)
                                }
                        metadata = {
                            key: value.encode("utf-8", errors="replace").decode("utf-8")
                            if isinstance(value, str)
                            else value
                            for key, value in metadata.items()
                            if not isinstance(value, float) or math.isfinite(value)
                        }
                        thumbnail = "screenshot.png" in z.namelist()
            except (ValueError, KeyError, OSError, zipfile.BadZipFile):
                pass
            result.append(
                dict(
                    id=self.identifier(name),
                    name=name,
                    kind=kind,
                    updated_at=latest.stat().st_mtime,
                    size_bytes=latest.stat().st_size,
                    copies=len(paths),
                    thumbnail=thumbnail,
                    sources=[str(x.relative_to(p)) for x in paths],
                    metadata=metadata,
                )
            )
        archived = {}
        for record_path in (self.root / project_id).glob("*/record.json"):
            record = json.loads(record_path.read_text())
            if record["entries"] and any(entry["existed"] for entry in record["entries"]):
                archived[record["save_id"]] = dict(
                    id=record["save_id"], name=Path(record["entries"][0]["path"]).name
                )
        return dict(
            saves=sorted(result, key=lambda x: (x["kind"] != "persistent", -x["updated_at"])),
            writable=self.writable(project_id),
            archived=list(archived.values()),
        )

    def writable(self, project_id, *, signing_owner=None, import_owner=None, migration_owner=None):
        try:
            self.library.maintenance.check(
                project_id,
                include_session=True,
                include_imports=True,
                signing_owner=signing_owner,
                import_owner=import_owner,
                migration_owner=migration_owner,
            )
        except ValueError:
            return False
        return True

    def require_writable(self, project_id, **owners):
        project(self.library, project_id)
        if not self.writable(project_id, **owners):
            raise ValueError(text("saves.stopFirst"))

    def read(self, project_id, uid):
        _, paths = self.resolve_save(project_id, uid)
        if paths[0].stat().st_size > save_codec.MAX_FILE:
            raise ValueError(text("saves.tooLarge"))
        return paths[0].read_bytes()

    def variables(self, project_id, uid):
        name, paths = self.resolve_save(project_id, uid)
        revision = self.revision(paths)
        try:
            info = save_codec.inspect(self.read(project_id, uid), name == "persistent")
            can_sign = not info["signed"] or self._trusted(
                project_id, self.read(project_id, uid), name == "persistent"
            )
            return dict(
                fields=info["fields"],
                revision=revision,
                editable=can_sign,
                writable=self.writable(project_id),
                reason=None if can_sign else "saves.untrusted",
                counts={
                    k: sum(f["type"] == k for f in info["fields"])
                    for k in ("boolean", "text", "number")
                },
            )
        except (ValueError, zipfile.BadZipFile):
            return dict(
                fields=[],
                revision=revision,
                editable=False,
                writable=self.writable(project_id),
                reason="saves.unsupported",
                counts={k: 0 for k in ("boolean", "text", "number")},
            )

    def _signer(self, project_id):
        return Signatures(self.library, project_id).sign

    def _trusted(self, project_id, data, persistent):
        try:
            log, signatures, _ = save_codec.unpack(data, persistent)
            return Signatures(self.library, project_id).verify(log, signatures)
        except (ValueError, UnicodeError):
            return False

    def update(self, project_id, uid, revision, changes):
        with self.library.lock:
            self.require_writable(project_id)
            name, paths = self.resolve_save(project_id, uid)
            if self.revision(paths) != revision:
                raise ValueError(text("saves.changed"))
            raw = self.read(project_id, uid)
            try:
                modified = save_codec.edit(
                    raw,
                    changes,
                    name == "persistent",
                    self._signer(project_id),
                )
            except save_codec.Invalid as exc:
                raise ValueError(text("saves.unsupported")) from exc
            self.commit_files(project_id, uid, paths, modified, "edit", expected_revision=revision)
            return dict(revision=self.revision(paths))

    def commit_files(
        self,
        project_id,
        uid,
        paths,
        data,
        kind,
        *,
        snapshot_only=False,
        request_id=None,
        preserve_mtime=False,
        expected_revision=None,
    ):
        existing = [path for path in paths if path.exists()]
        original_revision = self.revision(existing) if existing else ""
        if expected_revision is not None and original_revision != expected_revision:
            raise ValueError(text("saves.changed"))
        operation = uuid4().hex
        folder = self.root / project_id / operation
        folder.mkdir(parents=True)
        entries = []
        for i, path in enumerate(paths):
            existed = path.exists()
            if existed:
                shutil.copy2(path, folder / str(i))
                with (folder / str(i)).open("rb") as backup:
                    os.fsync(backup.fileno())
            entries.append(
                dict(
                    path=str(path),
                    backup=str(i),
                    existed=existed,
                    mtime_ns=path.stat().st_mtime_ns if existed else None,
                )
            )
        if (self.revision(existing) if existing else "") != original_revision or any(
            path.exists() for path in paths if path not in existing
        ):
            raise ValueError(text("saves.changed"))
        record = dict(
            id=operation,
            project_id=project_id,
            save_id=uid,
            kind=kind,
            created_at=time.time(),
            entries=entries,
            state="running",
            request_id=request_id,
            primary=max(
                (entry for entry in entries if entry["existed"]),
                key=lambda entry: entry["mtime_ns"],
                default={"backup": None},
            )["backup"],
        )
        atomic_json(folder / "record.json", record)
        with self.library.db.transaction() as db:
            db.execute(
                "INSERT INTO content_operations VALUES (?,?,?,?,?)",
                (operation, project_id, "saves", "running", json.dumps(record)),
            )
        try:
            for item in entries:
                if snapshot_only:
                    continue
                path = Path(item["path"])
                current_data = data.get(path) if isinstance(data, dict) else data
                if current_data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    pending = path.with_name(path.name + "." + operation + ".new")
                    with pending.open("xb") as f:
                        f.write(current_data)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(pending, path)
                    if preserve_mtime and item.get("mtime_ns") is not None:
                        os.utime(path, ns=(item["mtime_ns"], item["mtime_ns"]))
                        with path.open("rb") as written:
                            os.fsync(written.fileno())
                sync_folder(path.parent)
            record["state"] = "completed"
        except Exception:
            self.rollback_files(record, folder)
            record["state"] = "failed"
            raise
        finally:
            atomic_json(folder / "record.json", record)
            with self.library.db.transaction() as db:
                db.execute(
                    "UPDATE content_operations SET state=?,record=? WHERE id=?",
                    (record["state"], json.dumps(record), operation),
                )
        return record

    def rollback_files(self, record, folder):
        p = project(self.library, record["project_id"])
        for entry in record["entries"]:
            path = Path(entry["path"])
            safe(p, str(path.relative_to(p)))
            if entry["existed"]:
                pending = path.with_name(path.name + ".recovery-" + record["id"])
                shutil.copy2(folder / entry["backup"], pending)
                with pending.open("rb") as restored:
                    os.fsync(restored.fileno())
                os.replace(pending, path)
            else:
                path.unlink(missing_ok=True)
            path.with_name(path.name + "." + record["id"] + ".new").unlink(missing_ok=True)
            sync_folder(path.parent)

    def recover(self):
        with self.library.db.transaction() as db:
            rows = db.execute(
                "SELECT record FROM content_operations WHERE kind='saves' AND state IN ('running','recovering')"
            ).fetchall()
        for row in rows:
            record = json.loads(row[0])
            folder = self.root / record["project_id"] / record["id"]
            try:
                self.rollback_files(record, folder)
                record["state"] = "recovered"
                atomic_json(folder / "record.json", record)
                with self.library.db.transaction() as db:
                    db.execute(
                        "UPDATE content_operations SET state=?,record=? WHERE id=?",
                        ("recovered", json.dumps(record), record["id"]),
                    )
            except Exception:
                LOG.exception("Save operation recovery failed: operation=%s", record["id"])

    def delete(self, project_id, uid, revision):
        with self.library.lock:
            self.require_writable(project_id)
            name, paths = self.resolve_save(project_id, uid)
            if name == "persistent":
                raise ValueError(text("content.invalid"))
            if self.revision(paths) != revision:
                raise ValueError(text("saves.changed"))
            return self.commit_files(project_id, uid, paths, None, "delete")

    def upload(self, project_id, name, data, revision=None):
        if name != "persistent" and not re.fullmatch(r"[\w .-]{1,180}\.save", name):
            raise ValueError(text("content.invalid"))
        if len(data) > save_codec.MAX_FILE:
            raise ValueError(text("saves.tooLarge"))
        try:
            save_codec.unpack(data, name == "persistent")
        except ValueError as exc:
            raise ValueError(text("saves.unsupported")) from exc
        with self.library.lock:
            self.require_writable(project_id)
            paths = self.save_copies(project_id).get(name, [])
            if paths and self.revision(paths) != revision:
                raise ValueError(text("saves.changed"))
            data = Signatures(self.library, project_id).resign(data, name == "persistent")

            if not paths:
                p = project(self.library, project_id)
                locations = self.locations(project_id) or [safe(p, "game/saves")]
                paths = [safe(loc, name) for loc in locations]
            self.commit_files(
                project_id,
                self.identifier(name),
                paths,
                data,
                "upload",
                expected_revision=revision or "",
            )
        return dict(id=self.identifier(name))

    def backup(self, project_id, uid, revision, request_id):
        with self.library.lock:
            self.require_writable(project_id)
            with self.library.db.transaction() as db:
                rows = db.execute(
                    "SELECT record FROM content_operations WHERE project_id=? AND kind='saves'",
                    (project_id,),
                ).fetchall()
            for row in rows:
                record = json.loads(row[0])
                if record.get("kind") == "manual" and record.get("request_id") == request_id:
                    if record["save_id"] != uid or record["state"] != "completed":
                        raise ValueError(text("content.invalid"))
                    return record
            _, paths = self.resolve_save(project_id, uid)
            if self.revision(paths) != revision:
                raise ValueError(text("saves.changed"))
            return self.commit_files(
                project_id, uid, paths, None, "manual", snapshot_only=True, request_id=request_id
            )

    def backups(self, project_id, uid):
        project(self.library, project_id)
        result = []
        for record in (self.root / project_id).glob("*/record.json"):
            info = json.loads(record.read_text())
            if info.get("save_id") == uid and any(entry["existed"] for entry in info["entries"]):
                primary = next(
                    (
                        e
                        for e in info["entries"]
                        if e["existed"] and e["backup"] == info.get("primary")
                    ),
                    None,
                )
                primary = primary or next(e for e in info["entries"] if e["existed"])
                result.append(
                    dict(
                        {k: info[k] for k in ("id", "kind", "created_at", "state")},
                        name=Path(primary["path"]).name,
                        copies=sum(e["existed"] for e in info["entries"]),
                        size_bytes=(record.parent / primary["backup"]).stat().st_size,
                    )
                )
        return sorted(result, key=lambda r: r["created_at"], reverse=True)

    def backup_file(self, project_id, uid, backup_id):
        if not re.fullmatch("[0-9a-f]{32}", backup_id):
            raise KeyError(backup_id)
        folder = self.root / project_id / backup_id
        record = json.loads((folder / "record.json").read_text())
        if record["save_id"] != uid:
            raise KeyError(uid)
        entry = next(
            (e for e in record["entries"] if e["existed"] and e["backup"] == record.get("primary")),
            None,
        )
        entry = entry or next((e for e in record["entries"] if e["existed"]), None)
        if not entry:
            raise KeyError(uid)
        return folder / entry["backup"]

    def restore(self, project_id, uid, backup_id, revision):
        with self.library.lock:
            self.require_writable(project_id)
            data = self.backup_file(project_id, uid, backup_id).read_bytes()
            folder = self.root / project_id / backup_id
            record = json.loads((folder / "record.json").read_text())
            name = Path(record["entries"][0]["path"]).name
            paths = self.save_copies(project_id).get(name, [])
            if paths and self.revision(paths) != revision:
                raise ValueError(text("saves.changed"))
            if not paths:
                paths = [safe(location, name) for location in self.locations(project_id)]
                if not paths:
                    paths = [safe(project(self.library, project_id), "game/saves/" + name)]
            data = Signatures(self.library, project_id).resign(data, name == "persistent")
            return self.commit_files(
                project_id, uid, paths, data, "restore", expected_revision=revision or ""
            )

    def screenshot(self, project_id, uid):
        with zipfile.ZipFile(io.BytesIO(self.read(project_id, uid))) as z:
            info = z.getinfo("screenshot.png")
            if info.file_size > 4 * 1024**2:
                raise ValueError(text("content.invalid"))
            data = z.read(info)
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(text("content.invalid"))
            return data
