"""Authenticated resource and save endpoints; all paths are server-owned identifiers."""

import json
from functools import wraps
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr
from starlette.concurrency import run_in_threadpool

from vn_stream.api.responses import resource_response
from vn_stream.api.schemas.content import (
    ArchivedSave,
    AssetDetail,
    AssetEntries,
    AssetPreparation,
    AssetSummary,
    AssetTask,
    MigrationReview,
    MigrationSource,
    MigrationTask,
    PlainText,
    PreviewState,
    SaveBackup,
    SaveEntry,
    SaveListing,
    SaveRevision,
    SaveUpload,
    SaveVariables,
    SigningState,
    ThumbnailState,
)
from vn_stream.api.schemas.envelopes import Paginated, Success
from vn_stream.api.schemas.serialization import paginated, success, task_progress
from vn_stream.i18n import MessageText, text
from vn_stream.projects.locations import require_capability
from vn_stream.saves.renpy.pickle_codec import MAX_FILE


class Extract(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    mode: Literal["index", "migrate"] = "index"


class Prepare(BaseModel):
    representation: Literal["original", "preview"] = "original"


class SaveChange(BaseModel):
    id: str
    type: Literal["boolean", "text", "number"]
    value: StrictStr | StrictBool


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str = Field(min_length=64, max_length=64)
    changes: list[SaveChange] = Field(min_length=1, max_length=10000)


class BackupRequest(BaseModel):
    revision: str = Field(min_length=64, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)


class MigrationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=128)
    include_persistent: bool = True


class MigrationCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=32, max_length=32)
    request_id: str = Field(min_length=1, max_length=128)
    skip_invalid: bool = False


class MigrationUndo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=32, max_length=32)
    request_id: str = Field(min_length=1, max_length=128)


class Revision(BaseModel):
    revision: str | None = None


def checked(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, text("content.notFound")) from None
        except (ValueError, OSError) as error:
            detail = (
                error.args[0]
                if error.args and isinstance(error.args[0], MessageText)
                else text("content.operationFailed")
            )
            raise HTTPException(409, detail) from None

    return call


