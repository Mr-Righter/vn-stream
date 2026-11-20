"""Engine seams reject ambiguous projects and preserve engine-owned save layout."""

import json

import pytest

from tests.auth_helpers import api_data
from vn_stream.engines.registry import ADAPTERS, inspect_project, prepare_launch, resolve_runtime
from vn_stream.imports.publication import import_project
from vn_stream.runtimes.catalog import available_runtimes


def ons(root):
    root.mkdir(parents=True)
    script = ';$S1280,720G1500\n*define\nsavedir "save"\ngame\n*start\nend\n'
    (root / "nscript.dat").write_bytes(bytes(x ^ 0x84 for x in script.encode("gbk")))
    (root / "00.ns2").write_bytes(b"archive")
    (root / "default.ttf").write_bytes(b"font")
    (root / "save").mkdir()
    (root / "saves").mkdir()
    return root


def runtime(root, *, engine="ons", validation="validated"):
    root.mkdir(parents=True)
    executable = root / "runner"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    (root / "runtime.json").write_text(
        json.dumps(
            dict(
                schema=1,
                id="ons:yuri:test",
                engine_id=engine,
                flavor="yuri",
                version="test",
                executable="runner",
                validation=validation,
            )
        )
    )
    return root


def test_ons_inspection_and_launch_preserve_script_save_location(tmp_path):
    project = ons(tmp_path / "project")
    detected = inspect_project(project)
    assert detected.engine_id == "ons"
    assert detected.canvas == (1280, 720)
    assert detected.save_directory == "save"
    installed = available_runtimes(tmp_path / "runtimes", ADAPTERS)
    assert installed == []
    runtime(tmp_path / "runtimes/onscripter/yuri")
    installed = available_runtimes(tmp_path / "runtimes", ADAPTERS)
    selected = resolve_runtime(detected, installed)
    assert selected["runtime_id"] == "ons:yuri:test"
    launch = prepare_launch(project, detected, installed[0])
    assert launch.engine_id == "ons"
    assert "--save-dir" in launch.command
    assert launch.command[launch.command.index("--save-dir") + 1] == str(project / "save")
    assert "RENPY_SEARCHPATH" not in launch.env
    assert not (project / "game").exists()


def test_unknown_and_conflicting_evidence_do_not_auto_select(tmp_path):
    assert inspect_project(tmp_path).status == "unknown"
    project = ons(tmp_path / "project")
    (project / "startup.tjs").write_text("System.exit();")
    assert inspect_project(project).status == "conflict"
    assert resolve_runtime(inspect_project(project), [])["runtime_id"] is None


@pytest.mark.parametrize(
    "flavor,expected,absent",
    [
        ("yuri", ["--fontcache", "--enc:gbk", "--save-dir"], []),
        ("jh", ["--fontcache"], ["--save-dir", "--enc:gbk"]),
        ("zero", ["--fontcache", "--enc:gbk"], ["--save-dir", "--save"]),
    ],
)
def test_ons_profiles_use_only_their_supported_arguments(tmp_path, flavor, expected, absent):
    project = ons(tmp_path / "project")
    runtime(tmp_path / "runtimes/onscripter/native")
    installed = available_runtimes(tmp_path / "runtimes", ADAPTERS)[0]
    installed["flavor"] = flavor
    launch = prepare_launch(project, inspect_project(project), installed)
    for argument in expected:
        assert argument in launch.command
    for argument in absent:
        assert argument not in launch.command
    assert not any("animation" in arg for arg in launch.command)
    assert launch.cwd == project


def test_experimental_runtime_requires_explicit_selection_and_never_auto_recommends(tmp_path):
    project = ons(tmp_path / "project")
    runtime(tmp_path / "runtimes/onscripter/native", validation="experimental")
    installed = available_runtimes(tmp_path / "runtimes", ADAPTERS)[0]
    assert resolve_runtime(inspect_project(project), [installed])["runtime_id"] is None
    with pytest.raises(ValueError):
        prepare_launch(project, inspect_project(project), installed)
    assert (
        prepare_launch(
            project, inspect_project(project), installed, allow_experimental=True
        ).engine_id
        == "ons"
    )
    with pytest.raises(ValueError):
        prepare_launch(
            project,
            inspect_project(project),
            {**installed, "validation": "failed"},
            allow_experimental=True,
        )


