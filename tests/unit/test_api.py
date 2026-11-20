from pathlib import Path

import pytest

from tests.auth_helpers import TestClient, api_data, sign_in
from vn_stream.api.app import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "data", [tmp_path / "nas"], "test-secret")) as c:
        sign_in(c, "test-secret")
        yield c


def project(root):
    (root / "game").mkdir(parents=True)
    (root / "game/script.rpyc").write_bytes(b"compiled")
    (root / "renpy").mkdir()
    (root / "renpy/__init__.py").write_text("version_tuple = (8, 3, 7, 0)")
    return root


def test_api_requires_authentication(client):
    client.cookies.clear()
    assert client.get("/api/v2/projects").status_code == 401
    assert client.post("/api/v2/projects", json={"path": "/etc"}).status_code == 401


def test_import_list_and_missing_runtime_error(client, tmp_path):
    source = project(tmp_path / "nas/Sample")
    response = client.post("/api/v2/projects", json={"path": str(source)})
    assert response.status_code == 201
    game = api_data(response)
    assert game["engine_version"] == "8.3.7"
    assert api_data(client.get("/api/v2/projects")) == [game]
    result = client.post(f"/api/v2/projects/{game['project_id']}/start", json={})
    assert result.status_code == 409
    assert api_data(result)["error"]["code"] == "engine.runtimeUnavailable"


def test_api_rejects_outside_nas_and_reports_missing_game(client):
    assert client.post("/api/v2/projects", json={"path": "/etc"}).status_code == 400
    assert client.post("/api/v2/projects/missing/start", json={}).status_code == 404


def test_runtime_launch_passes_local_save_and_home(client, tmp_path):
    source = project(tmp_path / "nas/Sample")
    sdk = tmp_path / "data/runtimes/renpy/8.3.7"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text("version_tuple = (8, 3, 7, 0)")
    (sdk / "renpy.sh").write_text(
        'printf "%s\\n" "$@" > args.txt\nprintf "%s" "$HOME" > home.txt\nsleep 30\n'
    )
    game = api_data(client.post("/api/v2/projects", json={"path": str(source)}))
    assert api_data(client.get("/api/v2/runtimes"))[0]["build_version"] == "8.3.7"
    try:
        started = client.post(f"/api/v2/projects/{game['project_id']}/start", json={})
        assert started.status_code == 200
        import time

        deadline = time.monotonic() + 3
        while not (source / "home.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (source / "args.txt").read_text().splitlines() == [
            "--savedir",
            str(source / "game/saves"),
            str(source),
            "run",
        ]
        assert Path((source / "home.txt").read_text()) == source / ".vn-stream/home"
        assert api_data(client.get("/api/v2/session"))["project_id"] == game["project_id"]
    finally:
        assert client.post("/api/v2/session/stop").status_code == 200


def test_stream_discovery_is_authenticated_and_disabled_by_default(client):
    assert api_data(client.get("/api/v2/stream")) == {
        "available": False,
        "url": None,
        "backend": "selkies",
    }
    client.cookies.clear()
    assert client.get("/api/v2/stream").status_code == 401


def test_stream_connection_uses_only_configured_same_origin_path(tmp_path):
    with TestClient(create_app(tmp_path / "data", [tmp_path], "secret", stream_path="/")) as c:
        sign_in(c, "secret")
        response = c.get("/api/v2/stream")
        assert api_data(response) == {"available": True, "url": "/", "backend": "selkies"}


@pytest.mark.parametrize(
    "path", ["//other.example/", "javascript:alert(1)", "/a/../b", "/?token=secret"]
)
def test_stream_configuration_rejects_external_or_credential_urls(tmp_path, path):
    with pytest.raises(ValueError):
        create_app(tmp_path / "data", [tmp_path], "secret", stream_path=path)


def test_missing_game_directory_does_not_break_library_or_reindex(client, tmp_path):
    original = project(tmp_path / "nas/Original")
    game = api_data(client.post("/api/v2/projects", json={"path": str(original)}))
    moved = tmp_path / "nas/Moved"
    original.rename(moved)
    response = client.get("/api/v2/projects")
    assert response.status_code == 200
    assert api_data(response)[0]["available"] is False
    restored = client.post("/api/v2/projects", json={"path": str(moved)})
    assert restored.status_code == 201
    assert api_data(restored)["project_id"] == game["project_id"]
    assert api_data(client.get("/api/v2/projects"))[0]["available"] is True


