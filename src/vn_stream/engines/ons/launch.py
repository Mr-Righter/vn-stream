"""Engine-owned arguments for pinned ONS runtime branches."""

from vn_stream.engines.contracts import LaunchPlan
from vn_stream.runtimes.native import native_environment
from vn_stream.storage.files import safe as _file


def prepare_launch(project, inspection, runtime):
    flavor = runtime.get("flavor")
    executable, env = native_environment(project, runtime)
    command = [str(executable), "--root", str(project), "--window"]
    if flavor == "new":
        if inspection.entry != "0.txt":
            command += ["--game-script", inspection.entry]
        if inspection.save_directory:
            saves = _file(project, inspection.save_directory)
            saves.mkdir(parents=True, exist_ok=True)
        command += ["--save", str(project)]
        return LaunchPlan(command, env, project, "ons", inspection.canvas)
    if flavor in {"yuri", "jh", "zero"}:
        command.append("--fontcache")
    if inspection.save_directory:
        saves = _file(project, inspection.save_directory)
        saves.mkdir(parents=True, exist_ok=True)
        if flavor == "yuri":
            command += ["--save-dir", str(saves)]
        # Jh/zero Linux read the script savedir themselves.
    if flavor in {"yuri", "zero"}:
        command += ["--enc:" + inspection.encoding]
    if _file(project, "default.ttf").is_file():
        command += ["--font", "default.ttf"]
    return LaunchPlan(command, env, project, "ons", inspection.canvas)