def test_unknown_ons_flavor_is_rejected_before_home_creation(tmp_path):
    project = ons(tmp_path / "project")
    runtime(tmp_path / "runtimes/onscripter/native")
    installed = available_runtimes(tmp_path / "runtimes", ADAPTERS)[0]
    with pytest.raises(ValueError):
        prepare_launch(project, inspect_project(project), {**installed, "flavor": "unknown"})
    assert not (project / ".vn-stream/home").exists()


def test_krkr_archive_signature_and_entry(tmp_path):
    (tmp_path / "data.xp3").write_bytes(b"XP3\r\n \n\x1a\x8bg\x01" + b"\0" * 32)
    result = inspect_project(tmp_path)
    assert result.engine_id == "krkr"
    assert result.entry == "data.xp3"
    assert result.engine_version is None
    assert result.capabilities["save_management"] is False


def test_unvalidated_runtime_is_not_selectable_and_wrong_family_rejected(tmp_path):
    project = ons(tmp_path / "project")
    runtime(tmp_path / "runtimes/onscripter/yuri", validation="experimental")
    installed = available_runtimes(tmp_path / "runtimes", ADAPTERS)
    assert resolve_runtime(inspect_project(project), installed)["runtime_id"] is None
    with pytest.raises(ValueError):
        prepare_launch(project, inspect_project(project), {**installed[0], "engine_id": "krkr"})


def test_runtime_executable_cannot_escape_installation(tmp_path):
    root = runtime(tmp_path / "runtimes/onscripter/yuri")
    value = json.loads((root / "runtime.json").read_text())
    value["executable"] = "../../external"
    (root / "runtime.json").write_text(json.dumps(value))
    assert available_runtimes(tmp_path / "runtimes", ADAPTERS) == []


def test_ons_invalid_save_path_fails_closed(tmp_path):
    project = ons(tmp_path / "project")
    (project / "nscript.dat").unlink()
    (project / "0.txt").write_text('*define\nsavedir "../outside"\ngame\n')
    detected = inspect_project(project)
    assert detected.status == "unsupported"
    assert resolve_runtime(detected, [])["runtime_id"] is None


def test_catalog_ignores_old_service_database(tmp_path):
    import sqlite3

    from vn_stream.projects.catalog import ProjectCatalog

    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    db = sqlite3.connect(data / "state/library.sqlite3")
    db.execute(
        "CREATE TABLE games(id TEXT PRIMARY KEY,name TEXT,path TEXT UNIQUE,engine_version TEXT)"
    )
    db.execute("INSERT INTO games VALUES(?,?,?,?)", ("old", "Legacy", "/missing", "8.3.2"))
    db.commit()
    db.close()
    root = tmp_path / "games"
    project = ons(root / "onscripter/native")
    library = ProjectCatalog(data, [root], library_root=root)
    game = import_project(library, project)
    assert game.engine_id == "ons"
    with pytest.raises(KeyError):
        library.get("old")
    assert library.get(game.project_id) == game
    assert import_project(library, project).project_id == game.project_id


def test_discovery_and_archive_normalization_find_native_project_once(tmp_path):
    from vn_stream.imports.archive import normalize_project
    from vn_stream.projects.catalog import ProjectCatalog

    root = tmp_path / "games"
    project = ons(root / "onscripter/native")
    ons(project / "alternative")
    assert normalize_project(root, apk=False) == project
    library = ProjectCatalog(tmp_path / "data", [root], library_root=root)
    assert library.discover()["added"] == 1
    assert library.discover()["added"] == 0


def test_native_api_capabilities_and_signing_are_isolated(tmp_path):
    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    project = ons(tmp_path / "games/onscripter/native")
    app = create_app(tmp_path / "data", [tmp_path / "games"], "secret")
    with TestClient(app) as client:
        sign_in(client, "secret")
        response = client.post("/api/v2/projects", json={"path": str(project)})
        assert response.status_code == 201, response.text
        game = api_data(response)
        assert game["engine_id"] == "ons"
        assert game["capabilities"]["save_management"] is False
        assert client.get(f"/api/v2/projects/{game['project_id']}/assets").status_code == 200
        for suffix in ["saves", "saves/signing", "saves/migration-sources"]:
            assert client.get(f"/api/v2/projects/{game['project_id']}/{suffix}").status_code == 409
        with app.state.saves.library.db.transaction() as db:
            assert not db.execute(
                "SELECT 1 FROM save_signing_jobs WHERE project_id=?", (game["project_id"],)
            ).fetchone()
        assert (
            client.post(
                f"/api/v2/projects/{game['project_id']}/start", json={"runtime_id": "renpy:8.3.2"}
            ).status_code
            == 409
        )
        assert (
            client.post(f"/api/v2/projects/{game['project_id']}/start", json={}).status_code == 409
        )


