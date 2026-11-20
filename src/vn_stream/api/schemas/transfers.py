"""Public import jobs, source browser and resumable upload responses."""

from typing import Literal

from pydantic import BaseModel

from vn_stream.api.schemas.envelopes import ErrorMessage, from_record
from vn_stream.api.schemas.projects import Project


class ImportTask(BaseModel):
    task_id: str = from_record("task_id", "id")
    request_id: str
    state: Literal["running", "completed", "failed"]
    phase: str
    completed_bytes: int
    total_bytes: int | None
    progress_percent: int
    error: str | None
    warning: str | None
    messages: dict[str, ErrorMessage] | None = None
    project: Project | None
    source_name: str
    engine_id: str | None
    upload_id: str | None
    directory_upload_id: str | None
    abandoned: bool
    delete_source: bool


class AbandonedImport(BaseModel):
    abandoned: bool


class ImportRoot(BaseModel):
    id: int
    name: str


class SourceEntry(BaseModel):
    name: str
    path: str
    directory: bool
    project: bool


class ImportSources(BaseModel):
    roots: list[ImportRoot]
    entries: list[SourceEntry]
    root: int | None = None
    path: str | None = None
    source: str | None = None
    project: bool | None = None


class ArchiveUpload(BaseModel):
    upload_id: str = from_record("upload_id", "id")
    filename: str
    size_bytes: int = from_record("size_bytes", "size")
    offset_bytes: int = from_record("offset_bytes", "offset")


class DirectoryFile(BaseModel):
    file_id: str = from_record("file_id", "id")
    path: str
    size_bytes: int = from_record("size_bytes", "size")
    offset_bytes: int = from_record("offset_bytes", "offset")


class DirectoryFiles(BaseModel):
    files: list[DirectoryFile]


class DirectoryUpload(DirectoryFiles):
    upload_id: str = from_record("upload_id", "id")
    name: str
    file_count: int
    total_bytes: int
    state: Literal["manifest", "uploading", "complete"]
    manifest_count: int
    manifest_bytes: int
    uploaded_bytes: int
    next_cursor: int | None
    chunk_bytes: int