def content_routes(library, sessions, imports, *, assets, saves):
    def require(project_id, name):
        try:
            require_capability(library, project_id, name)
        except KeyError:
            raise HTTPException(404, text("content.notFound")) from None
        except ValueError:
            raise HTTPException(409, text("engine.unsupported")) from None

    def capability_guard(request: Request):
        project_id = request.path_params.get("project_id")
        if project_id:
            require(project_id, "assets" if "/assets" in request.url.path else "save_management")

    def migration_target(uid):
        task = saves.migrations.get(uid)
        require(task["target_id"], "save_migration")
        return task

    def asset_task(value):
        return {**value, "progress_percent": task_progress(value)}

    router = APIRouter(prefix="/api/v2", dependencies=[Depends(capability_guard)])

    @router.get(
        "/projects/{project_id}/assets",
        response_model=Success[AssetSummary],
        response_model_exclude_unset=True,
    )
    @checked
    def overview(project_id: str):
        value = assets.summary(project_id)
        return success({**value, "job": asset_task(value["job"]) if value["job"] else None})

    @router.post(
        "/projects/{project_id}/assets/extract",
        status_code=202,
        response_model=Success[AssetTask],
        response_model_exclude_unset=True,
    )
    @checked
    def extract(project_id: str, body: Extract):
        return success(asset_task(assets.extract(project_id, body.request_id, body.mode)))

    @router.get(
        "/tasks/assets/{uid}", response_model=Success[AssetTask], response_model_exclude_unset=True
    )
    @checked
    def job(uid: str):
        return success(asset_task(assets.job(uid)))

    @router.post(
        "/tasks/assets/{uid}/cancel",
        response_model=Success[AssetTask],
        response_model_exclude_unset=True,
    )
    @checked
    def cancel(uid: str):
        return success(asset_task(assets.cancel(uid)))

    @router.post(
        "/tasks/assets/{uid}/retry",
        status_code=202,
        response_model=Success[AssetTask],
        response_model_exclude_unset=True,
    )
    @checked
    def retry(uid: str):
        return success(asset_task(assets.retry(uid)))

    @router.get(
        "/projects/{project_id}/assets/entries",
        response_model=Paginated[AssetEntries],
        response_model_exclude_unset=True,
    )
    @checked
    def entries(
        project_id: str,
        category: str = "",
        folder: str = Query("", max_length=4096),
        search: str = Query("", max_length=512),
        page: int = Query(1, ge=1),
    ):
        if category and category not in ("image", "audio", "video", "script", "other"):
            raise ValueError
        result = assets.list(project_id, category=category, folder=folder, search=search, page=page)
        return paginated(
            {"entries": result["entries"]}, page=page, page_size=100, total=result["total"]
        )

    @router.get(
        "/projects/{project_id}/assets/{uid}",
        response_model=Success[AssetDetail],
        response_model_exclude_unset=True,
    )
    @checked
    def asset_detail(
        project_id: str,
        uid: str,
        category: str = "",
        folder: str = Query("", max_length=4096),
        search: str = Query("", max_length=512),
    ):
        return success(
            assets.detail(project_id, uid, category=category, folder=folder, search=search)
        )

    @router.get("/projects/{project_id}/assets/{uid}/content", response_class=FileResponse)
    @checked
    def file(project_id: str, uid: str, request: Request, download: bool = False):
        _, entry = assets.entry(project_id, uid)
        mime = (
            entry["mime"]
            if entry["kind"] in ("image", "audio", "video")
            else "application/octet-stream"
        )
        return resource_response(
            assets.file(project_id, uid),
            request,
            mime,
            entry["name"] if download or entry["kind"] not in ("image", "audio", "video") else None,
        )

    @router.post(
        "/projects/{project_id}/assets/{uid}/prepare",
        status_code=202,
        response_model=Success[AssetPreparation],
        response_model_exclude_unset=True,
    )
    @checked
    def prepare_asset(project_id: str, uid: str, body: Prepare):
        return success(assets.prepare(project_id, uid, body.representation))

    @router.get(
        "/projects/{project_id}/assets/{uid}/prepare",
        response_model=Success[AssetPreparation],
        response_model_exclude_unset=True,
    )
    @checked
    def preparation_status(
        project_id: str, uid: str, representation: Literal["original", "preview"] = "original"
    ):
        return success(assets.prepare_status(project_id, uid, representation))

    @router.get("/projects/{project_id}/assets/{uid}/prepared-content", response_class=FileResponse)
    @checked
    def prepared_content(
        project_id: str,
        uid: str,
        request: Request,
        representation: Literal["original", "preview"] = "original",
        download: bool = False,
    ):
        _, entry = assets.entry(project_id, uid)
        mime = (
            "image/png"
            if representation == "preview" and entry["mime"] == "image/x-tlg"
            else entry["mime"]
        )
        return resource_response(
            assets.prepared_file(project_id, uid, representation),
            request,
            mime,
            entry["name"] if download else None,
        )

    @router.get(
        "/projects/{project_id}/assets/{uid}/thumbnail",
        response_class=FileResponse,
        responses={200: {"content": {"image/jpeg": {}}}, 202: {"model": Success[ThumbnailState]}},
    )
    @checked
    def thumbnail(project_id: str, uid: str):
        value = assets.thumbnail(project_id, uid)
        if isinstance(value, dict):
            if value["state"] == "failed":
                raise HTTPException(422, text("assets.previewUnavailable"))
            return JSONResponse(
                success(value),
                status_code=202,
                headers={"Retry-After": "1", "Cache-Control": "no-store"},
            )
        return FileResponse(
            value, media_type="image/jpeg", headers={"Cache-Control": "private, no-cache"}
        )

    @router.post(
        "/projects/{project_id}/assets/{uid}/preview",
        status_code=202,
        response_model=Success[PreviewState],
        response_model_exclude_unset=True,
    )
    @checked
    def preview(project_id: str, uid: str):
        return success(assets.preview(project_id, uid))

    @router.get(
        "/projects/{project_id}/assets/{uid}/preview",
        response_model=Success[PreviewState],
        response_model_exclude_unset=True,
    )
    @checked
    def preview_status(project_id: str, uid: str):
        return success(assets.preview_status(project_id, uid))

    @router.get("/projects/{project_id}/assets/{uid}/preview/content", response_class=FileResponse)
    @checked
    def preview_content(project_id: str, uid: str):
        return FileResponse(assets.preview_file(project_id, uid))

    @router.get(
        "/projects/{project_id}/assets/{uid}/text",
        response_model=Success[PlainText],
        response_model_exclude_unset=True,
    )
    @checked
    def plain_text(project_id: str, uid: str):
        path = assets.file(project_id, uid)
        if path.size > 1024**2 or Path(
            assets.entry(project_id, uid)[1]["name"]
        ).suffix.lower() not in (
            ".rpy",
            ".py",
            ".txt",
            ".json",
            ".md",
            ".csv",
        ):
            raise ValueError
        return success({"text": path.read_bytes(limit=1024**2).decode("utf-8", errors="replace")})

    @router.get(
        "/projects/{project_id}/saves",
        response_model=Success[SaveListing],
        response_model_exclude_unset=True,
    )
    @checked
    def list_saves(project_id: str):
        return success(saves.list(project_id))

    @router.post(
        "/projects/{project_id}/saves/upload",
        response_model=Success[SaveUpload],
        response_model_exclude_unset=True,
    )
    async def upload(
        project_id: str,
        request: Request,
        name: str = Query(max_length=200),
        revision: str | None = None,
    ):
        data = bytearray()
        async for piece in request.stream():
            data.extend(piece)
            if len(data) > MAX_FILE:
                raise HTTPException(413, text("saves.tooLarge"))
        return success(
            await run_in_threadpool(checked(saves.upload), project_id, name, bytes(data), revision)
        )

    @router.get(
        "/projects/{project_id}/saves/signing",
        response_model=Success[SigningState],
        response_model_exclude_unset=True,
    )
    @checked
    def signing_status(project_id: str):
        require(project_id, "save_editing")
        return success(saves.signing_jobs.status(project_id))

    @router.post(
        "/projects/{project_id}/saves/signing/retry",
        status_code=202,
        response_model=Success[SigningState],
        response_model_exclude_unset=True,
    )
    @checked
    def signing_retry(project_id: str):
        require(project_id, "save_editing")
        return success(saves.signing_jobs.retry(project_id))

    @router.get(
        "/projects/{project_id}/saves/migration-sources",
        response_model=Success[list[MigrationSource]],
        response_model_exclude_unset=True,
    )
    @checked
    def migration_sources(project_id: str):
        require(project_id, "save_migration")
        return success(saves.migrations.sources(project_id))

    @router.post(
        "/projects/{project_id}/saves/migrations/preview",
        response_model=Success[MigrationReview],
        response_model_exclude_unset=True,
    )
    @checked
    def migration_preview(project_id: str, body: MigrationPreview):
        require(project_id, "save_migration")
        return success(
            saves.migrations.preview(project_id, body.source_id, body.include_persistent)
        )

    @router.post(
        "/projects/{project_id}/saves/migrations",
        status_code=202,
        response_model=Success[MigrationTask],
        response_model_exclude_unset=True,
    )
    @checked
    def migration_commit(project_id: str, body: MigrationCommit):
        require(project_id, "save_migration")
        return success(
            saves.migrations.commit(project_id, body.preview_id, body.request_id, body.skip_invalid)
        )

    @router.get(
        "/projects/{project_id}/saves/migrations",
        response_model=Success[list[MigrationTask]],
        response_model_exclude_unset=True,
    )
    @checked
    def migration_history(project_id: str):
        require(project_id, "save_migration")
        return success(saves.migrations.history(project_id))

    @router.get(
        "/tasks/save-migrations/{uid}",
        response_model=Success[MigrationTask],
        response_model_exclude_unset=True,
    )
    @checked
    def migration_status(uid: str):
        return success(migration_target(uid))

    @router.post(
        "/tasks/save-migrations/{uid}/undo-preview",
        response_model=Success[MigrationReview],
        response_model_exclude_unset=True,
    )
    @checked
    def migration_undo_preview(uid: str):
        migration_target(uid)
        return success(saves.migrations.undo_preview(uid))

    @router.post(
        "/tasks/save-migrations/{uid}/undo",
        status_code=202,
        response_model=Success[MigrationTask],
        response_model_exclude_unset=True,
    )
    @checked
    def migration_undo(uid: str, body: MigrationUndo):
        migration_target(uid)
        return success(saves.migrations.undo(uid, body.preview_id, body.request_id))

    @router.get(
        "/projects/{project_id}/saves/{uid}",
        response_model=Success[SaveEntry | ArchivedSave],
        response_model_exclude_unset=True,
    )
    @checked
    def save_detail(project_id: str, uid: str):
        return success(saves.detail(project_id, uid))

    @router.get(
        "/projects/{project_id}/saves/{uid}/variables",
        response_model=Paginated[SaveVariables],
        response_model_exclude_unset=True,
    )
    @checked
    def variables(
        project_id: str,
        uid: str,
        search: str = Query("", max_length=512),
        kind: str = "",
        page: int = Query(1, ge=1),
    ):
        try:
            require(project_id, "save_editing")
        except HTTPException as error:
            if error.status_code != 409:
                raise
            _, paths = saves.resolve_save(project_id, uid)
            return paginated(
                dict(
                    fields=[],
                    revision=saves.revision(paths),
                    editable=False,
                    writable=saves.writable(project_id),
                    counts={},
                    reason="engine.unsupported",
                ),
                page=page,
                page_size=100,
                total=0,
            )
        value = saves.variables(project_id, uid)
        fields = [
            f
            for f in value["fields"]
            if (not search or search.casefold() in f["name"].casefold())
            and (not kind or kind == f["type"])
        ]
        return paginated(
            dict(value, fields=fields[(page - 1) * 100 : page * 100]),
            page=page,
            page_size=100,
            total=len(fields),
        )

    @router.patch(
        "/projects/{project_id}/saves/{uid}/variables",
        response_model=Success[SaveRevision],
        response_model_exclude_unset=True,
    )
    @checked
    def edit(project_id: str, uid: str, body: Edit):
        require(project_id, "save_editing")
        changes = [change.model_dump() for change in body.changes]
        return success(saves.update(project_id, uid, body.revision, changes))

    @router.delete(
        "/projects/{project_id}/saves/{uid}",
        response_model=Success[SaveBackup],
        response_model_exclude_unset=True,
    )
    @checked
    def delete(project_id: str, uid: str, body: Revision):
        return success(saves.delete(project_id, uid, body.revision))

    @router.get("/projects/{project_id}/saves/{uid}/content", response_class=FileResponse)
    @checked
    def download(project_id: str, uid: str):
        name, paths = saves.resolve_save(project_id, uid)
        return FileResponse(paths[0], media_type="application/octet-stream", filename=name)

    @router.get(
        "/projects/{project_id}/saves/{uid}/screenshot",
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}},
    )
    @checked
    def screenshot(project_id: str, uid: str):
        return Response(saves.screenshot(project_id, uid), media_type="image/png")

    @router.post(
        "/projects/{project_id}/saves/{uid}/backups",
        status_code=201,
        response_model=Success[SaveBackup],
        response_model_exclude_unset=True,
    )
    @checked
    def manual_backup(project_id: str, uid: str, body: BackupRequest):
        record = saves.backup(project_id, uid, body.revision, body.request_id)
        return success({k: record[k] for k in ("id", "kind", "created_at", "state")})

    @router.get(
        "/projects/{project_id}/saves/{uid}/backups",
        response_model=Success[list[SaveBackup]],
        response_model_exclude_unset=True,
    )
    @checked
    def backups(project_id: str, uid: str):
        return success(saves.backups(project_id, uid))

    @router.post(
        "/projects/{project_id}/saves/{uid}/backups/{backup_id}/restore",
        response_model=Success[SaveBackup],
        response_model_exclude_unset=True,
    )
    @checked
    def restore(project_id: str, uid: str, backup_id: str, body: Revision):
        return success(saves.restore(project_id, uid, backup_id, body.revision))

    @router.get(
        "/projects/{project_id}/saves/{uid}/backups/{backup_id}/content",
        response_class=FileResponse,
    )
    @checked
    def backup_content(project_id: str, uid: str, backup_id: str):
        path = saves.backup_file(project_id, uid, backup_id)
        record = json.loads((path.parent / "record.json").read_text())
        return FileResponse(path, filename=Path(record["entries"][0]["path"]).name)

    return router
