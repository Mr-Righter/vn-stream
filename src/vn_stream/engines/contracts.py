"""Typed engine contracts; no registration or filesystem side effects."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, TypedDict

EngineId = str


class Capabilities(TypedDict):
    assets: bool
    save_management: bool
    save_editing: bool
    save_migration: bool
    native_actions: bool
    exit_mode: str
    uses_original_engine_version: bool


@dataclass(frozen=True)
class Inspection:
    engine_id: str | None
    status: str
    entry: str | None = None
    engine_version: str | None = None
    evidence: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    canvas: tuple[int, int] | None = None
    save_directory: str | None = None
    encoding: str | None = None
    capabilities: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LaunchPlan:
    command: list[str]
    env: dict[str, str]
    cwd: Path
    engine_id: str
    canvas: tuple[int, int] | None = None
    display_policy: Literal["engine_report", "window"] = "window"
    action_policy: Literal["renpy", "none"] = "none"


class AssetProvider(Protocol):
    """Optional adapter seam; resources remain original, revision-bound descriptors."""

    categories: tuple[str, ...]

    def enumerate(
        self, project: Path, inspection: Inspection, check: Callable[[], None]
    ) -> tuple[dict[str, dict], list[dict]]: ...


class EngineAdapter(Protocol):
    """Required operations with optional display metadata and a read-only compatibility hook."""

    engine_id: str
    directory_name: str
    display_name: str
    capabilities: Capabilities

    def inspect_project(self, project: Path) -> Inspection | None: ...
    def resolve_runtime(self, inspection: Inspection, installed: list[dict]) -> dict: ...
    def prepare_launch(
        self, project: Path, inspection: Inspection, runtime: dict
    ) -> LaunchPlan: ...