def test_automatic_match_never_uses_remembered_manual_override(client, tmp_path):
    source = project(tmp_path / "nas/Auto")
    sdk = tmp_path / "data/runtimes/renpy/other"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text("version_tuple = (8, 5, 3, 0)")
    (sdk / "renpy.sh").write_text("sleep 30")
    game = api_data(client.post("/api/v2/projects", json={"path": str(source)}))
    try:
        assert (
            client.post(
                f"/api/v2/projects/{game['project_id']}/start", json={"runtime_id": "renpy:8.5.3"}
            ).status_code
            == 200
        )
        client.post("/api/v2/session/stop")
        # A newly installed nearest SDK wins over the old remembered override.
        nearer = tmp_path / "data/runtimes/renpy/nearer"
        (nearer / "renpy").mkdir(parents=True)
        (nearer / "renpy/__init__.py").write_text("version_tuple = (8, 3, 8, 0)")
        (nearer / "renpy.sh").write_text("sleep 30")
        result = client.post(f"/api/v2/projects/{game['project_id']}/start", json={})
        assert result.status_code == 200
        listed = api_data(client.get("/api/v2/projects"))[0]
        assert listed["engine_version"] == "8.3.7"
        assert listed["runtime_build_version"] == "8.3.8"
        assert listed["runtime_override"] is None
        assert listed["recognition"]["evidence"] == ["renpy/__init__.py"]
    finally:
        client.post("/api/v2/session/stop")


def test_manual_run_logs_do_not_reidentify_an_unknown_original_engine(client, tmp_path):
    source = project(tmp_path / "nas/Unknown")
    (source / "renpy/__init__.py").unlink()
    sdk = tmp_path / "data/runtimes/renpy/8.3.7"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text("version_tuple = (8, 3, 7, 0)")
    (sdk / "renpy.sh").write_text("sleep 30")
    game = api_data(client.post("/api/v2/projects", json={"path": str(source)}))
    try:
        assert (
            client.post(
                f"/api/v2/projects/{game['project_id']}/start", json={"runtime_id": "renpy:8.3.7"}
            ).status_code
            == 200
        )
        client.post("/api/v2/session/stop")
        (source / "log.txt").write_text("Ren'Py 8.3.7.25031702\n")
        assert api_data(client.get("/api/v2/projects"))[0]["engine_version"] is None
        assert (
            client.post(f"/api/v2/projects/{game['project_id']}/start", json={}).status_code == 409
        )
    finally:
        client.post("/api/v2/session/stop")


def test_target_releases_distinguish_installed_engines_and_require_auth(client, tmp_path):
    sdk = tmp_path / "data/runtimes/renpy/local-label"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text("version_tuple = (7, 5, 3, 0)")
    (sdk / "renpy.sh").write_text("exit 0")
    response = client.get("/api/v2/runtimes/releases")
    assert response.status_code == 200
    releases = api_data(response)
    assert len(releases) == 9
    assert releases[0]["version"] == "7.5.3"
    assert releases[0]["installed"] is True
    assert releases[0]["python"] == "2.7"
    assert releases[-1]["version"] == "8.5.3"
    assert releases[-1]["installed"] is False
    client.cookies.clear()
    assert client.get("/api/v2/runtimes/releases").status_code == 401


def test_api_reports_interrupted_supervision_without_allowing_duplicate_start(client, tmp_path):
    import json

    from vn_stream.sessions.worker import process_scope

    source = project(tmp_path / "nas/Recover")
    sdk = tmp_path / "data/runtimes/renpy/8.3.7"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text("version_tuple = (8, 3, 7, 0)")
    (sdk / "renpy.sh").write_text("sleep 30")
    game = api_data(client.post("/api/v2/projects", json={"path": str(source)}))
    (tmp_path / "data/tasks/session/session.json").write_text(
        json.dumps({"state": "running", "project_id": game["project_id"], "scope": process_scope()})
    )
    assert api_data(client.get("/api/v2/session"))["state"] == "recovering"
    response = client.post(f"/api/v2/projects/{game['project_id']}/start", json={})
    assert response.status_code == 409
    assert (
        api_data(response)["error"]["code"]
        == "errors.thePreviousSessionSupervisorFailedRestartTheContainerWithout"
    )


