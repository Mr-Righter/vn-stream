"""Shared native artifact checks and isolated HOME preparation."""

from pathlib import Path

from vn_stream.i18n import text
from vn_stream.storage.files import safe as _file


def resolve_native_runtime(inspection, installed):
    candidates = sorted(
        (r for r in installed if r.get("validation") == "validated"), key=lambda r: r["id"]
    )
    # Preserve the NAS's runtime policy when updating from an image without a bundle.
    local = [runtime for runtime in candidates if not runtime.get("bundled")]
    candidates = local or candidates
    # Different forks are compatibility choices, never semantic-version upgrades.
    defaults = [runtime for runtime in candidates if runtime.get("default") is True]
    choices = defaults or candidates
    selected = choices[0] if len(choices) == 1 else None
    return dict(
        version=selected["version"] if selected else None,
        runtime_id=selected["id"] if selected else None,
        display_name=(selected.get("display_name") or selected.get("flavor") or selected["version"])
        if selected
        else None,
        kind="profile" if selected else "unavailable",
    )


def native_environment(project, runtime):
    home = _file(project, ".vn-stream/home")
    home.mkdir(parents=True, exist_ok=True)
    executable = _file(Path(runtime["path"]), runtime["executable"])
    from vn_stream.runtimes.catalog import verify_binary, verify_resources

    if runtime.get("binary_sha256") and not verify_binary(executable, runtime["binary_sha256"]):
        raise ValueError(text("engine.runtimeUnavailable"))
    if not verify_resources(Path(runtime["path"]), runtime):
        raise ValueError(text("engine.runtimeUnavailable"))
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "VN_STREAM_ENGINE_ID": runtime["engine_id"],
    }
    return executable, env
