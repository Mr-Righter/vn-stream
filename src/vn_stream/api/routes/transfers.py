"""Authenticated root-confined browsing and bounded, resumable upload staging."""

import json
import os
import shutil
import threading
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from vn_stream.api.schemas.envelopes import Cancellation, Success
from vn_stream.api.schemas.projects import Project
from vn_stream.api.schemas.serialization import success
from vn_stream.api.schemas.transfers import ArchiveUpload, ImportSources
from vn_stream.engines.registry import inspect_project
from vn_stream.i18n import text
from vn_stream.imports.archive import supports_archive
from vn_stream.storage.async_io import filesystem_call, filesystem_resource

CHUNK = 8 * 1024**2


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)


class PasswordRequest(BaseModel):
    password: str | None = Field(default=None, max_length=1024, repr=False)


def transfer_routes(data: Path, roots: list[Path], import_game, *, jobs=None):
    router = APIRouter(prefix="/api/v2")
    staging = data / "uploads/archives"
    staging.mkdir(parents=True, exist_ok=True)
    # One transfer operation at a time keeps append, cancel and import atomic.
    guard = jobs.guard if jobs else threading.Lock()
    maximum = int(os.environ.get("VN_STREAM_UPLOAD_MAX_BYTES", 20 * 1024**3))

    def folder(uid):
        if len(uid) != 32 or any(c not in "0123456789abcdef" for c in uid):
            raise HTTPException(404, text("errors.uploadNotFound"))
        path = staging / uid
        if not (path / "meta.json").is_file():
            raise HTTPException(404, text("errors.uploadNotFound"))
        return path

    def status(uid):
        path = folder(uid)
        meta = json.loads((path / "meta.json").read_text())
        return {**meta, "id": uid, "offset": (path / "content").stat().st_size}

    @router.get(
        "/imports/sources", response_model=Success[ImportSources], response_model_exclude_unset=True
    )
    def browse(root: int | None = None, path: str = ""):
        root_list = [{"id": i, "name": p.name or str(p)} for i, p in enumerate(roots)]
        if root is None:
            return success({"roots": root_list, "entries": []})
        if root < 0 or root >= len(roots):
            raise HTTPException(400, text("errors.folderNotFound"))
        if "\x00" in path or len(path) > 4096:
            raise HTTPException(400, text("errors.invalidFolderPath"))
        base = roots[root].resolve()
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise HTTPException(400, text("errors.folderIsOutsideTheAllowedLocation"))
        target = base / relative
        if (
            any(part.startswith(".") for part in relative.parts)
            or target == data
            or target.is_relative_to(data)
        ):
            raise HTTPException(400, text("errors.folderIsOutsideTheAllowedLocation"))
        current = base
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise HTTPException(400, text("errors.linkedFoldersAreNotSupported"))
        if not target.resolve().is_relative_to(base):
            raise HTTPException(400, text("errors.folderIsOutsideTheAllowedLocation"))
        try:
            entries = []
            for item in target.iterdir():
                if item.name.startswith(".") or item.is_symlink():
                    continue
                directory = item.is_dir()
                if directory or supports_archive(item.name):
                    entries.append(
                        {
                            "name": item.name,
                            "path": str(item.relative_to(base)),
                            "directory": directory,
                            "project": directory and inspect_project(item).engine_id is not None,
                        }
                    )
                if len(entries) > 5000:
                    raise HTTPException(
                        400, text("errors.tooManyFolderEntriesChooseASmallerImportRoot")
                    )
        except OSError as error:
            raise HTTPException(400, text("errors.unableToReadTheFolder")) from error
        return success(
            {
                "roots": root_list,
                "root": root,
                "path": path,
                "source": str(target),
                "project": inspect_project(target).engine_id is not None,
                "entries": sorted(
                    entries, key=lambda e: (not e["directory"], e["name"].casefold())
                ),
            }
        )

    @router.post(
        "/uploads/archives",
        status_code=201,
        response_model=Success[ArchiveUpload],
        response_model_exclude_unset=True,
    )
    def create(body: UploadRequest):
        if (
            not supports_archive(body.filename)
            or "/" in body.filename
            or "\\" in body.filename
            or any(ord(char) < 32 for char in body.filename)
        ):
            raise HTTPException(400, text("errors.unsupportedFilenameOrFormat"))
        if body.size_bytes > maximum:
            raise HTTPException(413, text("errors.theUploadExceedsTheSizeLimit"))
        if not guard.acquire(blocking=False):
            raise HTTPException(409, text("errors.aFileIsBeingProcessedPleaseRetryLater"))
        try:
            for path in staging.iterdir():
                if (
                    path.is_dir()
                    and time.time() - path.stat().st_mtime > 86400
                    and not (jobs and jobs.bound(path.name))
                ):
                    shutil.rmtree(path)
            if len(list(staging.iterdir())) >= 3:
                raise HTTPException(409, text("errors.completeOrCancelTheExistingUploadFirst"))
            if shutil.disk_usage(staging).free < body.size_bytes + 128 * 1024**2:
                raise HTTPException(400, text("errors.notEnoughServerStorage"))
            uid = uuid4().hex
            path = staging / uid
            path.mkdir(mode=0o700)
            (path / "meta.json").write_text(
                json.dumps({"filename": body.filename, "size": body.size_bytes})
            )
            (path / "content").touch()
            return success(status(uid))
        finally:
            guard.release()

    @router.get(
        "/uploads/archives/{uid}",
        response_model=Success[ArchiveUpload],
        response_model_exclude_unset=True,
    )
    def inspect(uid: str):
        if not guard.acquire(blocking=False):
            raise HTTPException(409, text("errors.aFileIsBeingProcessedPleaseRetryLater"))
        try:
            return success(status(uid))
        finally:
            guard.release()

    @router.put(
        "/uploads/archives/{uid}",
        response_model=Success[ArchiveUpload],
        response_model_exclude_unset=True,
    )
    async def append(uid: str, request: Request, offset_bytes: int):
        offset = offset_bytes
        if not guard.acquire(blocking=False):
            raise HTTPException(409, text("errors.aFileIsBeingProcessedPleaseRetryLater"))
        try:
            record = await filesystem_call(status, uid)
            if jobs and await filesystem_call(jobs.bound, uid):
                raise HTTPException(409, text("errors.theUploadIsAlreadyLinkedToAnImportTask"))
            path = folder(uid) / "content"
            if offset != record["offset"]:
                raise HTTPException(409, text("errors.theUploadOffsetChangedPleaseRetry"))
            received = 0
            async with filesystem_resource(path.open, lambda file: file.close(), "ab") as writer:
                try:
                    async for chunk in request.stream():
                        received += len(chunk)
                        if received > CHUNK or offset + received > record["size"]:
                            raise HTTPException(413, text("errors.uploadDataExceedsTheLimit"))
                        await filesystem_call(writer.write, chunk)
                except BaseException:
                    await filesystem_call(writer.truncate, offset)
                    raise
                await filesystem_call(writer.flush)
                await filesystem_call(os.fsync, writer.fileno())
            await filesystem_call(os.utime, path.parent, None)
            return success(await filesystem_call(status, uid))
        finally:
            guard.release()

    @router.delete(
        "/uploads/archives/{uid}",
        response_model=Success[Cancellation],
        response_model_exclude_unset=True,
    )
    def cancel(uid: str):
        if not guard.acquire(blocking=False):
            raise HTTPException(409, text("errors.aFileIsBeingProcessedPleaseRetryLater"))
        try:
            if jobs and jobs.bound(uid):
                raise HTTPException(409, text("errors.abandonTheFailedImportTaskFirst"))
            shutil.rmtree(folder(uid))
            return success({"cancelled": True})
        finally:
            guard.release()

    @router.post(
        "/uploads/archives/{uid}/import",
        status_code=201,
        response_model=Success[Project],
        response_model_exclude_unset=True,
    )
    def finish(uid: str, body: PasswordRequest):
        if not guard.acquire(blocking=False):
            raise HTTPException(409, text("errors.aFileIsBeingProcessedPleaseRetryLater"))
        try:
            record = status(uid)
            path = folder(uid)
            if record["offset"] != record["size"]:
                raise HTTPException(409, text("errors.theUploadIsNotComplete"))
            archive = path / record["filename"]
            # Keep content for password retry; linking avoids another large copy.
            if not archive.exists():
                os.link(path / "content", archive)
        finally:
            guard.release()
        return success(import_game(archive, body.password, uploaded=True))

    return router
