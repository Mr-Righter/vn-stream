"""Read-side project summaries, independent of HTTP and background workers."""

from dataclasses import asdict, replace
from pathlib import Path

from vn_stream.engines.contracts import Inspection
from vn_stream.engines.registry import (
    ADAPTERS,
    capabilities,
    describe_engine,
    inspect_project,
    project_runtimes,
)
from vn_stream.projects.metadata import game_info
from vn_stream.runtimes.catalog import available_runtimes
from vn_stream.sessions.history import summary


class ProjectQueries:
    def __init__(self, catalog, deletions):
        self.catalog = catalog
        self.deletions = deletions
        self.data = catalog.data

    def describe_project(self, game, *, refresh=False, minimal=False, installed=None):
        project = Path(game.path)
        inspection = (
            Inspection(game.engine_id, "import_record", engine_version=game.engine_version)
            if minimal
            else inspect_project(project, engine_id=game.engine_id)
        )
        if (
            inspection.engine_version is None
            and game.engine_version
            and inspection.status == "unknown"
        ):
            inspection = replace(
                inspection, engine_version=game.engine_version, status="import_record"
            )
        info = (
            {"release_version": None, "size_bytes": None}
            if minimal
            else game_info(
                project,
                self.data / "cache/projects" / f"{game.project_id}.json",
                refresh=refresh,
                engine_id=game.engine_id,
            )
        )
        return_info = {
            key: value
            for key, value in info.items()
            if key not in ("icon", "display_name", "schema", "icon_mime", "icon_revision")
        }
        installed = (
            installed
            if installed is not None
            else available_runtimes(self.data / "runtimes", ADAPTERS)
        )
        recommendation, runtime_options = project_runtimes(project, inspection, installed)
        engine = describe_engine(game.engine_id)

        return {
            **asdict(game),
            **return_info,
            **summary(self.catalog.paths.session, game.project_id),
            "name": info.get("display_name") or game.name,
            "engine_display_name": engine["display_name"],
            "engine_name_key": engine["engine_name_key"],
            "icon_url": (
                f"/api/v2/projects/{game.project_id}/icon?v={info.get('icon_revision', '')}"
                if info.get("icon")
                else None
            ),
            "recommendation": recommendation,
            "runtime_options": runtime_options,
            "capabilities": {**capabilities(game.engine_id), **inspection.capabilities},
            "recognition": {
                "status": inspection.status,
                "evidence": inspection.evidence,
                "warnings": inspection.warnings,
            },
            "runtime_id": self.catalog.runtime_id(game.project_id),
            "engine_version": inspection.engine_version,
            "runtime_version": self.catalog.runtime_version(game.project_id),
            "runtime_override": self.catalog.runtime_override(game.project_id),
            "available": project.is_dir() and inspection.engine_id == game.engine_id,
            "deletion": next(
                (
                    r
                    for r in self.deletions.rows()
                    if r["project_id"] == game.project_id and r["state"] != "completed"
                ),
                None,
            )
            if self.deletions
            else None,
        }
