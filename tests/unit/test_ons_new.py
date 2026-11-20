"""NEW is an explicit UTF-8 text-script runtime, not a Yuri alias."""

import pytest

from vn_stream.engines.registry import inspect_project, prepare_launch


def record(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "runner").write_text("#!/bin/sh\nexit 0\n")
    (runtime / "runner").chmod(0o755)
    return dict(
        id="ons:new:test",
        engine_id="ons",
        flavor="new",
        version="test",
        validation="experimental",
        path=str(runtime),
        executable="runner",
    )


def test_new_rejects_legacy_script_before_creating_home(tmp_path):
    project = tmp_path / "game"
    project.mkdir()
    script = "*define\ngame\n*start\nend\n"
    (project / "nscript.dat").write_bytes(bytes(c ^ 0x84 for c in script.encode()))
    with pytest.raises(ValueError, match="UTF-8"):
        prepare_launch(project, inspect_project(project), record(tmp_path), allow_experimental=True)
    assert not (project / ".vn-stream/home").exists()


def test_new_uses_separate_arguments_and_preserves_game(tmp_path):
    project = tmp_path / "game"
    (project / "fonts").mkdir(parents=True)
    script = '*define\nsavedir "save"\ngame\n*start\n; 中文\nend\n'
    (project / "0.txt").write_text(script, encoding="utf-8-sig")
    (project / "fonts/default.ttf").write_bytes(b"font")
    launch = prepare_launch(
        project, inspect_project(project), record(tmp_path), allow_experimental=True
    )
    assert "--window" in launch.command
    assert launch.command[launch.command.index("--save") + 1] == str(project)
    assert "--fontcache" not in launch.command
    assert not any(s.startswith("--enc:") for s in launch.command)
    assert (project / "0.txt").read_text(encoding="utf-8-sig") == script


def test_new_project_availability_is_exposed_without_executing_script(tmp_path):
    from vn_stream.projects.catalog import ProjectCatalog
    from vn_stream.projects.queries import ProjectQueries

    project = tmp_path / "original"
    project.mkdir()
    (project / "nscript.dat").write_bytes(bytes(c ^ 0x84 for c in b"*define\ngame\n*start\nend\n"))
    catalog = ProjectCatalog(tmp_path / "data", [tmp_path])
    game = catalog.register(project, "Test")
    preferred = {**record(tmp_path), "default": True, "validation": "validated"}
    compatible = {**preferred, "id": "ons:yuri:test", "flavor": "yuri", "default": False}
    failed = {**compatible, "id": "ons:yuri:failed", "validation": "failed"}
    result = ProjectQueries(catalog, None).describe_project(
        game, installed=[preferred, compatible, failed]
    )
    blocked, usable = result["runtime_options"]
    assert blocked["runtime_id"] == "ons:new:test"
    assert blocked["build_version"] == "test"
    assert blocked["available"] is False
    assert blocked["reason"]["code"] == "engine.onsNewScript"
    assert usable["runtime_id"] == "ons:yuri:test"
    assert usable["available"] is True
    assert result["recommendation"]["runtime_id"] == "ons:yuri:test"
    assert not (project / ".vn-stream/home").exists()
