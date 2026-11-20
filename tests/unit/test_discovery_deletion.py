import time
from pathlib import Path

import pytest

from tests.auth_helpers import TestClient, api_data, sign_in
from vn_stream.api.app import create_app
from vn_stream.projects.catalog import ProjectCatalog


def project(path):
    (path / "game/saves").mkdir(parents=True)
    (path / "game/script.rpy").write_text("label start:\n    return\n")
    (path / "game/saves/slot.save").write_bytes(b"existing-save")
    return path


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "nas/renpy"
    root.mkdir(parents=True)
    data = root.parent / ".service"
    with TestClient(create_app(data, [root], "secret", library_root=root.parent)) as c:
        sign_in(c, "secret")
        yield c, root, data


def test_refresh_discovers_nested_projects_once_and_moves_keep_identity(setup):
    c, root, _ = setup
    first = project(root / "One")
    project(root / "category/Two")
    project(root / ".import-pending/Hidden")
    (root / "Link").symlink_to(first, target_is_directory=True)
    assert api_data(c.get("/api/v2/projects")) == []
    r = c.post("/api/v2/projects/refresh")
    assert r.status_code == 200
    assert len(api_data(r)) == 2
    assert r.headers["x-discovery-added"] == "2"
    one = next(g for g in api_data(r) if g["name"] == "One")
    assert len(api_data(c.post("/api/v2/projects/refresh"))) == 2
    moved = root / "Moved"
    first.rename(moved)
    response = c.post("/api/v2/projects/refresh")
    assert next(g for g in api_data(response) if g["project_id"] == one["project_id"])[
        "path"
    ] == str(moved)


def test_discovery_limits_and_stops_inside_project(tmp_path):
    root = tmp_path / "nas"
    root.mkdir()
    lib = ProjectCatalog(tmp_path / "data", [root], library_root=root)
    project(root / "renpy/Outer")
    project(root / "renpy/Outer/nested/Inner")
    project(root / "a/b/c/d/TooDeep")
    result = lib.discover()
    assert result["added"] == 1
    assert result["truncated"] is True
    assert len(lib.list()) == 1
    result = lib.discover(max_directories=1)
    assert result["truncated"] is True


