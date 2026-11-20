"""Start one project while keeping maintenance, selection and lease order local."""

from dataclasses import replace
from pathlib import Path

from vn_stream.engines.registry import ADAPTERS, inspect_project, prepare_launch, project_runtimes
from vn_stream.i18n import text
from vn_stream.projects.locations import check_busy
from vn_stream.runtimes.catalog import available_runtimes


class ProjectStartError(ValueError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class GameLauncher:
    def __init__(self, catalog, sessions, runtime_root: Path):
        self.catalog = catalog
        self.sessions = sessions
        self.runtime_root = runtime_root

    def start_game(self, project_id: str, runtime_id: str | None = None) -> dict:
        with self.catalog.lock:
            record = self.catalog.get(project_id)
            project = Path(record.path)
            self.catalog.check_not_deleting(project)
            check_busy(self.catalog, project_id, include_imports=True)

            def prepare():
                inspection = inspect_project(project, engine_id=record.engine_id)
                if inspection.engine_id != record.engine_id:
                    raise ProjectStartError(409, text("engine.unrecognized"))
                if inspection.status == "unknown" and record.engine_version:
                    inspection = replace(
                        inspection, engine_version=record.engine_version, status="import_record"
                    )
                installed = available_runtimes(self.runtime_root, ADAPTERS)
                selected_id = runtime_id
                if not selected_id:
                    recommendation, _ = project_runtimes(project, inspection, installed)
                    selected_id = recommendation["runtime_id"]
                selected = next(
                    (
                        r
                        for r in installed
                        if r["id"] == selected_id and r["engine_id"] == record.engine_id
                    ),
                    None,
                )
                if selected is None:
                    raise ProjectStartError(409, text("engine.runtimeUnavailable"))
                launch = prepare_launch(
                    project, inspection, selected, allow_experimental=bool(runtime_id)
                )
                self.catalog.select_runtime(
                    record.project_id,
                    selected["version"],
                    override=runtime_id,
                    runtime_id=selected["id"],
                )
                return launch

            return self.sessions.start(record.project_id, [], project, {}, prepare=prepare)
