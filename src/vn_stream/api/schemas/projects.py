"""Project command contracts; unknown fields are rejected."""

from pydantic import BaseModel, ConfigDict, Field

from vn_stream.api.schemas.envelopes import ErrorMessage, Timestamp, from_record
from vn_stream.engines.contracts import EngineId


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    engine_id: EngineId | None = None
    password: str | None = Field(default=None, max_length=1024, repr=False)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_id: str | None = None


class Engine(BaseModel):
    engine_id: str
    display_name: str
    engine_name_key: str | None = None


class Runtime(BaseModel):
    runtime_id: str = from_record("runtime_id", "id")
    engine_id: str
    build_version: str = from_record("build_version", "version")
    display_name: str | None = None
    flavor: str | None = None
    validation: str | None = None
    default: bool = False
    source_commit: str | None = None
    binary_sha256: str | None = None


class RuntimeRelease(BaseModel):
    version: str
    python: str
    platform: str
    url: str
    sha256: str
    checksum_url: str
    installed: bool


class RuntimeOption(BaseModel):
    runtime_id: str
    display_name: str
    build_version: str
    available: bool
    reason: ErrorMessage | None


class RuntimeRecommendation(BaseModel):
    runtime_id: str | None = None
    build_version: str | None = from_record("build_version", "version")
    display_name: str | None = None
    kind: str


class Recognition(BaseModel):
    status: str
    evidence: list[str]
    warnings: list[str]


class Capabilities(BaseModel):
    assets: bool
    save_management: bool
    save_editing: bool
    save_migration: bool
    native_actions: bool
    exit_mode: str
    uses_original_engine_version: bool


class DeletionTask(BaseModel):
    task_id: str = from_record("task_id", "id")
    project_id: str
    name: str
    state: str
    phase: str
    error: str | None = None
    messages: dict[str, ErrorMessage] | None = None


class Project(BaseModel):
    project_id: str
    name: str
    path: str
    engine_id: str
    engine_version: str | None
    engine_display_name: str
    engine_name_key: str | None
    capabilities: Capabilities
    runtime_options: list[RuntimeOption]
    recognition: Recognition
    release_version: str | None
    icon_url: str | None
    metadata_sources: dict[str, list[str]] | None = None
    size_bytes: int | None
    last_played_at: Timestamp | None
    play_seconds: float | None
    available: bool
    runtime_id: str | None
    runtime_build_version: str | None = from_record("runtime_build_version", "runtime_version")
    runtime_override: str | None
    recommendation: RuntimeRecommendation
    deletion: DeletionTask | None


class RenderGeometry(BaseModel):
    physical: tuple[int, int]
    viewport: tuple[int, int, int, int]
    position: tuple[int, int]


class DisplayState(BaseModel):
    state: str
    revision: int
    source: tuple[int, int] | None = None
    content: tuple[int, int] | None = None
    capture: tuple[int, int] | None = None
    render: RenderGeometry | None = None
    measurement: str | None = None
    diagnostic: str | None = None
    notice: str | None = None
    messages: dict[str, ErrorMessage] | None = None
    ready: bool | None = None


class Session(BaseModel):
    state: str
    engine_id: str | None = None
    action_policy: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    pid: int | None = None
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None
    exit_code: int | None = None
    reason: str | None = None
    display: DisplayState | None = None
    readiness: str | None = None


class Stream(BaseModel):
    available: bool
    url: str | None
    backend: str
