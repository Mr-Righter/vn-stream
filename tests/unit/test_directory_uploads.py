"""Directory transfer boundaries: resumable bytes must publish once and preserve saves."""

from pathlib import Path
from threading import Event

import pytest

from tests.auth_helpers import TestClient, api_data, sign_in
from vn_stream.api.app import create_app


def client_for(tmp_path):
    client = TestClient(create_app(tmp_path / "data", [], "test", library_root=tmp_path / "games"))
    sign_in(client, "test")
    return client


def create(client, files):
    response = client.post(
        "/api/v2/uploads/directories",
        json={
            "name": "Story",
            "file_count": len(files),
            "total_bytes": sum(f["size_bytes"] for f in files),
        },
    )
    assert response.status_code == 201, response.text
    uid = api_data(response)["upload_id"]
    response = client.post(
        f"/api/v2/uploads/directories/{uid}/manifest",
        json={
            "batch_id": "first",
            "files": files,
        },
    )
    assert response.status_code == 200, response.text
    return uid, api_data(response)["files"]


def settle(client, uid):
    return client.app.state.import_jobs.wait(uid)


def test_restart_offsets_manifest_replay_and_atomic_publish_preserve_every_file(tmp_path):
    files = [
        {"path": "game/script.rpy", "size_bytes": 4},
        {"path": "game/saves/persistent", "size_bytes": 4},
        {"path": ".hidden", "size_bytes": 0},
    ]
    with client_for(tmp_path) as client:
        uid, entries = create(client, files)
        url = f"/api/v2/uploads/directories/{uid}"
        replay = client.post(url + "/manifest", json={"batch_id": "first", "files": files})
        assert api_data(replay)["files"] == entries
        assert client.post(url + "/complete").status_code == 409
        assert client.post(url + "/seal").status_code == 200
        file_url = url + "/files/" + entries[0]["file_id"]
        assert (
            api_data(client.put(file_url + "?offset_bytes=0", content=b"pa"))["offset_bytes"] == 2
        )
        assert client.put(file_url + "?offset_bytes=0", content=b"ss").status_code == 409
    with client_for(tmp_path) as client:
        page = api_data(client.get(url + "?limit=1"))
        assert page["files"][0]["offset_bytes"] == 2 and page["next_cursor"] == 1
        assert (
            api_data(client.put(file_url + "?offset_bytes=2", content=b"ss"))["offset_bytes"] == 4
        )
        assert (
            client.put(
                url + "/files/" + entries[1]["file_id"] + "?offset_bytes=0", content=b"save"
            ).status_code
            == 200
        )
        assert api_data(client.post(url + "/complete"))["state"] == "complete"
        response = client.post(
            "/api/v2/imports", json={"request_id": "first", "directory_upload_id": uid}
        )
        assert response.status_code == 202, response.text
        job = settle(client, api_data(response)["task_id"])
        assert job["state"] == "completed", job
        root = Path(job["project"]["path"])
        assert root.parent == tmp_path / "games/renpy" and not root.name.startswith(".")
        assert (root / "game/script.rpy").read_bytes() == b"pass"
        assert (root / "game/saves/persistent").read_bytes() == b"save"
        assert (root / ".hidden").read_bytes() == b""
        assert client.get(url).status_code == 404
        duplicate = client.post(
            "/api/v2/imports", json={"request_id": "again", "directory_upload_id": uid}
        )
        assert api_data(duplicate)["task_id"] == job["id"]
        assert len(api_data(client.get("/api/v2/projects"))) == 1


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "C:/file",
        "a\\b",
        "a//b",
        "a/./b",
        "game/../x",
        ".vn-stream/x",
        "game/.vn-stream/x",
        "x\x00y",
        "x\ny",
        "/".join(["dir"] * 33 + ["f"]),
    ],
)
def test_manifest_rejects_unsafe_paths_without_echoing_input(tmp_path, path):
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/v2/uploads/directories", json={"name": "Story", "file_count": 1, "total_bytes": 0}
        )
        assert response.status_code == 201
        uid = api_data(response)["upload_id"]
        response = client.post(
            f"/api/v2/uploads/directories/{uid}/manifest",
            json={"batch_id": "a", "files": [{"path": path, "size_bytes": 0}]},
        )
        assert response.status_code == 400
        assert "escape" not in response.text
        assert api_data(client.get(f"/api/v2/uploads/directories/{uid}"))["manifest_count"] == 0


