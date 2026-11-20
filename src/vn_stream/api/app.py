"""Authenticated control API; media transport remains a separate adapter."""

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from vn_stream.api.errors import install_error_handlers
from vn_stream.api.middleware import RequestContextMiddleware
from vn_stream.api.routes.authentication import install_auth
from vn_stream.api.routes.directory_uploads import directory_upload_routes
from vn_stream.api.routes.imports import import_job_routes
from vn_stream.api.routes.player import player_routes
from vn_stream.api.routes.transfers import transfer_routes
from vn_stream.api.routes.web_app import web_app_routes
from vn_stream.api.schemas.envelopes import Failure, Success
from vn_stream.api.schemas.projects import (
    DeletionTask,
    Engine,
    ImportRequest,
    Project,
    Runtime,
    RuntimeRelease,
    Session,
    StartRequest,
    Stream,
)
from vn_stream.api.schemas.serialization import success
from vn_stream.assets.archive_index import image_type
from vn_stream.engines.registry import ADAPTERS
from vn_stream.i18n import text
from vn_stream.projects.metadata import game_info, safe_asset
from vn_stream.runtimes.catalog import (
    available_runtimes,
)
from vn_stream.runtimes.releases import describe_releases
from vn_stream.streaming.connection import describe_stream


def create_app(
    data: Path,
    roots: list[Path],
    password: str,
    *,
    username: str = "player",
    web: Path | None = None,
    stream_path: str | None = None,
    library_root: Path | None = None,
    discovery_roots: list[Path] | None = None,
    public_prefix: str = "",
    allow_insecure_local: bool = False,
) -> FastAPI:
    if not password or not username or ":" in username:
        raise ValueError(text("errors.configureAValidUsernameAndVnStreamPassword"))
    from vn_stream.application.bootstrap import build_services
    from vn_stream.application.lifecycle import lifespan_for

    services = build_services(
        data, roots, library_root=library_root, discovery_roots=discovery_roots
    )
    library, sessions = services.catalog, services.sessions
    jobs, deletions = services.imports, services.deletions
    assets, saves = services.assets, services.saves
    connection = describe_stream(stream_path)
    lifespan = lifespan_for(services)

    app = FastAPI(
        title="VN Stream",
        version="2",
        lifespan=lifespan,
        docs_url=None,
        openapi_url=None,
        responses={
            code: {"model": Failure} for code in (400, 401, 403, 404, 409, 413, 415, 422, 500)
        },
    )

    install_error_handlers(app)

    install_auth(
        app,
        data / "database",
        username,
        password,
        prefix=public_prefix,
        allow_insecure_local=allow_insecure_local,
        web=web,
    )

    app.add_middleware(RequestContextMiddleware)

    app.include_router(web_app_routes(public_prefix))
    app.include_router(player_routes(app, sessions, library.paths.session))

    describe_game = services.queries.describe_project

    @app.get(
        "/api/v2/projects", response_model=Success[list[Project]], response_model_exclude_unset=True
    )
    def games():
        installed = available_runtimes(data / "runtimes", ADAPTERS)
        return success([describe_game(game, installed=installed) for game in library.list()])

    @app.post(
        "/api/v2/projects/refresh",
        response_model=Success[list[Project]],
        response_model_exclude_unset=True,
    )
    def refresh_games(response: Response):
        try:
            with jobs.operation():
                found = library.discover()
        except ValueError as error:
            raise HTTPException(
                409, error.args[0] if error.args else text("common.requestFailed")
            ) from None
        response.headers["X-Discovery-Added"] = str(found["added"])
        response.headers["X-Discovery-Warnings"] = str(found["warnings"])
        response.headers["X-Discovery-Truncated"] = str(found["truncated"]).lower()
        installed = available_runtimes(data / "runtimes", ADAPTERS)
        return success(
            [describe_game(game, refresh=True, installed=installed) for game in library.list()]
        )

    def find_game(project_id):
        try:
            return library.get(project_id)
        except KeyError as error:
            raise HTTPException(404, text("errors.gameNotFound")) from error

    @app.get(
        "/api/v2/projects/{project_id}",
        response_model=Success[Project],
        response_model_exclude_unset=True,
    )
    def game_details(project_id: str):
        return success(describe_game(find_game(project_id)))

    @app.get("/api/v2/projects/{project_id}/icon", response_class=FileResponse)
    def icon(project_id: str):
        game = find_game(project_id)
        with library.lock:
            info = game_info(
                Path(game.path),
                data / "cache/projects" / f"{game.project_id}.json",
                engine_id=game.engine_id,
            )
        asset = (
            data / "cache/projects" / f"{game.project_id}.icon"
            if info.get("icon") == "@asset"
            else data / "cache/projects" / f"{game.project_id}.ico"
            if info.get("icon") == "@launcher"
            else safe_asset(Path(game.path), info["icon"])
            if info.get("icon")
            else None
        )
        if asset is None or not asset.is_file():
            raise HTTPException(404, text("errors.iconUnavailable"))
        with asset.open("rb") as file:
            media_type = image_type(file.read(16))
        if media_type is None:
            raise HTTPException(404, text("errors.iconUnavailable"))
        return FileResponse(
            asset, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"}
        )

    @app.post(
        "/api/v2/projects",
        status_code=201,
        response_model=Success[Project],
        response_model_exclude_unset=True,
    )
    def import_game(request: ImportRequest):
        return success(
            perform_import(Path(request.path), request.password, engine_id=request.engine_id)
        )

    app.state.import_jobs = jobs

    def deletion_call(action, *args):
        try:
            return action(*args)
        except KeyError:
            raise HTTPException(404, text("errors.gameOrDeletionTaskNotFound")) from None
        except (ValueError, OSError) as error:
            raise HTTPException(
                409, error.args[0] if error.args else text("common.requestFailed")
            ) from None

    @app.delete(
        "/api/v2/projects/{project_id}",
        status_code=202,
        response_model=Success[DeletionTask],
        response_model_exclude_unset=True,
    )
    def delete_game(project_id: str):
        return success(deletion_call(deletions.create, project_id))

    @app.get(
        "/api/v2/tasks/project-deletions/{uid}",
        response_model=Success[DeletionTask],
        response_model_exclude_unset=True,
    )
    def deletion_status(uid: str):
        return success(deletion_call(deletions.get, uid))

    @app.post(
        "/api/v2/tasks/project-deletions/{uid}/retry",
        status_code=202,
        response_model=Success[DeletionTask],
        response_model_exclude_unset=True,
    )
    def retry_deletion(uid: str):
        return success(deletion_call(deletions.retry, uid))

    from vn_stream.api.routes.content import content_routes

    router = content_routes(library, sessions, jobs, assets=assets, saves=saves)
    app.include_router(router)
    app.state.assets, app.state.saves = assets, saves
    app.state.deletions = deletions
    app.state.services = services

    app.include_router(import_job_routes(jobs))
    app.include_router(directory_upload_routes(jobs))

    def perform_import(path, password, *, uploaded=False, engine_id=None):
        try:
            source = {"upload_id": path.parent.name} if uploaded else {"path": str(path)}
            job = jobs.create(uuid4().hex, password=password, engine_id=engine_id, **source)
            if job["state"] == "failed":
                job = jobs.retry(job["id"], password=password)
            job = jobs.wait(job["id"])
            if job["state"] != "completed":
                raise ValueError(job["error"] or text("errors.importFailed"))
            return job["project"]
        except (ValueError, OSError) as error:
            raise HTTPException(
                400, error.args[0] if error.args else text("common.requestFailed")
            ) from error

    app.include_router(transfer_routes(data, library.roots, perform_import, jobs=jobs))

    @app.get(
        "/api/v2/engines", response_model=Success[list[Engine]], response_model_exclude_unset=True
    )
    def engines():
        from vn_stream.engines.registry import describe_engines

        return success(describe_engines())

    @app.get(
        "/api/v2/runtimes", response_model=Success[list[Runtime]], response_model_exclude_unset=True
    )
    def runtimes():
        return success(available_runtimes(data / "runtimes", ADAPTERS))

    @app.get(
        "/api/v2/runtimes/releases",
        response_model=Success[list[RuntimeRelease]],
        response_model_exclude_unset=True,
    )
    def runtime_releases():
        return success(describe_releases(data / "runtimes", ADAPTERS))

    @app.get("/api/v2/stream", response_model=Success[Stream], response_model_exclude_unset=True)
    def stream():
        return success(connection)

    @app.get("/api/v2/session", response_model=Success[Session], response_model_exclude_unset=True)
    def session():
        return success(sessions.status())

    from vn_stream.application.game_launcher import ProjectStartError

    launcher = services.launcher

    @app.post(
        "/api/v2/projects/{project_id}/start",
        response_model=Success[Session],
        response_model_exclude_unset=True,
    )
    def start(project_id: str, request: StartRequest):
        try:
            return success(launcher.start_game(project_id, request.runtime_id))
        except KeyError:
            raise HTTPException(404, text("errors.gameNotFound")) from None
        except ProjectStartError as error:
            raise HTTPException(error.status_code, error.args[0]) from error
        except ValueError as error:
            raise HTTPException(
                409, error.args[0] if error.args else text("common.requestFailed")
            ) from error
        except OSError as error:
            logging.getLogger(__name__).error(
                "Project start failed: exception=%s errno=%s", type(error).__name__, error.errno
            )
            raise HTTPException(400, text("game.startFailed")) from error

    @app.post(
        "/api/v2/session/stop", response_model=Success[Session], response_model_exclude_unset=True
    )
    def stop():
        return success(sessions.stop())

    if web is not None and web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app