def test_native_api_launch_and_idempotent_session(tmp_path):
    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    project = ons(tmp_path / "games/onscripter/native")
    runtime_path = runtime(tmp_path / "data/runtimes/onscripter/yuri")
    (runtime_path / "runner").write_text('#!/bin/sh\nprintf "%s\\n" "$@" > args.txt\nsleep 20\n')
    with TestClient(create_app(tmp_path / "data", [tmp_path / "games"], "secret")) as client:
        sign_in(client, "secret")
        game = api_data(client.post("/api/v2/projects", json={"path": str(project)}))
        first = client.post(f"/api/v2/projects/{game['project_id']}/start", json={})
        assert first.status_code == 200, first.text
        assert api_data(first)["engine_id"] == "ons"
        second = client.post(f"/api/v2/projects/{game['project_id']}/start", json={})
        assert api_data(first)["session_id"] == api_data(second)["session_id"]
        assert not (project / "game").exists()
        assert client.post("/api/v2/session/stop").status_code == 200


def test_window_probe_ignores_unrelated_process_group(monkeypatch):
    from types import SimpleNamespace

    from vn_stream.streaming.window_canvas import window_geometry

    def run(args, **kwargs):
        values = {
            ("xprop", "-root", "_NET_CLIENT_LIST"): "window id # 0x123, 0x456",
            ("xprop", "-id", "0x123", "_NET_WM_PID"): "= 12",
            ("xprop", "-id", "0x456", "_NET_WM_PID"): "= 34",
            ("xwininfo", "-id", "0x456"): "Map State: IsViewable\n Width: 1280\n Height: 720",
        }
        return SimpleNamespace(stdout=values[tuple(args)])

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("os.getpgid", lambda pid: 500 if pid == 34 else 999)
    assert window_geometry(500) == ("0x456", 1280, 720)


def test_native_window_canvas_has_no_renpy_hook_and_requires_window(tmp_path, monkeypatch):
    from vn_stream.streaming.window_canvas import WindowCanvas

    c = WindowCanvas(tmp_path, "session", (1280, 720), apply=lambda w, h: (w, h), now=0)
    assert c.environment() == {}
    assert not (c.directory / "scripts").exists()
    c.pid = 100
    monkeypatch.setattr("vn_stream.streaming.window_canvas.window_geometry", lambda pid: None)
    assert c.poll(6)
    assert c.status["ready"] is False
    monkeypatch.setattr(
        "vn_stream.streaming.window_canvas.window_geometry", lambda pid: ("0x1", 1280, 720)
    )
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: None)
    assert c.poll(7)
    assert c.status["ready"] is True
    assert c.status["capture"] == [1280, 720]
    c.close()


def test_native_inspection_reuses_script_parse_but_detects_revision(tmp_path, monkeypatch):
    from vn_stream.engines.ons import inspection as engines

    project = ons(tmp_path / "project")
    assert inspect_project(project).canvas == (1280, 720)
    before = engines._ons_script.cache_info().hits
    assert inspect_project(project).canvas == (1280, 720)
    assert engines._ons_script.cache_info().hits > before
    (project / "nscript.dat").unlink()
    (project / "0.txt").write_text(";$S800,600\n*define\ngame\n")
    assert inspect_project(project).canvas == (800, 600)


def test_conflicting_project_requires_explicit_engine_and_retains_choice(tmp_path):
    from vn_stream.projects.catalog import ProjectCatalog

    project = ons(tmp_path / "games/mixed")
    (project / "startup.tjs").write_text("System.exit();")
    library = ProjectCatalog(tmp_path / "data", [tmp_path / "games"])
    with pytest.raises(ValueError):
        import_project(library, project)
    game = import_project(library, project, engine_id="ons")
    assert game.engine_id == "ons"
    assert import_project(library, project).project_id == game.project_id