@pytest.mark.parametrize("second", ["game", "game/a", "game/a/child"])
def test_cross_batch_duplicates_and_parent_file_conflicts_are_atomic(tmp_path, second):
    with client_for(tmp_path) as client:
        uid, _ = create(client, [{"path": "game/a", "size_bytes": 0}])
        response = client.post(
            f"/api/v2/uploads/directories/{uid}/manifest",
            json={"batch_id": "next", "files": [{"path": second, "size_bytes": 0}]},
        )
        assert response.status_code in (400, 409)
        assert api_data(client.get(f"/api/v2/uploads/directories/{uid}"))["manifest_count"] == 1


def test_limits_auth_same_origin_and_validation_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("VN_STREAM_UPLOAD_MAX_BYTES", "10")
    with client_for(tmp_path) as client:
        client.cookies.clear()
        assert client.post("/api/v2/uploads/directories", json={}).status_code == 401
        sign_in(client, "test")
        assert (
            client.post(
                "/api/v2/uploads/directories",
                json={},
                headers={"origin": "https://foreign.invalid"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v2/uploads/directories",
                json={"name": "Story", "file_count": 100001, "total_bytes": 0},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v2/uploads/directories",
                json={"name": "Story", "file_count": 1, "total_bytes": 11},
            ).status_code
            == 413
        )
        uid, files = create(client, [{"path": "game/script.rpy", "size_bytes": 10}])
        url = f"/api/v2/uploads/directories/{uid}"
        assert (
            client.post(
                "/api/v2/uploads/directories",
                json={"name": "Other", "file_count": 1, "total_bytes": 0},
            ).status_code
            == 409
        )
        assert (
            client.post(
                url + "/manifest",
                json={
                    "batch_id": "huge",
                    "files": [{"path": str(i), "size_bytes": 0} for i in range(1001)],
                },
            ).status_code
            == 422
        )
        assert client.post(url + "/seal").status_code == 200
        assert (
            client.put(
                url + "/files/" + files[0]["file_id"] + "?offset_bytes=0", content=b"x" * 11
            ).status_code
            == 413
        )
        assert api_data(client.get(url))["files"][0]["offset_bytes"] == 0
        secret = "private-credential"
        response = client.post(
            "/api/v2/imports",
            json={
                "request_id": "invalid",
                "directory_upload_id": uid,
                "upload_id": "a" * 32,
                "password": secret,
            },
        )
        assert response.status_code == 422 and secret not in response.text
        assert client.delete(url).status_code == 200
        assert client.get(url).status_code == 404


def test_manifest_counts_size_and_batch_identity_are_checked_before_sealing(tmp_path):
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/v2/uploads/directories", json={"name": "Story", "file_count": 2, "total_bytes": 4}
        )
        uid = api_data(response)["upload_id"]
        url = f"/api/v2/uploads/directories/{uid}"
        first = {"batch_id": "one", "files": [{"path": "game/script.rpy", "size_bytes": 4}]}
        assert client.post(url + "/manifest", json=first).status_code == 200
        assert client.post(url + "/seal").status_code == 409
        changed = {"batch_id": "one", "files": [{"path": "other", "size_bytes": 4}]}
        assert client.post(url + "/manifest", json=changed).status_code == 409
        too_large = {"batch_id": "two", "files": [{"path": "extra", "size_bytes": 1}]}
        assert client.post(url + "/manifest", json=too_large).status_code == 409
        valid = {"batch_id": "two", "files": [{"path": "extra", "size_bytes": 0}]}
        assert client.post(url + "/manifest", json=valid).status_code == 200
        assert client.post(url + "/seal").status_code == 200
        assert client.post(url + "/manifest", json=first).status_code == 200
        assert (
            client.post(
                url + "/manifest",
                json={"batch_id": "three", "files": [{"path": "another", "size_bytes": 0}]},
            ).status_code
            == 409
        )