@pytest.mark.parametrize(
    "version", ["7.5.3", "7.8.2", "8.0.3", "8.1.3", "8.2.3", "8.3.7", "8.4.1", "8.5.3"]
)
def test_each_target_version_is_automatically_matched(client, tmp_path, version):
    source = project(tmp_path / "nas/Target")
    declaration = f"version_tuple = ({version.replace('.', ', ')}, 0)"
    (source / "renpy/__init__.py").write_text(declaration)
    sdk = tmp_path / "data/runtimes/renpy/label-independent-of-version"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text(declaration)
    (sdk / "renpy.sh").write_text("sleep 30")
    game = api_data(client.post("/api/v2/projects", json={"path": str(source)}))
    assert game["engine_version"] == version
    try:
        assert (
            client.post(f"/api/v2/projects/{game['project_id']}/start", json={}).status_code == 200
        )
        game = api_data(client.get("/api/v2/projects"))[0]
        assert game["runtime_build_version"] == version
        assert game["runtime_override"] is None
    finally:
        client.post("/api/v2/session/stop")


def test_same_cookie_protects_static_page_and_api(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("library")
    with TestClient(create_app(tmp_path / "data", [tmp_path], "secret", web=web)) as c:
        assert c.get("/", follow_redirects=False).status_code == 302
        assert c.get("/api/v2/session").status_code == 401
        sign_in(c, "secret")
        assert c.get("/").status_code == 200
        assert c.get("/api/v2/session").status_code == 200


def test_cached_password_cannot_authorize_foreign_mutation(client):
    for headers in ({"Origin": "https://foreign.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
        assert client.post("/api/v2/session/stop", headers=headers).status_code == 403
    assert (
        client.post("/api/v2/session/stop", headers={"Origin": "https://testserver"}).status_code
        == 200
    )


def test_account_and_password_accept_utf8(tmp_path):
    with TestClient(create_app(tmp_path / "data", [tmp_path], "测试密码", username="玩家")) as c:
        sign_in(c, "测试密码", "玩家")
        assert c.get("/api/v2/session").status_code == 200


def test_recommendation_details_refresh_and_authenticated_icon(client, tmp_path):
    source = project(tmp_path / "nas/Info")
    (source / "game/options.rpy").write_text(
        'define config.name = _("Story")\ndefine config.version = "0.9"'
    )
    sdk = tmp_path / "data/runtimes/renpy/8.3.8"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text("version_tuple = (8, 3, 8, 0)")
    (sdk / "renpy.sh").write_text("sleep 30")
    game = api_data(client.post("/api/v2/projects", json={"path": str(source)}))
    assert game["recommendation"] == {
        "build_version": "8.3.8",
        "kind": "newer",
        "runtime_id": "renpy:8.3.8",
    }
    assert game["name"] == "Story" and game["release_version"] == "0.9"
    assert api_data(client.get(f"/api/v2/projects/{game['project_id']}"))["size_bytes"] > 0
    try:
        started = client.post(f"/api/v2/projects/{game['project_id']}/start", json={})
        assert started.status_code == 200
        assert api_data(started)["session_id"]
        detail = api_data(client.get(f"/api/v2/projects/{game['project_id']}"))
        assert detail["runtime_override"] is None
        assert detail["runtime_build_version"] == "8.3.8"
    finally:
        client.post("/api/v2/session/stop")
    assert client.post("/api/v2/projects/refresh", json={}).status_code == 200
    assert client.get(f"/api/v2/projects/{game['project_id']}/icon").status_code == 404
    client.cookies.clear()
    assert client.get(f"/api/v2/projects/{game['project_id']}/icon").status_code == 401


def test_new_runtime_id_preserves_manual_renpy_choice(client, tmp_path):
    source = project(tmp_path / "nas/Manual-ID")
    sdk = tmp_path / "data/runtimes/renpy/8.3.7"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text("version_tuple = (8, 3, 7, 0)")
    (sdk / "renpy.sh").write_text("sleep 30\n")
    game = api_data(client.post("/api/v2/projects", json={"path": str(source)}))
    try:
        result = client.post(
            f"/api/v2/projects/{game['project_id']}/start", json={"runtime_id": "renpy:8.3.7"}
        )
        assert result.status_code == 200
        assert (
            api_data(client.get(f"/api/v2/projects/{game['project_id']}"))["runtime_override"]
            == "renpy:8.3.7"
        )
    finally:
        client.post("/api/v2/session/stop")
