"""Public contracts for the clean-start, multi-engine service."""

import importlib.util


def test_only_new_service_package_is_available():
    assert importlib.util.find_spec("vn_stream") is not None
    assert importlib.util.find_spec("renpy_stream") is None


def test_v2_envelopes_and_no_old_routes(tmp_path):
    from tests.auth_helpers import TestClient
    from vn_stream.api.app import create_app

    with TestClient(create_app(tmp_path / "data", [tmp_path / "games"], "secret")) as client:
        denied = client.get("/api/v2/projects")
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "ui.pleaseSignIn"
        login = client.post("/api/v2/auth/login", json={"username": "player", "password": "secret"})
        assert login.status_code == 200
        assert login.json()["data"]["csrf_token"]
        assert client.get("/api/v2/projects").json() == {"data": []}
        assert client.get("/api/v2/engines").json()["data"][0]["engine_id"] == "renpy"
        assert client.get("/api/games").status_code == 404
        assert client.get("/api/v1/projects").status_code == 404


def test_start_accepts_runtime_id_only(tmp_path):
    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    with TestClient(create_app(tmp_path / "data", [tmp_path], "secret")) as client:
        sign_in(client, "secret")
        response = client.post("/api/v2/projects/missing/start", json={"runtime_version": "8.3.2"})
        assert response.status_code == 422