def test_eight_mib_chunks_and_crash_tail_recovery(tmp_path):
    with client_for(tmp_path) as client:
        uid, files = create(client, [{"path": "game/script.rpy", "size_bytes": 8 * 1024**2 + 1}])
        url = f"/api/v2/uploads/directories/{uid}"
        assert client.post(url + "/seal").status_code == 200
        file_url = url + "/files/" + files[0]["file_id"]
        assert (
            client.put(file_url + "?offset_bytes=0", content=b"a" * (8 * 1024**2 + 1)).status_code
            == 413
        )
        assert api_data(client.get(url))["uploaded_bytes"] == 0
        assert (
            api_data(client.put(file_url + "?offset_bytes=0", content=b"a" * (8 * 1024**2)))[
                "offset_bytes"
            ]
            == 8 * 1024**2
        )
        source = tmp_path / "games/.vn-stream-uploads" / uid / "project/game/script.rpy"
        with source.open("ab") as writer:
            writer.write(b"unacknowledged crash tail")
    with client_for(tmp_path) as client:
        assert (
            client.put(file_url + f"?offset_bytes={8 * 1024**2}", content=b"b").status_code == 200
        )
        assert source.stat().st_size == 8 * 1024**2 + 1
        assert client.post(url + "/complete").status_code == 200


@pytest.mark.parametrize("after_rename", [False, True])
def test_directory_publication_restart_recovers_without_double_publish(
    tmp_path, monkeypatch, after_rename
):
    from vn_stream.projects.catalog import ProjectCatalog

    with client_for(tmp_path) as client:
        uid, files = create(client, [{"path": "game/script.rpy", "size_bytes": 4}])
        url = f"/api/v2/uploads/directories/{uid}"
        assert client.post(url + "/seal").status_code == 200
        assert (
            client.put(
                url + "/files/" + files[0]["file_id"] + "?offset_bytes=0", content=b"pass"
            ).status_code
            == 200
        )
        assert client.post(url + "/complete").status_code == 200
        from vn_stream.imports.publication import ImportPublication

        owner = ImportPublication if after_rename else ProjectCatalog
        original = owner.record if after_rename else owner.rename_new

        def interruption(*args, **kwargs):
            raise SystemExit("simulated interruption")

        monkeypatch.setattr(owner, "record" if after_rename else "rename_new", interruption)
        response = client.post(
            "/api/v2/imports", json={"request_id": "crash", "directory_upload_id": uid}
        )
        job_id = api_data(response)["task_id"]
        client.app.state.import_jobs.wait(job_id)
        if after_rename:
            monkeypatch.setattr(owner, "record", original)
        else:
            monkeypatch.setattr(ProjectCatalog, "rename_new", staticmethod(original))
    with client_for(tmp_path) as client:
        job = api_data(client.get("/api/v2/tasks/imports/" + job_id))
        if not after_rename:
            assert job["state"] == "failed"
            assert client.delete(url).status_code == 409
            assert (
                client.post("/api/v2/tasks/imports/" + job_id + "/retry", json={}).status_code
                == 202
            )
            job = settle(client, job_id)
        assert job["state"] == "completed", job
        assert job["completed_bytes"] == job["total_bytes"] == 4
        assert Path(job["project"]["path"]).parent == tmp_path / "games/renpy"
        assert client.get(url).status_code == 404
        assert len(api_data(client.get("/api/v2/projects"))) == 1
        assert len([p for p in (tmp_path / "games").iterdir() if not p.name.startswith(".")]) == 1