def wait_job(c, job):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = api_data(c.get("/api/v2/tasks/project-deletions/" + job["task_id"]))
        if job["state"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("deletion did not finish")


def test_delete_entire_project_keeps_independent_backup_and_is_idempotent(setup):
    c, root, data = setup
    p = project(root / "ToDelete")
    game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
    backup = data / "backups" / game["project_id"] / "kept"
    backup.mkdir(parents=True)
    (backup / "slot.save").write_bytes(b"backup")
    r = c.delete("/api/v2/projects/" + game["project_id"])
    assert r.status_code == 202
    done = wait_job(c, api_data(r))
    assert done["state"] == "completed"
    assert not p.exists()
    assert (backup / "slot.save").read_bytes() == b"backup"
    assert api_data(c.get("/api/v2/projects")) == []
    assert (
        api_data(c.delete("/api/v2/projects/" + game["project_id"]))["task_id"] == done["task_id"]
    )
    assert api_data(c.post("/api/v2/projects/refresh")) == []


def test_delete_refuses_root_and_parent_of_other_game(setup):
    c, root, _ = setup
    p = project(root / "Parent")
    child = project(p / "Nested")
    parent_game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
    c.post("/api/v2/projects", json={"path": str(child)})
    assert c.delete("/api/v2/projects/" + parent_game["project_id"]).status_code == 409
    assert p.is_dir()


def test_delete_requires_auth_and_csrf(setup):
    c, root, _ = setup
    p = project(root / "Kept")
    game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
    c.headers.pop("X-CSRF-Token")
    assert c.delete("/api/v2/projects/" + game["project_id"]).status_code == 403
    c.cookies.clear()
    assert c.delete("/api/v2/projects/" + game["project_id"]).status_code == 401
    assert p.exists()


def test_delete_failure_keeps_identity_and_retry_recovers(setup, monkeypatch):
    import shutil

    c, root, _ = setup
    p = project(root / "Retry")
    game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
    original = shutil.rmtree

    def fail_quarantine(path, *args, **kwargs):
        if Path(path).name.startswith(".delete-"):
            raise PermissionError("fixture permission denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_quarantine)
    job = api_data(c.delete("/api/v2/projects/" + game["project_id"]))
    failed = wait_job(c, job)
    assert failed["state"] == "failed"
    assert not p.exists()
    assert c.post("/api/v2/projects", json={"path": str(p.parent / (".delete-" + failed["task_id"]))}).status_code == 400
    assert c.post("/api/v2/projects/" + game["project_id"] + "/start", json={}).status_code in (
        400,
        409,
    )
    assert len(api_data(c.post("/api/v2/projects/refresh"))) == 1
    monkeypatch.setattr(shutil, "rmtree", original)
    retried = c.post("/api/v2/tasks/project-deletions/" + job["task_id"] + "/retry")
    assert wait_job(c, api_data(retried))["state"] == "completed"


def test_failed_deletion_identity_cannot_be_rebound_by_restored_copy(setup, monkeypatch):
    import shutil

    c, root, _ = setup
    source = project(root / "OriginalPendingDelete")
    game = api_data(c.post("/api/v2/projects", json={"path": str(source)}))
    restored = root / ".restored-copy"
    shutil.copytree(source, restored)
    original = shutil.rmtree

    def fail(path, *args, **kwargs):
        if Path(path).name.startswith(".delete-"):
            raise PermissionError("fixture")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail)
    job = api_data(c.delete("/api/v2/projects/" + game["project_id"]))
    assert wait_job(c, job)["state"] == "failed"
    response = c.post("/api/v2/projects", json={"path": str(restored)})
    assert response.status_code == 400
    indexed = api_data(c.get("/api/v2/projects/" + game["project_id"]))
    assert indexed["path"] == str(source)
    restored.rename(root / "RestoredCopy")
    refresh = c.post("/api/v2/projects/refresh")
    assert int(refresh.headers["x-discovery-warnings"]) >= 1
    assert len(api_data(refresh)) == 1
    assert api_data(refresh)[0]["path"] == str(source)
    monkeypatch.setattr(shutil, "rmtree", original)
    retry = c.post("/api/v2/tasks/project-deletions/" + job["task_id"] + "/retry")
    assert wait_job(c, api_data(retry))["state"] == "completed"
    assert (root / "RestoredCopy/game/saves/slot.save").exists()


def test_discovery_identity_conflict_does_not_duplicate(setup):
    import shutil

    c, root, _ = setup
    p = project(root / "Original")
    c.post("/api/v2/projects/refresh")
    shutil.copytree(p, root / "Copy")
    response = c.post("/api/v2/projects/refresh")
    assert len(api_data(response)) == 1
    assert int(response.headers["x-discovery-warnings"]) >= 1


def test_delete_running_and_recovering_game_are_rejected(setup, monkeypatch):
    from vn_stream.sessions.supervisor import SessionManager

    c, root, _ = setup
    p = project(root / "Active")
    game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
    for state in ("running", "recovering"):
        monkeypatch.setattr(
            SessionManager,
            "status",
            lambda self, state=state: {"state": state, "project_id": game["project_id"]},
        )
        assert c.delete("/api/v2/projects/" + game["project_id"]).status_code == 409
        assert p.is_dir()


def test_failed_bound_import_blocks_delete_until_abandoned(setup):
    import json
    import sqlite3

    c, root, data = setup
    p = project(root / "Bound")
    game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
    db = sqlite3.connect(data / "database/vn-stream.sqlite3")
    record = {
        "id": "f" * 32,
        "request_id": "pending-fixture",
        "source": str(p),
        "state": "failed",
        "abandoned": False,
    }
    db.execute(
        "INSERT INTO import_jobs(id,request_id,state,record) VALUES(?,?,?,?)",
        (record["id"], record["request_id"], "failed", json.dumps(record)),
    )
    db.commit()
    db.close()
    assert c.delete("/api/v2/projects/" + game["project_id"]).status_code == 409
    assert p.is_dir()


def test_start_during_failed_deletion_returns_conflict(setup, monkeypatch):
    import shutil

    c, root, _ = setup
    p = project(root / "Conflict")
    game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
    original = shutil.rmtree

    def fail(path, *args, **kwargs):
        if Path(path).name.startswith(".delete-"):
            raise PermissionError("fixture")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail)
    job = api_data(c.delete("/api/v2/projects/" + game["project_id"]))
    assert wait_job(c, job)["state"] == "failed"
    assert c.post("/api/v2/projects/" + game["project_id"] + "/start", json={}).status_code == 409