def test_native_archive_engine_selection_and_task_retry(tmp_path):
    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    project = ons(tmp_path / "games/mixed")
    (project / "startup.tjs").write_text("System.exit();")
    with TestClient(create_app(tmp_path / "data", [tmp_path / "games"], "secret")) as client:
        sign_in(client, "secret")
        task = api_data(
            client.post("/api/v2/imports", json={"path": str(project), "request_id": "mixed"})
        )
        jobs = client.app.state.import_jobs
        assert jobs.wait(task["task_id"])["state"] == "failed"
        retried = client.post(
            f"/api/v2/tasks/imports/{task['task_id']}/retry", json={"engine_id": "ons"}
        )
        assert retried.status_code == 202, retried.text
        task = jobs.wait(task["task_id"])
        assert task["state"] == "completed", task
        assert task["project"]["engine_id"] == "ons"


def test_changed_native_binary_is_no_longer_installed(tmp_path):
    import hashlib

    root = runtime(tmp_path / "runtimes/onscripter/yuri")
    value = json.loads((root / "runtime.json").read_text())
    value["binary_sha256"] = hashlib.sha256((root / "runner").read_bytes()).hexdigest()
    (root / "runtime.json").write_text(json.dumps(value))
    assert len(available_runtimes(tmp_path / "runtimes", ADAPTERS)) == 1
    (root / "runner").write_text("#!/bin/sh\nexit 2\n")
    assert available_runtimes(tmp_path / "runtimes", ADAPTERS) == []


def test_unreadable_native_script_does_not_break_catalog_inspection(tmp_path, monkeypatch):
    from pathlib import Path

    project = ons(tmp_path / "project")
    original = Path.open

    def denied(path, *args, **kwargs):
        if path.name == "nscript.dat":
            raise PermissionError("private diagnostic")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    value = inspect_project(project, engine_id="ons")
    assert value.status == "unsupported"
    assert value.warnings == ("inspection_failed",)


def test_native_window_tracks_late_resolution_changes(tmp_path, monkeypatch):
    from vn_stream.streaming.window_canvas import WindowCanvas

    observed = ["0x1", 640, 480]
    monkeypatch.setattr(
        "vn_stream.streaming.window_canvas.window_geometry", lambda pid: tuple(observed)
    )
    monkeypatch.setattr("subprocess.run", lambda *args, **kw: None)
    calls = []
    c = WindowCanvas(tmp_path, "late", apply=lambda w, h: calls.append((w, h)) or (w, h), now=0)
    c.pid = 100
    assert c.poll(1)
    observed[1:] = [1280, 720]
    assert c.poll(3)
    assert c.status["source"] == [1280, 720]
    assert calls == [(640, 480), (1280, 720)]
    monkeypatch.setattr("vn_stream.streaming.window_canvas.window_geometry", lambda pid: None)
    assert c.poll(5)
    assert c.status["ready"] is False


def test_readonly_native_copy_preserves_resources_and_save_layout(tmp_path):
    from vn_stream.projects.catalog import ProjectCatalog

    project = ons(tmp_path / "source/native")
    (project / "save/slot1.dat").write_bytes(b"native save")
    library = ProjectCatalog(tmp_path / "data", [project.parent], library_root=tmp_path / "games")
    game = (
        __import__("vn_stream.imports.publication", fromlist=["ImportPublication"])
        .ImportPublication(library)
        ._copy_readonly(project)
    )
    from pathlib import Path

    assert game.engine_id == "ons"
    copied = Path(game.path)
    assert (copied / "save/slot1.dat").read_bytes() == b"native save"
    assert not (copied / "game").exists()
    assert (project / "save/slot1.dat").read_bytes() == b"native save"


def test_native_display_failure_stays_unready_and_retries(tmp_path, monkeypatch):
    from vn_stream.streaming.window_canvas import WindowCanvas

    calls = []

    def apply(w, h):
        calls.append((w, h))
        if len(calls) == 1:
            raise OSError("temporary display failure")
        return w, h

    monkeypatch.setattr(
        "vn_stream.streaming.window_canvas.window_geometry", lambda pid: ("0x1", 1280, 720)
    )
    monkeypatch.setattr("subprocess.run", lambda *args, **kw: None)
    canvas = WindowCanvas(tmp_path, "retry", apply=apply, now=0)
    canvas.pid = 100
    canvas.poll(1)
    assert canvas.status["ready"] is False
    canvas.poll(3)
    assert canvas.status["ready"] is True
    assert canvas.status["capture"] == [1280, 720]
    assert calls == [(1280, 720), (1920, 1080), (1280, 720)]