def test_failed_directory_retains_bytes_through_ttl_and_abandon_only_removes_staging(tmp_path):
    with client_for(tmp_path) as client:
        uid, files = create(client, [{"path": "game/unknown.bin", "size_bytes": 4}])
        url = f"/api/v2/uploads/directories/{uid}"
        client.post(url + "/seal")
        client.put(url + "/files/" + files[0]["file_id"] + "?offset_bytes=0", content=b"save")
        client.post(url + "/complete")
        response = client.post(
            "/api/v2/imports", json={"request_id": "bad", "directory_upload_id": uid}
        )
        job_id = api_data(response)["task_id"]
        assert settle(client, job_id)["state"] == "failed"
        manager = client.app.state.import_jobs
        with manager.library.db.transaction() as db:
            db.execute("UPDATE directory_uploads SET updated = 1 WHERE id = ?", (uid,))
        assert (
            client.post(
                "/api/v2/uploads/directories",
                json={"name": "Other", "file_count": 1, "total_bytes": 0},
            ).status_code
            == 409
        )
        assert client.delete(url).status_code == 409
        assert client.post(url + "/complete").status_code == 409
        source = tmp_path / "games/.vn-stream-uploads" / uid
        assert (source / "project/game/unknown.bin").read_bytes() == b"save"
        external = tmp_path / "games/keep"
        external.mkdir()
        (external / "save").write_bytes(b"untouched")
        assert api_data(client.delete("/api/v2/tasks/imports/" + job_id)) == {"abandoned": True}
        assert not source.exists()
        assert (external / "save").read_bytes() == b"untouched"
        assert (
            client.post(
                "/api/v2/uploads/directories",
                json={"name": "Other", "file_count": 1, "total_bytes": 0},
            ).status_code
            == 201
        )


def test_path_import_cannot_register_directory_upload_staging_in_place(tmp_path):
    root = tmp_path / "games"
    with TestClient(create_app(tmp_path / "data", [root], "test", library_root=root)) as client:
        sign_in(client, "test")
        uid, _ = create(client, [{"path": "game/script.rpy", "size_bytes": 0}])
        source = root / ".vn-stream-uploads" / uid / "project"
        response = client.post(
            "/api/v2/imports", json={"request_id": "bypass", "path": str(source)}
        )
        assert response.status_code == 409
        assert source.is_dir()


def test_repeated_abandon_of_failed_directory_job_returns_success(tmp_path):
    with client_for(tmp_path) as client:
        uid, _ = create(client, [{"path": "game/unknown.bin", "size_bytes": 0}])
        upload_url = f"/api/v2/uploads/directories/{uid}"
        assert client.post(upload_url + "/seal").status_code == 200
        assert client.post(upload_url + "/complete").status_code == 200
        response = client.post(
            "/api/v2/imports", json={"request_id": "failed-directory", "directory_upload_id": uid}
        )
        job_id = api_data(response)["task_id"]
        assert settle(client, job_id)["state"] == "failed"
        job_url = "/api/v2/tasks/imports/" + job_id
        first = client.delete(job_url)
        assert first.status_code == 200 and api_data(first) == {"abandoned": True}
        again = client.delete(job_url)
        assert again.status_code == 200 and api_data(again) == {"abandoned": True}
        assert api_data(client.get(job_url))["abandoned"]
        assert api_data(client.get("/api/v2/imports/active")) is None
        assert client.get(upload_url).status_code == 404


def test_active_discovers_lost_create_and_preserves_durable_progress_after_restart(tmp_path):
    active_url = "/api/v2/uploads/directories/active"
    with client_for(tmp_path) as client:
        client.cookies.clear()
        assert client.get(active_url).status_code == 401
        sign_in(client, "test")
        initial = client.get(active_url)
        assert initial.status_code == 200 and api_data(initial) is None
        jobs = client.app.state.import_jobs
        # Simulate the create commit succeeding while its response (and ID) is lost.
        with jobs.operation():
            jobs.directories.create("Story", 1, 4)
        discovered = api_data(client.get(active_url))
        uid = discovered["upload_id"]
        url = f"/api/v2/uploads/directories/{uid}"
        assert discovered == api_data(client.get(url))
        assert discovered["state"] == "manifest" and discovered["files"] == []
        manifest = api_data(
            client.post(
                url + "/manifest",
                json={
                    "batch_id": "one",
                    "files": [{"path": "game/script.rpy", "size_bytes": 4}],
                },
            )
        )["files"]
        assert api_data(client.get(active_url))["files"] == manifest
        assert client.post(url + "/seal").status_code == 200
        file_url = url + "/files/" + manifest[0]["file_id"]
        assert client.put(file_url + "?offset_bytes=0", content=b"pa").status_code == 200
        with jobs.library.db.transaction() as db:
            db.execute("UPDATE directory_uploads SET updated = 1 WHERE id = ?", (uid,))
        before_restart = api_data(client.get(active_url))
        assert before_restart["state"] == "uploading"
        assert before_restart["manifest_count"] == 1
        assert before_restart["uploaded_bytes"] == before_restart["files"][0]["offset_bytes"] == 2
        with jobs.library.db.transaction() as db:
            assert (
                db.execute("SELECT updated FROM directory_uploads WHERE id = ?", (uid,)).fetchone()[
                    0
                ]
                == 1
            )
    with client_for(tmp_path) as client:
        assert api_data(client.get(active_url)) == before_restart
        assert client.put(file_url + "?offset_bytes=2", content=b"ss").status_code == 200
        assert client.post(url + "/complete").status_code == 200
        assert api_data(client.get(active_url))["state"] == "complete"
        response = client.post(
            "/api/v2/imports",
            json={
                "request_id": "discovered",
                "directory_upload_id": uid,
            },
        )
        assert settle(client, api_data(response)["task_id"])["state"] == "completed"
        assert api_data(client.get(active_url)) is None


