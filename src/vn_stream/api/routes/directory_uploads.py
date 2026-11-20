"""Authenticated by the application middleware; no host paths in upload responses."""

import os
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from vn_stream.api.schemas.envelopes import Cancellation, Success
from vn_stream.api.schemas.serialization import success
from vn_stream.api.schemas.transfers import DirectoryFile, DirectoryFiles, DirectoryUpload
from vn_stream.i18n import text
from vn_stream.imports.directory_uploads import CHUNK, DirectoryUploadError
from vn_stream.storage.async_io import filesystem_call, filesystem_resource


class DirectoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=255)
    file_count: int = Field(ge=1, le=100000)
    total_bytes: int = Field(ge=0)


class ManifestFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=4096)
    size_bytes: int = Field(ge=0)


class ManifestBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    batch_id: str = Field(min_length=1, max_length=200)
    files: list[ManifestFile] = Field(min_length=1, max_length=1000)


def directory_upload_routes(jobs):
    router = APIRouter(prefix="/api/v2/uploads/directories")
    uploads = jobs.directories

    @contextmanager
    def operation():
        try:
            with jobs.operation():
                yield
        except DirectoryUploadError as error:
            raise HTTPException(
                error.status, error.args[0] if error.args else text("common.requestFailed")
            ) from None
        except KeyError:
            raise HTTPException(404, text("errors.folderUploadOrFileNotFound")) from None
        except ValueError:
            raise HTTPException(409, text("errors.aFileIsBeingProcessedPleaseRetryLater")) from None
        except OSError:
            raise HTTPException(400, text("errors.unableToReadOrWriteTheFolderUpload")) from None

    @router.post("", status_code=201, response_model=Success[DirectoryUpload])
    def create(body: DirectoryRequest):
        with operation():
            return success(uploads.create(**body.model_dump()))

    @router.get("/active", response_model=Success[DirectoryUpload | None])
    def active():
        with operation():
            return success(uploads.active())

    @router.get("/{uid}", response_model=Success[DirectoryUpload])
    def status(
        uid: str,
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=1000, ge=1, le=1000),
    ):
        with operation():
            return success(uploads.status(uid, cursor, limit))

    @router.post("/{uid}/manifest", response_model=Success[DirectoryFiles])
    def manifest(uid: str, body: ManifestBatch):
        with operation():
            return success(
                uploads.manifest(
                    uid,
                    body.batch_id,
                    [{"path": file.path, "size": file.size_bytes} for file in body.files],
                )
            )

    @router.post("/{uid}/seal", response_model=Success[DirectoryUpload])
    def seal(uid: str):
        with operation():
            return success(uploads.seal(uid))

    @router.put("/{uid}/files/{file_id}", response_model=Success[DirectoryFile])
    async def append(uid: str, file_id: str, request: Request, offset_bytes: int = Query(ge=0)):
        offset = offset_bytes
        with operation():
            async with filesystem_resource(
                uploads.open_append, lambda pair: pair[1].close(), uid, file_id, offset
            ) as (entry, writer):
                received = 0
                try:
                    async for chunk in request.stream():
                        received += len(chunk)
                        if received > CHUNK or offset + received > entry["size"]:
                            raise DirectoryUploadError(
                                413, text("errors.uploadDataExceedsTheLimit")
                            )
                        await filesystem_call(writer.write, chunk)
                    await filesystem_call(writer.flush)
                    await filesystem_call(os.fsync, writer.fileno())
                except BaseException:
                    await filesystem_call(writer.truncate, offset)
                    await filesystem_call(writer.flush)
                    raise
                # A cancelled response cannot roll back bytes after the offset commits.
                return success(
                    await filesystem_call(uploads.commit_append, uid, entry, offset + received)
                )

    @router.post("/{uid}/complete", response_model=Success[DirectoryUpload])
    def complete(uid: str):
        with operation():
            return success(uploads.complete(uid))

    @router.delete("/{uid}", response_model=Success[Cancellation])
    def cancel(uid: str):
        with operation():
            return success(uploads.cancel(uid))

    return router