def test_native_window_refused_size_is_not_reported_as_measured(tmp_path, monkeypatch):
    from vn_stream.streaming.window_canvas import WindowCanvas

    monkeypatch.setattr(
        "vn_stream.streaming.window_canvas.window_geometry", lambda pid: ("0x1", 640, 480)
    )
    monkeypatch.setattr("subprocess.run", lambda *args, **kw: None)
    canvas = WindowCanvas(tmp_path, "refused", (1280, 720), apply=lambda w, h: (w, h), now=0)
    canvas.pid = 100
    canvas.poll(1)
    assert canvas.status["ready"] is False
    assert canvas.status.get("measurement") != "window_observed"


def test_overlapping_configured_roots_do_not_duplicate_browser_entries(tmp_path):
    from vn_stream.projects.catalog import ProjectCatalog

    root = tmp_path / "games"
    library = ProjectCatalog(
        tmp_path / "data", [root, root], library_root=root, discovery_roots=[root, root]
    )
    assert library.roots == [root]
    assert library.discovery_roots == [root]


def test_ons_inspection_matches_native_script_precedence(tmp_path):
    project = ons(tmp_path / "project")
    (project / "nscr_sec.dat").write_bytes(b"other encrypted format")
    result = inspect_project(project)
    assert result.engine_id == "ons"
    assert result.entry == "nscr_sec.dat" and result.status == "unsupported"
    (project / "0.txt").write_text('*define\nsavedir "save"\ngame\n')
    result = inspect_project(project)
    assert result.entry == "0.txt" and result.status == "detected"


def test_window_probe_failure_publishes_loss_of_ready_state(tmp_path, monkeypatch):
    from vn_stream.streaming.window_canvas import WindowCanvas

    probe = [("0x1", 640, 480)]

    def geometry(pid):
        if isinstance(probe[0], Exception):
            raise probe[0]
        return probe[0]

    monkeypatch.setattr("vn_stream.streaming.window_canvas.window_geometry", geometry)
    monkeypatch.setattr("subprocess.run", lambda *args, **kw: None)
    canvas = WindowCanvas(tmp_path, "lost", apply=lambda w, h: (w, h), now=0)
    canvas.pid = 100
    assert canvas.poll(1)
    assert canvas.status["ready"] is True
    probe[0] = OSError("temporary X server failure")
    assert canvas.poll(3)
    assert canvas.status["ready"] is False
    assert canvas.status["notice"]
    probe[0] = None
    canvas.poll(5)
    assert canvas.status["ready"] is False
    canvas.close()


def test_window_probe_failure_preserves_initial_display_deadline(tmp_path, monkeypatch):
    from vn_stream.streaming.window_canvas import WindowCanvas

    def geometry(pid):
        raise OSError("X server unavailable")

    monkeypatch.setattr("vn_stream.streaming.window_canvas.window_geometry", geometry)
    applied = []
    canvas = WindowCanvas(
        tmp_path, "deadline", apply=lambda w, h: applied.append((w, h)) or (w, h), now=0
    )
    canvas.pid = 100
    canvas.poll(1)
    assert canvas.poll(6)
    assert canvas.status["state"] == "fallback"
    assert applied == [(1920, 1080)]
    assert canvas.status["ready"] is False
    canvas.close()


def test_server_browser_uses_normalized_library_roots(tmp_path):
    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    games, native = tmp_path / "games", tmp_path / "ons"
    games.mkdir()
    native.mkdir()
    with TestClient(create_app(tmp_path / "data", [games, native, games], "secret")) as client:
        sign_in(client, "secret")
        roots = api_data(client.get("/api/v2/imports/sources"))["roots"]
        assert roots == [{"id": 0, "name": "games"}, {"id": 1, "name": "ons"}]
        assert client.get("/api/v2/imports/sources", params={"root": 1}).status_code == 200