def test_active_returns_first_manifest_page_and_next_cursor(tmp_path):
    with client_for(tmp_path) as client:
        created = api_data(
            client.post(
                "/api/v2/uploads/directories",
                json={
                    "name": "Story",
                    "file_count": 1001,
                    "total_bytes": 0,
                },
            )
        )
        url = "/api/v2/uploads/directories/" + created["upload_id"]
        first = [{"path": f"game/file-{i}.bin", "size_bytes": 0} for i in range(1000)]
        assert (
            client.post(url + "/manifest", json={"batch_id": "first", "files": first}).status_code
            == 200
        )
        assert (
            client.post(
                url + "/manifest",
                json={
                    "batch_id": "last",
                    "files": [{"path": "game/last.bin", "size_bytes": 0}],
                },
            ).status_code
            == 200
        )
        response = client.get("/api/v2/uploads/directories/active")
        assert response.status_code == 200
        active = api_data(response)
        assert active == api_data(client.get(url))
        assert active["upload_id"] == created["upload_id"] and active["manifest_count"] == 1001
        assert len(active["files"]) == 1000 and active["next_cursor"] == 1000
        assert api_data(client.get(url + "?cursor=1000"))["files"][0]["path"] == "game/last.bin"


def test_active_returns_null_when_completed_job_cleans_up_looked_up_upload(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        uid, _ = create(client, [{"path": "game/script.rpy", "size_bytes": 0}])
        url = f"/api/v2/uploads/directories/{uid}"
        assert client.post(url + "/seal").status_code == 200
        assert client.post(url + "/complete").status_code == 200
        jobs = client.app.state.import_jobs
        original = jobs.directories.status
        cleanup = jobs._cleanup
        entered, release = Event(), Event()

        def paused_cleanup(job_id):
            entered.set()
            assert release.wait(3)
            return cleanup(job_id)

        monkeypatch.setattr(jobs, "_cleanup", paused_cleanup)
        job = jobs.create("active-cleanup-race", directory_upload_id=uid)

        def cleanup_before_status(upload_id, *args, **kwargs):
            release.set()
            assert jobs.wait(job["id"])["state"] == "completed"
            return original(upload_id, *args, **kwargs)

        monkeypatch.setattr(jobs.directories, "status", cleanup_before_status)
        try:
            assert entered.wait(3)
            response = client.get("/api/v2/uploads/directories/active")
        finally:
            release.set()
        assert response.status_code == 200
        assert api_data(response) is None
        assert len(api_data(client.get("/api/v2/projects"))) == 1


@pytest.mark.parametrize("failure", ["other_key", "existing_record", "storage"])
def test_active_does_not_hide_errors_other_than_missing_upload(tmp_path, monkeypatch, failure):
    with client_for(tmp_path) as client:
        uid, _ = create(client, [{"path": "game/script.rpy", "size_bytes": 0}])
        directories = client.app.state.import_jobs.directories
        error = (
            OSError("storage unavailable")
            if failure == "storage"
            else KeyError(uid if failure == "existing_record" else "updated")
        )

        def failed_status(upload_id):
            if failure != "existing_record":
                directories.remove(upload_id)
            raise error

        monkeypatch.setattr(directories, "status", failed_status)
        with pytest.raises(type(error)) as raised:
            directories.active()
        assert raised.value is error
