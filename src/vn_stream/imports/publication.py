"""Own import staging, publication checkpoints and transactional registration."""

import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from vn_stream.engines.directories import engine_directory
from vn_stream.engines.registry import ADAPTERS, inspect_project
from vn_stream.i18n import text
from vn_stream.imports.archive import (
    SUPPORTED_EXTENSIONS,
    extract_archive,
    normalize_project,
    supports_archive,
)
from vn_stream.imports.repository import ImportRepository
from vn_stream.projects.models import Project


class ImportPublication:
    def __init__(self, catalog):
        self.catalog = catalog
        self.repository = ImportRepository(catalog.db)

    def record(self, game, *, job_id=None):
        with self.catalog.lock:
            self.catalog.maintenance.check(game.project_id, import_owner=job_id)
            with self.catalog.db.transaction() as db:
                self.catalog.write_record(db, game)
                if job_id:
                    self.repository.registered(db, job_id, game)
            if self.catalog.on_registered:
                self.catalog.on_registered(game.project_id)
            return game

    def register(self, project, name, *, job_id=None, engine_id=None):
        game = self.catalog.prepare_project(project, name, engine_id=engine_id)
        if job_id:
            self.repository.update(job_id, reserved_project=asdict(game), phase="registering")
        return self.record(game, job_id=job_id)

    def _target(self, name, engine_id):
        parent = engine_directory(self.catalog.library_root, engine_id, ADAPTERS)
        parent.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w .-]", "_", name, flags=re.UNICODE).strip(" .")[:100] or "game"
        while True:
            target = parent / f"{safe}-{uuid4().hex[:8]}"
            if not target.exists():
                return target

    def publish(self, project, name, *, job_id=None, new_identity=False, engine_id=None):
        inspection = inspect_project(project, engine_id=engine_id)
        if inspection.engine_id is None or inspection.status == "conflict":
            raise ValueError(text("engine.unrecognized"))
        target = self._target(name, inspection.engine_id)
        game = self.catalog.prepare_project(
            project, name, target=target, new_identity=new_identity, engine_id=engine_id
        )
        if job_id:
            self.repository.update(job_id, reserved_project=asdict(game), phase="registering")
        # This service has one import worker. Never replace an existing destination.
        if target.exists():
            raise ValueError(text("errors.theDestinationFolderAlreadyExists"))
        self.catalog.rename_new(project, target)
        return self.record(game, job_id=job_id)

    @contextmanager
    def _staging(self, job_id):
        if job_id is None:
            with tempfile.TemporaryDirectory(
                dir=self.catalog.library_root, prefix=".import-"
            ) as temporary:
                yield Path(temporary)
            return
        # Persist a deterministic location before creating it, closing the otherwise
        # untracked mkdir/checkpoint crash window.
        staging = self.catalog.library_root / (".import-" + job_id)
        self.repository.update(job_id, staging=str(staging))
        staging.mkdir(mode=0o700)
        try:
            yield staging
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _copy_readonly(self, source: Path, *, job_id=None, phase=None, engine_id=None) -> Project:
        """Keep every save while preparing a writable copy on the publication filesystem."""
        inspection = inspect_project(source, engine_id=engine_id)
        if inspection.engine_id is None or inspection.status == "conflict":
            raise ValueError(text("engine.unrecognized"))
        size = 0
        for item in source.rglob("*"):
            if item.is_symlink():
                raise ValueError(
                    text("errors.theProjectContainsLinksPrepareACompleteFolderWithout")
                )
            if item.is_file():
                size += item.stat().st_size
        if shutil.disk_usage(self.catalog.library_root).free < size + 128 * 1024**2:
            raise ValueError(text("errors.notEnoughServerStorageForAWritableGameCopy"))
        with self._staging(job_id) as temporary:
            copied = temporary / "project"
            shutil.copytree(source, copied, symlinks=True)
            entries = [copied, *copied.rglob("*")]
            if any(item.is_symlink() for item in entries):
                raise ValueError(
                    text("errors.theProjectContainsLinksPrepareACompleteFolderWithout")
                )
            for item in entries:
                item.chmod(item.stat().st_mode | (0o700 if item.is_dir() else 0o600))
            if phase:
                phase("registering")
            return self.publish(
                copied, source.name, job_id=job_id, new_identity=True, engine_id=engine_id
            )

    def validate_source(self, source, *, uploaded=False):
        from vn_stream.projects.locations import check_busy

        source = Path(source).resolve()
        for game in self.catalog.list():
            game_path = Path(game.path)
            if (
                source == game_path
                or source.is_relative_to(game_path)
                or game_path.is_relative_to(source)
            ):
                check_busy(self.catalog, game.project_id)
        self.catalog.check_not_deleting(source)
        if not (uploaded and source.is_relative_to(self.catalog.data / "uploads/archives")):
            self.catalog.check_source_location(source)
        if source.is_relative_to(self.catalog.library_root / ".vn-stream-uploads"):
            raise ValueError(text("errors.uploadStagingFoldersCanOnlyBeImportedThroughFolder"))
        if not any(source.is_relative_to(root) for root in self.catalog.roots) and not (
            uploaded and source.is_relative_to(self.catalog.data / "uploads/archives")
        ):
            raise ValueError(text("errors.theSourceMustBeInsideAConfiguredNasImport"))
        if not source.exists():
            raise ValueError(text("errors.sourceNotFound"))
        return source

    def import_source(
        self,
        source: Path,
        *,
        password: str | None = None,
        engine_id: str | None = None,
        uploaded: bool = False,
        progress=None,
        phase=None,
        job_id=None,
    ) -> Project:
        source = self.validate_source(source, uploaded=uploaded)
        if source.is_dir():
            if os.access(source, os.W_OK):
                if phase:
                    phase("registering")
                inspection = inspect_project(source, engine_id=engine_id)
                self.catalog.check_classification(source, inspection.engine_id)
                return self.register(source, source.name, job_id=job_id, engine_id=engine_id)
            return self._copy_readonly(source, job_id=job_id, phase=phase, engine_id=engine_id)
        if not supports_archive(source):
            raise ValueError(text("errors.unsupportedArchiveFormat"))
        # Staging must share the publication filesystem for atomic directory rename.
        with self._staging(job_id) as temporary:
            staging = temporary / "extracted"
            extract_archive(
                source, staging, self.catalog.limits, password=password, progress=progress
            )
            if phase:
                phase("normalizing")
            project = normalize_project(
                staging, apk=source.suffix.lower() == ".apk", engine_id=engine_id
            )
            if (project / ".vn-stream").exists():
                raise ValueError(text("errors.theArchiveContainsAReservedManagementFolder"))
            if phase:
                phase("registering")
            name = next(
                source.name[: -len(suffix)]
                for suffix in sorted(SUPPORTED_EXTENSIONS, key=len, reverse=True)
                if source.name.lower().endswith(suffix)
            )
            return self.publish(project, name, job_id=job_id, engine_id=engine_id)


def import_project(catalog, source, **options):
    """Import a project without creating a background task."""
    return ImportPublication(catalog).import_source(source, **options)
