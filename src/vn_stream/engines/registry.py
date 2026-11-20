"""Single builtin engine registry and shared validation policy."""

from dataclasses import replace
from pathlib import Path

from vn_stream.engines.contracts import Capabilities, EngineAdapter, Inspection, LaunchPlan
from vn_stream.engines.krkr.adapter import KrkrAdapter
from vn_stream.engines.ons.adapter import OnsAdapter
from vn_stream.engines.renpy.adapter import RenPyAdapter
from vn_stream.i18n import descriptor, text
from vn_stream.runtimes.catalog import verify_resources

ADAPTERS: dict[str, EngineAdapter] = {
    a.engine_id: a for a in (RenPyAdapter(), OnsAdapter(), KrkrAdapter())
}
_DEFAULT_CAPABILITIES: Capabilities = {
    "assets": False,
    "save_management": False,
    "save_editing": False,
    "save_migration": False,
    "native_actions": False,
    "exit_mode": "stop",
    "uses_original_engine_version": False,
}
_MANUAL_VALIDATIONS = {"validated", "experimental"}


def capabilities(engine_id: str | None) -> Capabilities:
    adapter = ADAPTERS.get(engine_id) if engine_id is not None else None
    merged = _DEFAULT_CAPABILITIES.copy()
    if adapter:
        merged.update(adapter.capabilities)
    return merged


def describe_engine(engine_id):
    adapter = ADAPTERS.get(engine_id)
    return {
        "engine_id": engine_id,
        "display_name": adapter.display_name if adapter else engine_id,
        "engine_name_key": getattr(adapter, "display_name_key", None),
    }


def describe_engines():
    return [describe_engine(engine_id) for engine_id in ADAPTERS]


def _inspect_project(project: Path, *, engine_id: str | None = None) -> Inspection:
    if any(p.is_symlink() for p in (project, *project.parents)) or not project.is_dir():
        return Inspection(None, "unknown")
    matches = [
        found for adapter in ADAPTERS.values() if (found := adapter.inspect_project(project))
    ]
    if engine_id:
        return next((r for r in matches if r.engine_id == engine_id), Inspection(None, "unknown"))
    if len(matches) > 1:
        return Inspection(
            None,
            "conflict",
            evidence=tuple(r.engine_id for r in matches if r.engine_id is not None),
        )
    return matches[0] if matches else Inspection(None, "unknown")


def inspect_project(project: Path, *, engine_id: str | None = None) -> Inspection:
    try:
        return _inspect_project(project, engine_id=engine_id)
    except (OSError, ValueError, UnicodeError):
        return Inspection(
            engine_id,
            "unsupported",
            warnings=("inspection_failed",),
            capabilities=capabilities(engine_id),
        )


def resolve_runtime(inspection: Inspection, installed: list[dict]) -> dict:
    """Apply the adapter's automatic policy to installed, eligible runtimes."""
    if inspection.engine_id not in ADAPTERS or inspection.status in ("conflict", "unsupported"):
        return dict(version=None, runtime_id=None, kind=inspection.status)
    family = [
        r
        for r in installed
        if r.get("engine_id", "renpy") == inspection.engine_id
        and r.get("validation") == "validated"
    ]
    return ADAPTERS[inspection.engine_id].resolve_runtime(inspection, family)


def _runtime_ineligibility(project, inspection, runtime, *, allow_experimental):
    if (
        inspection.engine_id not in ADAPTERS
        or runtime.get("engine_id", "renpy") != inspection.engine_id
        or inspection.status in ("conflict", "unsupported")
        or runtime.get("validation")
        not in (_MANUAL_VALIDATIONS if allow_experimental else {"validated"})
    ):
        return text("engine.runtimeUnavailable")
    if any(p.is_symlink() for p in (project, *project.parents)) or not project.is_dir():
        return text("engine.invalidProject")
    adapter = ADAPTERS[inspection.engine_id]
    try:
        if not verify_resources(
            Path(runtime["path"]), runtime, required=getattr(adapter, "required_resources", ())
        ):
            return text("engine.runtimeUnavailable")
        compatibility = getattr(adapter, "runtime_compatibility", None)
        reason = compatibility(project, inspection, runtime) if compatibility else None
        return text(reason["code"], **reason["params"]) if reason else None
    except OSError:
        return text("engine.runtimeUnavailable")


def _manual_inspection(inspection):
    return replace(inspection, status="manual") if inspection.status == "conflict" else inspection


def project_runtimes(project: Path, inspection: Inspection, installed: list[dict]):
    """Read-only options for explicit selection and the automatic recommendation."""
    options = []
    eligible = []
    manual = _manual_inspection(inspection)
    for runtime in installed:
        if (
            runtime.get("engine_id", "renpy") != inspection.engine_id
            or runtime.get("validation") not in _MANUAL_VALIDATIONS
        ):
            continue
        error = _runtime_ineligibility(project, manual, runtime, allow_experimental=True)
        if error is None:
            eligible.append(runtime)
        options.append(
            {
                "runtime_id": runtime["id"],
                "display_name": runtime.get("display_name") or runtime["version"],
                "build_version": runtime["version"],
                "available": error is None,
                "reason": descriptor(error) if error else None,
            }
        )
    return resolve_runtime(inspection, eligible), options


def prepare_launch(
    project: Path, inspection: Inspection, runtime: dict, *, allow_experimental: bool = False
) -> LaunchPlan:
    if allow_experimental:
        inspection = _manual_inspection(inspection)
    if error := _runtime_ineligibility(
        project, inspection, runtime, allow_experimental=allow_experimental
    ):
        raise ValueError(error)
    assert inspection.engine_id is not None
    return ADAPTERS[inspection.engine_id].prepare_launch(project, inspection, runtime)
