import io
import os
import threading
import time
import zipfile

from tests.auth_helpers import TestClient, api_data, sign_in
from vn_stream.api.app import create_app


def content():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("Story/game/script.rpy", "label start:\n return")
    return payload.getvalue()


def upload(client, payload):
    response = client.post(
        "/api/v2/uploads/archives", json={"filename": "Story.zip", "size_bytes": len(payload)}
    )
    assert response.status_code == 201, response.text
    uid = api_data(response)["upload_id"]
    assert (
        client.put(f"/api/v2/uploads/archives/{uid}?offset_bytes=0", content=payload).status_code
        == 200
    )
    return uid


def settle(client, uid):
    for _ in range(300):
        result = client.get(f"/api/v2/tasks/imports/{uid}")
        assert result.status_code == 200
        if api_data(result)["state"] != "running":
            return api_data(result)
        time.sleep(0.005)
    raise AssertionError("worker did not finish")


def test_async_upload_binding_status_and_no_blocked_catalog(tmp_path, monkeypatch):
    from vn_stream.imports import publication as service

    entered, release = threading.Event(), threading.Event()
    original = service.extract_archive

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "extract_archive", slow)
    data = tmp_path / "data"
    with TestClient(create_app(data, [], "test", library_root=tmp_path / "library")) as client:
        sign_in(client, "test")
        uid = upload(client, content())
        response = client.post("/api/v2/imports", json={"request_id": "one", "upload_id": uid})
        assert response.status_code == 202
        job = api_data(response)
        assert job["delete_source"] is False
        assert entered.wait(1)
        try:
            assert api_data(client.get("/api/v2/imports/active"))["task_id"] == job["task_id"]
            assert api_data(client.get("/api/v2/projects")) == []
            assert (
                api_data(client.get(f"/api/v2/tasks/imports/{job['task_id']}"))["state"]
                == "running"
            )
            duplicate = client.post("/api/v2/imports", json={"request_id": "two", "upload_id": uid})
            assert duplicate.status_code == 202 and api_data(duplicate)["task_id"] == job["task_id"]
            assert client.delete(f"/api/v2/uploads/archives/{uid}").status_code == 409
            assert (
                client.put(
                    f"/api/v2/uploads/archives/{uid}?offset_bytes=0", content=b""
                ).status_code
                == 409
            )
            assert client.delete(f"/api/v2/tasks/imports/{job['task_id']}").status_code == 409
            os.utime(data / "uploads/archives" / uid, (1, 1))
            assert (
                client.post(
                    "/api/v2/uploads/archives", json={"filename": "Other.zip", "size_bytes": 1}
                ).status_code
                == 201
            )
            assert (data / "uploads/archives" / uid / "content").exists()
        finally:
            release.set()
        finished = settle(client, job["task_id"])
        assert finished["state"] == "completed", finished
        assert finished["completed_bytes"] == finished["total_bytes"]
        assert not (data / "uploads/archives" / uid).exists()
        assert api_data(client.get("/api/v2/imports/active")) is None
        detail = api_data(client.get("/api/v2/projects/" + finished["project"]["project_id"]))
        assert finished["project"] == detail
        # The same request remains idempotent after bytes have been cleaned up.
        assert (
            api_data(client.post("/api/v2/imports", json={"request_id": "one", "upload_id": uid}))[
                "task_id"
            ]
            == job["task_id"]
        )


def test_failure_retry_abandon_and_validation_do_not_expose_passwords(tmp_path):
    data = tmp_path / "data"
    with TestClient(create_app(data, [tmp_path], "test")) as client:
        assert client.get("/api/v2/imports/active").status_code == 401
        sign_in(client, "test")
        response = client.post(
            "/api/v2/imports", json={"request_id": "invalid", "password": ["hidden-password"]}
        )
        assert response.status_code == 422 and "hidden-password" not in response.text
        assert client.post("/api/v2/imports", json={"request_id": "none"}).status_code == 422
        assert (
            client.post(
                "/api/v2/imports", json={"request_id": "both", "path": "/a", "upload_id": "a" * 32}
            ).status_code
            == 422
        )
        uid = upload(client, b"corrupt")
        job = api_data(
            client.post(
                "/api/v2/imports",
                json={"request_id": "one", "upload_id": uid, "password": "hidden-password"},
            )
        )
        failed = settle(client, job["task_id"])
        assert failed["state"] == "failed"
        assert "hidden-password" not in str(failed)
        assert api_data(client.get("/api/v2/imports/active"))["task_id"] == job["task_id"]
        assert client.delete(f"/api/v2/uploads/archives/{uid}").status_code == 409
        assert (
            client.post(f"/api/v2/tasks/imports/{job['task_id']}/retry", json={}).status_code == 202
        )
        assert settle(client, job["task_id"])["state"] == "failed"
        assert api_data(client.delete(f"/api/v2/tasks/imports/{job['task_id']}")) == {
            "abandoned": True
        }
        assert not (data / "uploads/archives" / uid).exists()
        assert api_data(client.get("/api/v2/imports/active")) is None
        assert (
            client.post(f"/api/v2/tasks/imports/{job['task_id']}/retry", json={}).status_code == 409
        )
        assert client.get("/api/v2/tasks/imports/missing").status_code == 404
        assert b"hidden-password" not in (data / "database/vn-stream.sqlite3").read_bytes()


def test_directory_deletion_is_rejected_and_metadata_failure_is_warning(tmp_path, monkeypatch):

    root = tmp_path / "Story"
    (root / "game").mkdir(parents=True)
    (root / "game/script.rpy").write_text("pass")
    with TestClient(create_app(tmp_path / "data", [tmp_path], "test")) as client:
        sign_in(client, "test")
        assert (
            client.post(
                "/api/v2/imports",
                json={"request_id": "bad", "path": str(root), "delete_source": True},
            ).status_code
            == 409
        )

        def fail(*args, **kwargs):
            raise OSError("metadata failure")

        monkeypatch.setattr(
            __import__("vn_stream.projects.queries", fromlist=["game_info"]), "game_info", fail
        )
        job = api_data(client.post("/api/v2/imports", json={"request_id": "ok", "path": str(root)}))
        finished = settle(client, job["task_id"])
        assert finished["state"] == "completed"
        assert finished["project"]["project_id"] and finished["warning"]
        assert root.exists()
