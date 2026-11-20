"""Engine-owned launch arguments and runtime profile selection."""

from pathlib import Path

from vn_stream.engines.contracts import LaunchPlan


def resolve_runtime(inspection, installed):
    result = recommend_runtime(inspection.engine_version, [r["version"] for r in installed])
    selected = next((r for r in installed if r["version"] == result["version"]), None)
    return {**result, "runtime_id": selected["id"] if selected else None}


def prepare_launch(project, inspection, runtime):
    command, env = launch_configuration(project, Path(runtime["path"]))
    return LaunchPlan(
        command,
        {**env, "VN_STREAM_ENGINE_ID": "renpy"},
        project,
        "renpy",
        display_policy="engine_report",
        action_policy="renpy",
    )


def launch_configuration(project: Path, runtime: Path) -> tuple[list[str], dict[str, str]]:
    saves = project / "game/saves"
    home = project / ".vn-stream/home"
    saves.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    return (
        ["/bin/sh", str(runtime / "renpy.sh"), "--savedir", str(saves), str(project), "run"],
        {"HOME": str(home), "RENPY_MULTIPERSISTENT": str(home / "multipersistent")},
    )


def recommend_runtime(original: str | None, versions: list[str]) -> dict:
    """Prefer exact metadata, then the nearest newer SDK in the same major family."""

    def number(value):
        return tuple(int(part) for part in value.split("."))

    if original is None:
        return {"version": None, "kind": "unknown"}
    source = number(original)
    candidates = sorted(set(versions), key=number)
    if original in candidates:
        return {"version": original, "kind": "exact"}
    newer = [v for v in candidates if number(v)[0] == source[0] and number(v) > source]
    return {"version": newer[0] if newer else None, "kind": "newer" if newer else "unavailable"}
