"""Project locations, capability checks and durable maintenance declarations."""

from pathlib import Path

from vn_stream.i18n import text


def initialize(library):
    with library.db.transaction() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS content_operations "
            "(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, "
            "state TEXT NOT NULL, record TEXT NOT NULL)"
        )

    def active(project_id):
        with library.db.transaction() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT id FROM content_operations WHERE project_id=? AND state IN ('running','recovering')",
                    (project_id,),
                )
            ]

    library.maintenance.register("operations", active)


def check_busy(library, project_id, **owners):
    library.maintenance.check(project_id, **owners)


def project(library, project_id):
    game = library.get(project_id)
    path = Path(game.path)
    if not path.is_dir() or any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError(text("content.unavailable"))
    library.check_not_deleting(path)
    return path


def require_capability(library, project_id, capability):
    from vn_stream.engines.registry import capabilities

    if not capabilities(library.get(project_id).engine_id).get(capability):
        raise ValueError(text("engine.unsupported"))
