"""Offline, journaled directory moves; game bytes never pass through this module."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from vn_stream.engines.directories import directories
from vn_stream.engines.registry import ADAPTERS
from vn_stream.projects.catalog import ProjectCatalog
from vn_stream.projects.manifest import read_manifest
from vn_stream.storage.files import atomic_json, sync_folder

PATH_KEYS = frozenset(
    {
        "path",
        "source",
        "target",
        "project",
        "staging",
        "quarantine",
        "source_path",
        "target_path",
        "project_path",
        "cwd",
        "root",
        "target_root",
    }
)
PATH_LIST_KEYS = frozenset({"paths", "created_directories"})
RECORD_TABLES = (
    "project_deletions",
    "import_jobs",
    "content_operations",
    "save_migrations",
    "save_signing_jobs",
    "asset_jobs",
    "asset_preparations",
)


def relocate_value(value, mapping):
    for old in sorted(mapping, key=len, reverse=True):
        if value == old or value.startswith(old.rstrip("/") + "/"):
            return mapping[old] + value[len(old) :]
    return value


def relocate_record(value, mapping, key=None):
    if isinstance(value, dict):
        if key == "directory_identities":
            return {relocate_value(k, mapping): v for k, v in value.items()}
        return {k: relocate_record(v, mapping, k) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate_record(v, mapping, key) for v in value]
    if isinstance(value, str) and key in PATH_KEYS | PATH_LIST_KEYS:
        return relocate_value(value, mapping)
    return value


@contextmanager
def offline_lease(data):
    import fcntl

    with (data / "layout.lock").open("a") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "Stop the service before applying or rolling back a layout migration"
            ) from error
        yield


class LayoutMigration:
    def __init__(self, data: Path, games_root: Path, mounts: dict[str, Path]):
        self.data, self.root = data.resolve(), games_root.resolve()
        self.database = self.data / "database/vn-stream.sqlite3"
        self.mounts = {key.rstrip("/"): value.resolve() for key, value in mounts.items()}
        self.marker = self.data / "layout-maintenance.json"

    def _physical(self, path):
        for prefix in sorted(self.mounts, key=len, reverse=True):
            if path == prefix or path.startswith(prefix + "/"):
                return self.mounts[prefix] / Path(path).relative_to(prefix)
        candidate = Path(path)
        if candidate.is_relative_to(self.root):
            return candidate
        raise ValueError("Project path is outside the declared old mounts")

    def _db(self):
        return sqlite3.connect(self.database.as_uri() + "?mode=rw", uri=True)

    @staticmethod
    def _identity(path):
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError("Migration does not follow symbolic links")
        stat = path.stat()
        return [stat.st_dev, stat.st_ino]

    def preview(self):
        if self.marker.exists():
            raise ValueError("Recover the pending layout migration first")
        groups = directories(self.root, ADAPTERS)
        moves, projects = [], []
        with self._db() as db:
            rows = db.execute(
                "SELECT project_id,engine_id,path FROM projects ORDER BY project_id"
            ).fetchall()
        for project_id, engine_id, old in rows:
            source = self._physical(old)
            if (
                source == self.root
                or source.is_relative_to(self.data)
                or not source.is_relative_to(self.root)
            ):
                raise ValueError("Project is not inside the declared game root")
            read_manifest(source, project_id)
            target = groups[engine_id] / source.name
            projects.append(
                dict(
                    project_id=project_id,
                    old=old,
                    new="/games/" + target.relative_to(self.root).as_posix(),
                )
            )
            if source != target:
                moves.append(self._move(source, target))
        runtime_root = self.data / "runtimes"
        runtime_groups = directories(runtime_root, ADAPTERS)
        for source in sorted(runtime_root.iterdir()) if runtime_root.exists() else []:
            if source.name.startswith(".") or source in runtime_groups.values():
                continue
            if source.is_symlink() or not source.is_dir():
                raise ValueError("Unexpected entry in runtime installation root")
            if (source / "renpy.sh").is_file():
                engine_id = "renpy"
            elif (source / "runtime.json").is_file():
                engine_id = json.loads((source / "runtime.json").read_text())["engine_id"]
            else:
                raise ValueError("Unrecognized runtime installation")
            moves.append(self._move(source, runtime_groups[engine_id] / source.name))
        targets = [move["target"] for move in moves]
        if len(targets) != len(set(targets)):
            raise ValueError("Multiple projects have the same destination")
        return dict(
            format="vn-stream/layout-1",
            id=uuid4().hex,
            data=str(self.data),
            games_root=str(self.root),
            state="preview",
            projects=projects,
            moves=moves,
        )

    def _move(self, source, target):
        if target.exists() or target.is_symlink():
            raise ValueError(f"Destination already exists: {target}")
        parent = target.parent
        while not parent.exists():
            parent = parent.parent
        identity = self._identity(source)
        if identity[0] != parent.stat().st_dev:
            raise ValueError("Cross-filesystem moves require a separate, explicit migration")
        return dict(source=str(source), target=str(target), identity=identity)

    def _validate(self, plan):
        if (
            plan.get("format") != "vn-stream/layout-1"
            or plan.get("data") != str(self.data)
            or plan.get("games_root") != str(self.root)
            or len(plan["id"]) != 32
            or any(c not in "0123456789abcdef" for c in plan["id"])
        ):
            raise ValueError("Migration plan belongs to another installation")
        for move in plan["moves"]:
            for key in ("source", "target"):
                path = Path(move[key])
                if path == self.root or not path.is_relative_to(self.root) or ".." in path.parts:
                    raise ValueError("Invalid migration path")

    def _journal(self, plan):
        return self.data / "tasks/layout" / plan["id"]

    @staticmethod
    def _backup_database(source, target):
        with sqlite3.connect(source) as original, sqlite3.connect(target) as copied:
            original.backup(copied)
        target.chmod(0o600)

    def _assert_idle(self):
        with self._db() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in RECORD_TABLES:
                if table not in tables:
                    continue
                columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                for row in db.execute(f"SELECT record FROM {table}"):
                    record = json.loads(row[0])
                    if record.get("state") in ("running", "recovering", "pending", "queued"):
                        raise ValueError(
                            f"Pending task must be completed before migration: {table}"
                        )
                if (
                    "state" in columns
                    and db.execute(
                        f"SELECT 1 FROM {table} WHERE state IN ('running','recovering','pending','queued') LIMIT 1"
                    ).fetchone()
                ):
                    raise ValueError(f"Pending task must be completed before migration: {table}")

    def _prepare_journal(self, plan):
        folder = self._journal(plan)
        existing = folder / "journal.json"
        if existing.exists():
            return json.loads(existing.read_text())
        self._assert_idle()
        with self._db() as db:
            current = dict(db.execute("SELECT project_id,path FROM projects"))
        if current != {p["project_id"]: p["old"] for p in plan["projects"]}:
            raise ValueError("Project records changed; generate a new preview")
        for move in plan["moves"]:
            if (
                self._identity(Path(move["source"])) != move["identity"]
                or Path(move["target"]).exists()
            ):
                raise ValueError("Directories changed; generate a new preview")
        # No moves occur before journal publication. The durable marker holds
        # the service offline while an interrupted preparation is rebuilt.
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._identity(folder)
        snapshot = folder / "database.sqlite3"
        snapshot.unlink(missing_ok=True)
        self._backup_database(self.database, snapshot)
        with snapshot.open("rb") as stream:
            os.fsync(stream.fileno())
        files = []
        for source in sorted((self.data / "save-backups").glob("*/*/record.json")):
            if source.is_symlink() or any(p.is_symlink() for p in source.parents):
                raise ValueError("Backup metadata contains a symbolic link")
            backup = f"json-{len(files)}"
            shutil.copy2(source, folder / backup)
            with (folder / backup).open("rb") as stream:
                os.fsync(stream.fileno())
            files.append(
                dict(
                    path=str(source),
                    backup=backup,
                    sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                )
            )
        journal = dict(plan, state="prepared", files=files)
        atomic_json(existing, journal)
        sync_folder(folder.parent)
        return journal

    def _check_parent(self, path):
        parent = path.parent
        while not parent.exists() and not parent.is_symlink():
            parent = parent.parent
        self._identity(parent)
        if path.resolve() != path:
            raise ValueError("Migration does not follow symbolic links")

    def _update_database(self, plan, mapping):
        with self._db() as db:
            for project in plan["projects"]:
                db.execute(
                    "UPDATE projects SET path=? WHERE project_id=?",
                    (project["new"], project["project_id"]),
                )
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in RECORD_TABLES:
                if table not in tables:
                    continue
                for rowid, raw in db.execute(f"SELECT rowid,record FROM {table}").fetchall():
                    rewritten = relocate_record(json.loads(raw), mapping)
                    db.execute(
                        f"UPDATE {table} SET record=? WHERE rowid=?",
                        (json.dumps(rewritten, ensure_ascii=False), rowid),
                    )
            if "save_migration_previews" in tables:
                db.execute("DELETE FROM save_migration_previews")

    def apply(self, plan):
        self._validate(plan)
        with offline_lease(self.data):
            if self.marker.exists() and json.loads(self.marker.read_text())["id"] != plan["id"]:
                raise ValueError("Another layout migration needs recovery")
            existing = self._journal(plan) / "journal.json"
            if not existing.exists():
                self._assert_idle()
            # Persist the startup barrier before creating any recovery snapshot.
            atomic_json(self.marker, {"id": plan["id"]})
            journal = self._prepare_journal(plan)
            if journal["state"] == "completed":
                if self.marker.exists():
                    self.marker.unlink()
                    sync_folder(self.data)
                return journal
            if journal["state"] == "rolled_back":
                raise ValueError("Generate a new preview after rollback")
            for move in plan["moves"]:
                source, target = Path(move["source"]), Path(move["target"])
                self._check_parent(source)
                self._check_parent(target)
                if (
                    target.exists()
                    and not source.exists()
                    and self._identity(target) == move["identity"]
                ):
                    continue
                if self._identity(source) != move["identity"]:
                    raise ValueError("Source directory identity changed")
                target.parent.mkdir(parents=True, exist_ok=True)
                self._check_parent(target)
                ProjectCatalog.rename_new(source, target)
                sync_folder(source.parent)
                sync_folder(target.parent)
            mapping = {p["old"]: p["new"] for p in plan["projects"]}
            for item in journal["files"]:
                original = json.loads((self._journal(plan) / item["backup"]).read_text())
                atomic_json(Path(item["path"]), relocate_record(original, mapping))
            self._update_database(plan, mapping)
            journal["state"] = "completed"
            atomic_json(self._journal(plan) / "journal.json", journal)
            self.marker.unlink()
            sync_folder(self.data)
            return journal

    def rollback(self, plan):
        self._validate(plan)
        with offline_lease(self.data):
            if self.marker.exists() and json.loads(self.marker.read_text())["id"] != plan["id"]:
                raise ValueError("Another layout migration needs recovery")
            journal_file = self._journal(plan) / "journal.json"
            if not journal_file.exists():
                # Preparation has not published a move-capable journal yet.
                if self.marker.exists():
                    self.marker.unlink()
                    sync_folder(self.data)
                return
            journal = json.loads(journal_file.read_text())
            atomic_json(self.marker, {"id": plan["id"]})
            for move in reversed(plan["moves"]):
                source, target = Path(move["source"]), Path(move["target"])
                self._check_parent(source)
                self._check_parent(target)
                if (
                    source.exists()
                    and not target.exists()
                    and self._identity(source) == move["identity"]
                ):
                    continue
                if self._identity(target) != move["identity"]:
                    raise ValueError("Moved directory identity changed; refusing rollback")
                source.parent.mkdir(parents=True, exist_ok=True)
                self._check_parent(source)
                ProjectCatalog.rename_new(target, source)
                sync_folder(source.parent)
                sync_folder(target.parent)
            for item in journal["files"]:
                atomic_json(
                    Path(item["path"]),
                    json.loads((self._journal(plan) / item["backup"]).read_text()),
                )
            self._backup_database(self._journal(plan) / "database.sqlite3", self.database)
            journal["state"] = "rolled_back"
            atomic_json(self._journal(plan) / "journal.json", journal)
            self.marker.unlink()
            sync_folder(self.data)


def main():
    parser = argparse.ArgumentParser(description="Offline engine directory migration")
    parser.add_argument("action", choices=("preview", "apply", "rollback"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--games-root", type=Path, required=True)
    parser.add_argument(
        "--mount", action="append", default=[], help="Old container prefix=physical directory"
    )
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    operation = LayoutMigration(
        args.data, args.games_root, {k: Path(v) for k, v in (m.split("=", 1) for m in args.mount)}
    )
    if args.action == "preview":
        plan = operation.preview()
        with args.plan.open("x") as output:
            json.dump(plan, output, ensure_ascii=False, indent=2)
        args.plan.chmod(0o600)
        print(json.dumps({"projects": len(plan["projects"]), "moves": len(plan["moves"])}))
    else:
        plan = json.loads(args.plan.read_text())
        getattr(operation, args.action)(plan)
        print(args.action + " completed")


if __name__ == "__main__":
    main()
