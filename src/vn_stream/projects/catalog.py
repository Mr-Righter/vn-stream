"""Game-owned manifests make complete directories portable; SQLite is a searchable index."""

import ctypes
import errno
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from threading import RLock
from uuid import uuid4

from vn_stream.engines.directories import directories
from vn_stream.engines.registry import ADAPTERS, inspect_project
from vn_stream.i18n import text
from vn_stream.imports.archive import (
    Limits,
)
from vn_stream.projects.maintenance import ProjectMaintenance
from vn_stream.projects.manifest import read_manifest
from vn_stream.projects.models import Project
from vn_stream.storage.database import Database
from vn_stream.storage.paths import DataPaths


class ProjectCatalog:
    def __init__(
        self,
        data: Path,
        roots: list[Path],
        *,
        limits: Limits | None = None,
        library_root: Path | None = None,
        discovery_roots: list[Path] | None = None,
    ):
        self.data = data.resolve()
        self.roots = list(dict.fromkeys(root.resolve() for root in roots))
        self.library_root = (library_root or self.data.parent / "projects").resolve()
        self.discovery_roots = list(
            dict.fromkeys(p.resolve() for p in [self.library_root, *(discovery_roots or [])])
        )
        self.library_root.mkdir(parents=True, exist_ok=True)
        self.limits = limits or Limits()
        self.lock = RLock()
        self.on_registered = None
        self.paths = DataPaths(self.data)
        self.paths.initialize()
        self.database = self.paths.database
        self.db = Database(self.database)
        self.maintenance = ProjectMaintenance()
        with self.db.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS project_deletions (id TEXT PRIMARY KEY, project_id TEXT UNIQUE, state TEXT, record TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS projects "
                "(project_id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT UNIQUE NOT NULL, engine_version TEXT, engine_id TEXT NOT NULL)"
            )

    def category_directories(self):
        return directories(self.library_root, ADAPTERS)

    def check_source_location(self, path):
        if path == self.data or path.is_relative_to(self.data):
            raise ValueError(text("content.unavailable"))
        if path.is_relative_to(self.library_root):
            relative = path.relative_to(self.library_root)
            if any(part.startswith(".") for part in relative.parts):
                raise ValueError(text("content.unavailable"))
        if path == self.library_root or path in self.category_directories().values():
            raise ValueError(text("content.unavailable"))

    def check_classification(self, path, engine_id):
        if not path.is_relative_to(self.library_root):
            return
        expected = self.category_directories().get(engine_id)
        if expected is None or not path.is_relative_to(expected) or path == expected:
            raise ValueError(
                text("projects.wrongDirectory", directory=expected.name if expected else "")
            )

    def list(self) -> list[Project]:
        with self.db.transaction() as db:
            return [
                Project(*row)
                for row in db.execute(
                    "SELECT project_id,name,path,engine_version,engine_id FROM projects ORDER BY name, project_id"
                )
            ]

    def get(self, project_id: str) -> Project:
        with self.db.transaction() as db:
            row = db.execute(
                "SELECT project_id,name,path,engine_version,engine_id FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return Project(*row)

    def record(self, game: Project) -> Project:
        with self.lock:
            self.maintenance.check(game.project_id)
            with self.db.transaction() as db:
                self.write_record(db, game)
            if self.on_registered:
                self.on_registered(game.project_id)
            return game

    def write_record(self, connection, game: Project) -> None:
        connection.execute(
            "INSERT INTO projects(project_id,name,path,engine_version,engine_id) VALUES(?,?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET name=excluded.name,path=excluded.path,engine_version=excluded.engine_version,engine_id=excluded.engine_id",
            (game.project_id, game.name, game.path, game.engine_version, game.engine_id),
        )

    def register(self, project: Path, name: str, *, engine_id=None) -> Project:
        return self.record(self.prepare_project(project, name, engine_id=engine_id))

    def prepare_project(
        self, project: Path, name: str, *, target=None, new_identity=False, engine_id=None
    ) -> Project:
        self.check_not_deleting(project)
        if engine_id is None:
            try:
                previous_manifest = read_manifest(project)
                engine_id = previous_manifest.get("engine_id", "renpy")
            except (OSError, ValueError, KeyError, TypeError):
                pass
        inspection = inspect_project(project, engine_id=engine_id)
        if inspection.engine_id is None:
            raise ValueError(text("engine.unrecognized"))
        if any(p.is_symlink() for p in project.rglob("*")):
            raise ValueError(text("errors.theProjectContainsLinksPrepareACompleteFolderWithout"))
        metadata = project / ".vn-stream"
        previous = {}
        if metadata.exists():
            try:
                content = json.loads((metadata / "project.json").read_text())
                project_id = content["project_id"]
                if (
                    content["format"] != "vn-stream/1"
                    or len(project_id) != 32
                    or any(c not in "0123456789abcdef" for c in project_id)
                ):
                    raise ValueError(text("errors.invalidManifestFormat"))
                with self.db.transaction() as db:
                    deletion = db.execute(
                        "SELECT state FROM project_deletions WHERE project_id=?",
                        (project_id,),
                    ).fetchone()
                if deletion and deletion[0] != "completed":
                    raise ValueError(text("errors.aDeletionTaskIsPendingForThisGameIdentity"))
                if deletion:
                    new_identity = True
                    content["restored_from"] = {"project_id": project_id}
                try:
                    indexed = self.get(project_id)
                except KeyError:
                    indexed = None
                if (
                    not new_identity
                    and indexed
                    and Path(indexed.path) != project
                    and Path(indexed.path).exists()
                ):
                    raise ValueError(
                        text("errors.thisGameIdentityIsAlreadyRegisteredInAnotherFolder")
                    )
                if content.get("engine_id", "renpy") != inspection.engine_id:
                    raise ValueError(text("engine.unrecognized"))
                if not new_identity:
                    from vn_stream.projects.locations import check_busy

                    check_busy(self, project_id)
                    return Project(
                        project_id,
                        content["name"],
                        str(target or project),
                        content["engine_version"],
                        content.get("engine_id", "renpy"),
                    )
                previous = content
            except (KeyError, TypeError, json.JSONDecodeError, OSError) as error:
                raise ValueError(
                    text("errors.theReservedManagementFolderExistsWithAnUnrecognizedManifest")
                ) from error
        game = Project(
            uuid4().hex,
            name,
            str(target or project),
            inspection.engine_version or previous.get("engine_version"),
            inspection.engine_id,
        )
        created_metadata = not metadata.exists()
        metadata.mkdir(exist_ok=new_identity)
        pending = metadata / ("manifest-" + uuid4().hex + ".pending")
        try:
            manifest = {**previous, "format": "vn-stream/1", **asdict(game)}
            # A portable manifest must not carry the original host's absolute game path.
            manifest.pop("path")
            pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            os.replace(pending, metadata / "project.json")
            return game
        except Exception:
            pending.unlink(missing_ok=True)
            if created_metadata:
                shutil.rmtree(metadata)
            raise

    def discover(self, **limits):
        from vn_stream.projects.discovery import discover

        return discover(self, **limits)

    def check_not_deleting(self, project):
        with self.db.transaction() as db:
            rows = db.execute("SELECT record FROM project_deletions WHERE state != 'completed'")
            for row in rows:
                record = json.loads(row[0])
                for blocked in (Path(record["path"]), Path(record["quarantine"])):
                    if (
                        project == blocked
                        or project.is_relative_to(blocked)
                        or blocked.is_relative_to(project)
                    ):
                        raise ValueError(text("errors.theGameProjectIsBeingDeletedOrAwaitingA"))

    def runtime_version(self, project_id: str) -> str | None:
        game = self.get(project_id)
        manifest = Path(game.path) / ".vn-stream/project.json"
        try:
            content = json.loads(manifest.read_text())
            return content.get("runtime_version") if isinstance(content, dict) else None
        except (OSError, ValueError):
            # An offline/moved NAS game must not make the entire catalog unavailable.
            return None

    def runtime_override(self, project_id: str) -> str | None:
        game = self.get(project_id)
        try:
            return json.loads((Path(game.path) / ".vn-stream/project.json").read_text()).get(
                "runtime_override"
            )
        except (OSError, ValueError, AttributeError):
            return None

    def runtime_id(self, project_id):
        game = self.get(project_id)
        try:
            content = read_manifest(Path(game.path), project_id)
            return content.get("runtime_id") or (
                "renpy:" + content["runtime_version"]
                if content.get("runtime_version") and game.engine_id == "renpy"
                else None
            )
        except (ValueError, OSError):
            return None

    def select_runtime(
        self,
        project_id: str,
        version: str,
        *,
        override: str | None = None,
        runtime_id: str | None = None,
    ) -> None:
        with self.lock:
            game = self.get(project_id)
            project = Path(game.path)
            manifest = project / ".vn-stream/project.json"
            content = read_manifest(project, project_id)
            if (
                content.get("runtime_version") == version
                and content.get("runtime_override") == override
                and content.get("runtime_id") == runtime_id
            ):
                return
            content["runtime_id"] = runtime_id
            content["runtime_version"] = version
            content["runtime_override"] = override
            pending = manifest.with_suffix(".pending")
            try:
                pending.write_text(json.dumps(content, ensure_ascii=False, indent=2))
                os.replace(pending, manifest)
            finally:
                pending.unlink(missing_ok=True)

    @staticmethod
    def rename_new(source, target):
        # Linux renameat2 prevents even an external writer's empty directory from being
        # overwritten between our collision check and atomic publication.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOSYS, text("errors.atomicPublicationRequiresRenameat2Support"))
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