def test_fourth_engine_runtime_and_capabilities_follow_registry(tmp_path, monkeypatch):
    from vn_stream.engines.contracts import Inspection, LaunchPlan
    from vn_stream.engines.registry import ADAPTERS, capabilities

    class FourthAdapter:
        engine_id = "fourth"
        directory_name = "fourth"
        display_name = "Test engine"
        capabilities = dict(
            assets=False,
            save_management=False,
            save_editing=False,
            save_migration=False,
            native_actions=False,
            exit_mode="stop",
            uses_original_engine_version=False,
        )

        def inspect_project(self, project):
            return (
                Inspection("fourth", "detected", "entry") if (project / "entry").exists() else None
            )

        def resolve_runtime(self, inspection, installed):
            selected = installed[0] if installed else None
            return dict(
                runtime_id=selected["id"] if selected else None,
                version=selected["version"] if selected else None,
                kind="profile" if selected else "unavailable",
            )

        def runtime_compatibility(self, project, inspection, runtime):
            if not (project / "compatible").exists():
                return {"code": "engine.runtimeUnavailable", "params": {}}

        def prepare_launch(self, project, inspection, runtime):
            return LaunchPlan([str(project / "entry")], {}, project, "fourth")

    monkeypatch.setitem(ADAPTERS, "fourth", FourthAdapter())
    folder = runtime(tmp_path / "data/runtimes/fourth/build", engine="fourth")
    manifest = json.loads((folder / "runtime.json").read_text())
    manifest["id"] = "fourth:test"
    (folder / "runtime.json").write_text(json.dumps(manifest))
    installed = available_runtimes(tmp_path / "data/runtimes", ADAPTERS)
    assert len(installed) == 1
    assert capabilities("fourth") == FourthAdapter.capabilities
    project = tmp_path / "project"
    project.mkdir()
    (project / "entry").write_text("#!/bin/sh\nsleep 30\n")
    (project / "entry").chmod(0o755)
    marker = project / "compatible"
    marker.touch()
    inspection = inspect_project(project)
    assert resolve_runtime(inspection, installed)["runtime_id"] == "fourth:test"
    launch = prepare_launch(project, inspection, installed[0])
    assert launch.engine_id == "fourth"
    assert "ALSOFT_CONF" not in launch.env
    assert "RENPY_SEARCHPATH" not in launch.env

    # Exercise the existing HTTP use case and real supervisor, not a separate
    # fourth-engine route or a mocked session starter.
    from tests.auth_helpers import TestClient, api_data, sign_in
    from vn_stream.api.app import create_app

    with TestClient(create_app(tmp_path / "data", [tmp_path], "secret")) as client:
        sign_in(client, "secret")
        registered = client.post("/api/v2/projects", json={"path": str(project)})
        assert registered.status_code == 201, registered.text
        record = api_data(registered)
        assert record["engine_id"] == "fourth"
        assert record["engine_display_name"] == "Test engine"
        assert record["engine_name_key"] is None
        assert not record["capabilities"]["uses_original_engine_version"]
        assert not record["capabilities"]["save_management"]
        assert record["runtime_options"] == [
            {
                "runtime_id": "fourth:test",
                "display_name": "yuri",
                "build_version": "test",
                "available": True,
                "reason": None,
            }
        ]
        assert record["recommendation"]["runtime_id"] == "fourth:test"
        project_url = f"/api/v2/projects/{record['project_id']}"
        marker.unlink()
        description = api_data(client.get(project_url))
        assert description["recommendation"]["runtime_id"] is None
        assert not description["runtime_options"][0]["available"]
        error = client.post(f"{project_url}/start", json={"runtime_id": "fourth:test"})
        assert error.status_code == 409
        assert error.json()["error"] == description["runtime_options"][0]["reason"]
        marker.touch()
        response = client.post(f"{project_url}/start", json={})
        assert response.status_code == 200, response.text
        assert api_data(response)["state"] == "running"
        assert api_data(response)["action_policy"] == "none"
        marker.unlink()
        resumed = client.post(f"{project_url}/start", json={})
        assert resumed.status_code == 200, resumed.text
        assert api_data(resumed)["session_id"] == api_data(response)["session_id"]
        assert not (project / "game/saves").exists()
        assert client.get(f"/api/v2/projects/{record['project_id']}/saves").status_code == 409
        assert client.post("/api/v2/session/stop").status_code == 200


