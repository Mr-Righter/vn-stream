"""Engine-owned launch arguments and runtime profile selection."""

from pathlib import Path

from vn_stream.engines.contracts import LaunchPlan
from vn_stream.runtimes.native import native_environment


def prepare_launch(project, inspection, runtime):
    executable, env = native_environment(project, runtime)
    if runtime["flavor"] == "krkr-rs":
        command = [str(executable), "--base-dir", str(project), "--backend", "vulkan"]
    elif runtime["flavor"] == "krkr-next":
        entry = project / inspection.entry if inspection.entry == "data.xp3" else project
        command = [str(executable), "--game", str(entry)]
    else:
        env["ALSOFT_CONF"] = str(Path(runtime["path"]) / "Resources/vn-stream-alsoft.conf")
        command = [str(executable), "--game", str(project)]
    return LaunchPlan(command, env, project, "krkr", inspection.canvas)
