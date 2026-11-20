import io
import zipfile

from tests.auth_helpers import TestClient, api_data, sign_in
from vn_stream.api.app import create_app
from vn_stream.imports.publication import import_project


def test_browser_confines_paths_and_upload_retry(tmp_path):
    root = tmp_path / "nas"
    root.mkdir()
    (root / "folder").mkdir()
    (root / "secret-link").symlink_to("/etc")
    with TestClient(create_app(tmp_path / "data", [root], "test")) as client:
        sign_in(client, "test")
        listed = api_data(client.get("/api/v2/imports/sources"))
        assert listed["roots"]
        entries = api_data(client.get("/api/v2/imports/sources", params={"root": 0}))["entries"]
        assert any(e["name"] == "folder" for e in entries)
        assert not any(e["name"] == "secret-link" for e in entries)
        assert (
            client.get("/api/v2/imports/sources", params={"root": 0, "path": "../"}).status_code
            == 400
        )
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("Demo/game/script.rpy", "label start:\n return")
        content = payload.getvalue()
        created = client.post(
            "/api/v2/uploads/archives", json={"filename": "Demo.zip", "size_bytes": len(content)}
        )
        assert created.status_code == 201
        uid = api_data(created)["upload_id"]
        assert (
            client.put(
                f"/api/v2/uploads/archives/{uid}", params={"offset_bytes": 1}, content=content
            ).status_code
            == 409
        )
        assert (
            client.put(
                f"/api/v2/uploads/archives/{uid}", params={"offset_bytes": 0}, content=content
            ).status_code
            == 200
        )
        imported = client.post(f"/api/v2/uploads/archives/{uid}/import", json={})
        assert imported.status_code == 201
        assert api_data(imported)["project_id"]
        assert client.get(f"/api/v2/uploads/archives/{uid}").status_code == 404


def test_readonly_folder_is_copied_with_saves(tmp_path, monkeypatch):
    from vn_stream.projects.catalog import ProjectCatalog

    root = tmp_path / "nas"
    project = root / "Story"
    (project / "game/saves").mkdir(parents=True)
    (project / "game/script.rpy").write_text("label start:\n return")
    (project / "game/saves/persistent").write_bytes(b"keep-me")
    monkeypatch.setattr("vn_stream.projects.catalog.os.access", lambda *_: False)
    library = ProjectCatalog(tmp_path / "data", [root])
    game = import_project(library, project)
    from pathlib import Path

    assert Path(game.path) != project
    assert (Path(game.path) / "game/saves/persistent").read_bytes() == b"keep-me"
    assert not (project / ".vn-stream").exists()


def test_upload_limits_cancel_and_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("VN_STREAM_UPLOAD_MAX_BYTES", "10")
    with TestClient(create_app(tmp_path / "data", [tmp_path / "nas"], "test")) as client:
        assert client.get("/api/v2/imports/sources").status_code == 401
        assert (
            client.post(
                "/api/v2/uploads/archives", json={"filename": "a.zip", "size_bytes": 1}
            ).status_code
            == 401
        )
        sign_in(client, "test")
        assert (
            client.post(
                "/api/v2/uploads/archives", json={"filename": "../a.zip", "size_bytes": 1}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/v2/uploads/archives", json={"filename": "a.ipa", "size_bytes": 1}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/v2/uploads/archives", json={"filename": "a.zip", "size_bytes": 11}
            ).status_code
            == 413
        )
        uid = api_data(
            client.post("/api/v2/uploads/archives", json={"filename": "a.zip", "size_bytes": 10})
        )["upload_id"]
        assert (
            client.put(
                f"/api/v2/uploads/archives/{uid}?offset_bytes=0", content=b"a" * 11
            ).status_code
            == 413
        )
        assert api_data(client.get(f"/api/v2/uploads/archives/{uid}"))["offset_bytes"] == 0
        assert client.post(f"/api/v2/uploads/archives/{uid}/import", json={}).status_code == 409
        assert client.delete(f"/api/v2/uploads/archives/{uid}").status_code == 200
        assert client.get(f"/api/v2/uploads/archives/{uid}").status_code == 404


def test_password_retry_keeps_upload_and_hides_password(tmp_path):
    import pyzipper

    payload = io.BytesIO()
    with pyzipper.AESZipFile(payload, "w", encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(b"correct")
        archive.writestr("Story/game/script.rpy", "label start:\n return")
    content = payload.getvalue()
    data = tmp_path / "data"
    with TestClient(create_app(data, [], "test")) as client:
        sign_in(client, "test")
        uid = api_data(
            client.post(
                "/api/v2/uploads/archives",
                json={"filename": "Story.zip", "size_bytes": len(content)},
            )
        )["upload_id"]
        client.put(f"/api/v2/uploads/archives/{uid}?offset_bytes=0", content=content)
        failed = client.post(
            f"/api/v2/uploads/archives/{uid}/import", json={"password": "sensitive-wrong"}
        )
        assert failed.status_code == 400
        assert "sensitive-wrong" not in failed.text
        assert api_data(client.get(f"/api/v2/uploads/archives/{uid}"))["offset_bytes"] == len(
            content
        )
        assert api_data(client.get("/api/v2/projects")) == []
        assert (
            client.post(
                f"/api/v2/uploads/archives/{uid}/import", json={"password": "correct"}
            ).status_code
            == 201
        )
        for path in data.rglob("*.json"):
            assert "correct" not in path.read_text() and "sensitive-wrong" not in path.read_text()


def test_metadata_cache_failure_does_not_fail_successful_import(tmp_path, monkeypatch):
    from pathlib import Path

    root = tmp_path / "nas"
    (root / "Story/game").mkdir(parents=True)
    (root / "Story/game/options.rpy").write_text('define config.name = "Story"')
    original = Path.write_text

    def fail_cache(path, *args, **kwargs):
        if path.parent.name == "metadata":
            raise OSError("metadata cache volume unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_cache)
    with TestClient(create_app(tmp_path / "data", [root], "test")) as client:
        sign_in(client, "test")
        assert (
            client.post("/api/v2/projects", json={"path": str(root / "Story")}).status_code == 201
        )
        result = client.get("/api/v2/projects")
        assert result.status_code == 200
        assert api_data(result)[0]["name"] == "Story"