def test_native_api_can_start_explicit_experiment_but_not_automatically(tmp_path):
    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    project = ons(tmp_path / "games/onscripter/native")
    runtime(tmp_path / "data/runtimes/onscripter/candidate", validation="experimental")
    with TestClient(create_app(tmp_path / "data", [tmp_path / "games"], "secret")) as client:
        sign_in(client, "secret")
        record = api_data(client.post("/api/v2/projects", json={"path": str(project)}))
        url = f"/api/v2/projects/{record['project_id']}/start"
        assert client.post(url, json={}).status_code == 409
        response = client.post(url, json={"runtime_id": "ons:yuri:test"})
        assert response.status_code == 200, response.text
        assert (
            api_data(client.get(f"/api/v2/projects/{record['project_id']}"))["runtime_override"]
            == "ons:yuri:test"
        )
        client.post("/api/v2/session/stop")


@pytest.mark.parametrize("flavor", ["official", "en", "gbk-2015"])
def test_retired_ons_profiles_cannot_prepare_launch(tmp_path, flavor):
    project = ons(tmp_path / "project")
    runtime(tmp_path / "runtimes/onscripter/native")
    installed = available_runtimes(tmp_path / "runtimes", ADAPTERS)[0]
    with pytest.raises(ValueError):
        prepare_launch(project, inspect_project(project), {**installed, "flavor": flavor})
    assert not (project / ".vn-stream/home").exists()


@pytest.mark.parametrize("flavor,encoding", [("zero", "utf8")])
def test_fixed_encoding_ons_profiles_reject_unsupported_scripts_before_preparation(
    tmp_path, flavor, encoding
):
    from dataclasses import replace

    project = ons(tmp_path / "project")
    runtime(tmp_path / "runtimes/onscripter/native")
    installed = available_runtimes(tmp_path / "runtimes", ADAPTERS)[0]
    inspection = replace(inspect_project(project), encoding=encoding)
    with pytest.raises(ValueError):
        prepare_launch(project, inspection, {**installed, "flavor": flavor})
    assert not (project / ".vn-stream/home").exists()


def test_bundled_runtimes_keep_local_default_and_explicit_candidates(tmp_path, monkeypatch):
    local = tmp_path / "data/runtimes"
    bundle = tmp_path / "bundle"
    monkeypatch.setenv("VN_STREAM_BUNDLED_RUNTIMES", str(bundle))

    def build(root, variant, *, validation="validated", default=False):
        path = runtime(root / "onscripter" / variant, validation=validation)
        manifest = path / "runtime.json"
        record = json.loads(manifest.read_text())
        record.update(id=f"ons:yuri:{variant}", default=default)
        manifest.write_text(json.dumps(record))
        return path

    installed = build(local, "existing", default=True)
    build(bundle, "existing", default=True)
    default = build(bundle, "bundled", default=True)
    candidate = build(bundle, "trial", validation="experimental")
    project = ons(tmp_path / "project")
    inspection = inspect_project(project)
    records = available_runtimes(local, ADAPTERS)
    by_id = {record["id"]: record for record in records}
    assert len(by_id) == 3
    assert by_id["ons:yuri:existing"]["path"] == str(installed)
    assert by_id["ons:yuri:bundled"]["path"] == str(default)
    assert by_id["ons:yuri:trial"]["path"] == str(candidate)
    assert resolve_runtime(inspection, records)["runtime_id"] == "ons:yuri:existing"
    assert prepare_launch(
        project, inspection, by_id["ons:yuri:trial"], allow_experimental=True
    ).command[0] == str(candidate / "runner")

    (installed / "runner").chmod(0o644)
    records = available_runtimes(local, ADAPTERS)
    assert {record["id"]: record["path"] for record in records}["ons:yuri:existing"] == str(
        bundle / "onscripter/existing"
    )


def test_bundled_sdk_is_exposed_by_v2_runtime_routes(tmp_path, monkeypatch):
    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    root = tmp_path / "bundled"
    sdk = root / "renpy/8.5.3"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text("version_tuple = (8, 5, 3, 0)")
    (sdk / "renpy.sh").write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("VN_STREAM_BUNDLED_RUNTIMES", str(root))
    data = tmp_path / "data"
    games = tmp_path / "games"
    with TestClient(create_app(data, [games], "secret")) as client:
        sign_in(client, "secret")
        assert [r["runtime_id"] for r in api_data(client.get("/api/v2/runtimes"))] == [
            "renpy:8.5.3"
        ]
        releases = api_data(client.get("/api/v2/runtimes/releases"))
        assert {r["version"] for r in releases if r["installed"]} == {"8.5.3"}
        assert not (data / "runtimes/renpy/8.5.3").exists()
