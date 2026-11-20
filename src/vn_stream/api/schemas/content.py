"""Asset, save and migration resources exposed by the v2 content routes."""

from typing import Literal

from pydantic import BaseModel

from vn_stream.api.schemas.envelopes import ErrorMessage, Timestamp, from_record


class AssetDirectory(BaseModel):
    id: str
    name: str
    path: str
    directory: Literal[True]
    count: int


class AssetEntry(BaseModel):
    id: str
    name: str
    path: str
    directory: Literal[False] = False
    size_bytes: int = from_record("size_bytes", "size")
    kind: str
    mime: str
    archive: str | None = None
    requires_preparation: bool
    internal_path: str | None = None
    format: str | None = None
    preview_supported: bool = True


class AssetEntries(BaseModel):
    entries: list[AssetDirectory | AssetEntry]


class AssetDetail(AssetEntry):
    position: int
    total: int
    previous_id: str | None
    next_id: str | None


class AssetTask(BaseModel):
    task_id: str = from_record("task_id", "id")
    project_id: str
    state: str
    phase: str
    completed_bytes: int
    total_bytes: int
    completed_files: int
    total_files: int
    progress_percent: int
    warnings: int
    cancelled: bool
    mode: str | None = None
    error: str | None = None
    messages: dict[str, ErrorMessage] | None = None


class AssetSummary(BaseModel):
    ready: bool
    categories: list[str]
    counts: dict[str, int]
    size_bytes: int
    updated_at: Timestamp | None
    job: AssetTask | None
    generation: str | None = None
    warning_details: list[ErrorMessage] | None = None
    warnings: int | None = None
    retained_files: int | None = None
    retained_bytes: int | None = None


class PreviewState(BaseModel):
    state: str
    progress_percent: int = from_record("progress_percent", "progress")
    error: str | None = None
    messages: dict[str, ErrorMessage] | None = None


class AssetPreparation(PreviewState):
    task_id: str
    url: str | None = None


class ThumbnailState(BaseModel):
    state: str
    progress_percent: int | None = None
    task_id: str | None = None


class PlainText(BaseModel):
    text: str


class ArchivedSave(BaseModel):
    id: str
    name: str
    archived: bool | None = None
    writable: bool | None = None


class SaveEntry(ArchivedSave):
    kind: str
    updated_at: Timestamp
    size_bytes: int
    copies: int
    thumbnail: bool
    metadata: dict[str, str | int | float]
    sources: list[str]
    signature: str | None = None


class SaveListing(BaseModel):
    saves: list[SaveEntry]
    writable: bool
    archived: list[ArchivedSave]


class SaveField(BaseModel):
    id: str
    name: str
    type: Literal["boolean", "text", "number"]
    value: str | bool
    numeric_type: str | None = None


class SaveRevision(BaseModel):
    revision: str


class SaveVariables(SaveRevision):
    fields: list[SaveField]
    editable: bool
    writable: bool
    reason: str | None
    counts: dict[str, int]


class SaveUpload(BaseModel):
    id: str


class SaveBackup(BaseModel):
    id: str
    created_at: Timestamp
    kind: str
    state: str
    name: str | None = None
    size_bytes: int | None = None
    copies: int | None = None


class SigningIssue(BaseModel):
    name: str
    code: str


class SigningState(BaseModel):
    state: str
    project_id: str
    task_id: str | None = None
    created_at: Timestamp | None = None
    signed: int
    skipped: int
    failed: int
    total: int
    errors: list[SigningIssue]


class MigrationProject(BaseModel):
    project_id: str
    name: str
    display_name: str | None
    release_version: str | None
    engine_version: str | None
    engine_id: str


class MigrationSource(MigrationProject):
    available: bool
    counts: dict[str, int]


class MigrationEntry(BaseModel):
    name: str
    kind: str
    size_bytes: int
    updated_at: Timestamp | None
    source_copies: int
    target_copies: int
    action: Literal["add", "overwrite", "remove"]
    valid: bool
    reason: ErrorMessage | None


class MigrationReview(BaseModel):
    id: str
    kind: Literal["migration", "undo"]
    source: MigrationProject
    target: MigrationProject
    include_persistent: bool
    entries: list[MigrationEntry]
    counts: dict[str, int]
    required_bytes: int
    warnings: list[ErrorMessage]
    migration_id: str | None = None


class MigrationResult(BaseModel):
    name: str
    action: Literal["add", "overwrite", "remove"]
    state: str
    reason: ErrorMessage | None = None


class MigrationTask(BaseModel):
    task_id: str = from_record("task_id", "id")
    kind: Literal["migration", "undo"]
    target_id: str
    source_id: str | None
    source: MigrationProject
    target: MigrationProject
    state: str
    phase: str
    created_at: Timestamp
    total: int
    prepared: int
    backed_up: int
    completed: int
    skipped: int
    results: list[MigrationResult]
    error: str | None
    messages: dict[str, ErrorMessage] | None = None
    undo_id: str | None
    migration_id: str | None
    request_id: str