def test_quarantined_archive_source_cannot_be_reimported(setup, monkeypatch):
    import shutil
    import zipfile

    c, root, _ = setup
    p = project(root / "ArchiveConflict")
    with zipfile.ZipFile(p / "source.zip", "w") as archive:
        archive.writestr("game/script.rpy", "label start:\n    return")
    game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
    original = shutil.rmtree

    def fail(path, *args, **kwargs):
        if Path(path).name.startswith(".delete-"):
            raise PermissionError("fixture")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail)
    job = wait_job(c, api_data(c.delete("/api/v2/projects/" + game["project_id"])))
    assert job["state"] == "failed"
    result = c.post("/api/v2/projects", json={"path": str(p.parent / (".delete-" + job["task_id"]) / "source.zip")})
    assert result.status_code == 400
    assert len(api_data(c.get("/api/v2/projects"))) == 1


@pytest.mark.parametrize("already_renamed", [False, True])
def test_deletion_recovers_prepared_record_after_service_restart(setup, already_renamed):
    from uuid import uuid4

    c, root, data = setup
    p = project(root / "Recovery")
    game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
    info = p.stat()
    uid = uuid4().hex
    q = p.parent / (".delete-" + uid)
    record = dict(
        id=uid,
        project_id=game["project_id"],
        name=game["name"],
        path=str(p),
        quarantine=str(q),
        identity=[info.st_dev, info.st_ino],
        state="running",
        phase="prepared",
        error=None,
    )
    c.app.state.deletions.save(record)
    if already_renamed:
        p.rename(q)
    with TestClient(create_app(data, [root], "secret", library_root=root.parent)) as restarted:
        sign_in(restarted, "secret")
        assert wait_job(restarted, {"task_id": record["id"]})["state"] == "completed"
        assert not p.exists() and not q.exists()
        assert api_data(restarted.get("/api/v2/projects")) == []


def test_restored_deleted_manifest_gets_fresh_identity(setup):
    c, root, _ = setup
    p = project(root / "Restored")
    game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
    manifest = (p / ".vn-stream/project.json").read_text()
    assert (
        wait_job(c, api_data(c.delete("/api/v2/projects/" + game["project_id"])))["state"]
        == "completed"
    )
    project(p)
    (p / ".vn-stream").mkdir()
    (p / ".vn-stream/project.json").write_text(manifest)
    result = api_data(c.post("/api/v2/projects/refresh"))
    assert len(result) == 1 and result[0]["project_id"] != game["project_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = api_data(c.get(f"/api/v2/projects/{result[0]['project_id']}/saves/signing"))[
            "state"
        ]
        if state == "completed":
            break
        time.sleep(0.01)
    assert state == "completed"
    assert (
        wait_job(c, api_data(c.delete("/api/v2/projects/" + result[0]["project_id"])))["state"]
        == "completed"
    )


def test_new_identity_write_failure_preserves_existing_manifest_and_home(setup, monkeypatch):
    c, root, data = setup
    p = project(root / "WriteFailure")
    c.post("/api/v2/projects", json={"path": str(p)})
    meta = p / ".vn-stream"
    (meta / "home").mkdir()
    (meta / "home/kept").write_text("progress")
    previous = (meta / "project.json").read_text()
    write = Path.write_text

    def fail(path, *args, **kwargs):
        if path.parent == meta and path.name.startswith("manifest"):
            raise PermissionError("fixture")
        return write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail)
    lib = ProjectCatalog(data, [root], library_root=root)
    with pytest.raises(OSError):
        lib.prepare_project(p, "new", new_identity=True)
    assert (meta / "project.json").read_text() == previous
    assert (meta / "home/kept").read_text() == "progress"


def test_discovery_default_managed_root_is_not_excluded_as_service_data(tmp_path):
    lib = ProjectCatalog(tmp_path / "data", [tmp_path / "nas"])
    project(lib.library_root / "renpy/Dropped")
    assert lib.discover()["added"] == 1
