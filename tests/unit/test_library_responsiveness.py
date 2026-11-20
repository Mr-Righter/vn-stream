"""Catalog polling and a slow startup must not stall unrelated requests."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from tests.auth_helpers import TestClient, api_data, sign_in
from tests.unit.test_api import project
from vn_stream.api import app as api_module
from vn_stream.api.app import create_app
from vn_stream.engines.renpy.version_detection import detect_engine
from vn_stream.sessions.supervisor import SessionManager


def test_catalog_detects_installed_runtimes_once_per_request(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "data", [tmp_path / "nas"], "secret")) as c:
        sign_in(c, "secret")
        for name in ("Alpha", "Beta", "Gamma"):
            source = project(tmp_path / "nas" / name)
            assert c.post("/api/v2/projects", json={"path": str(source)}).status_code == 201
        sdk = tmp_path / "data/runtimes/renpy/local"
        (sdk / "renpy").mkdir(parents=True)
        (sdk / "renpy/__init__.py").write_text("version_tuple = (8, 3, 7, 0)")
        (sdk / "renpy.sh").write_text("exit 0")
        calls = []
        original = api_module.available_runtimes

        def counted(root, engines):
            calls.append(root)
            return original(root, engines)

        monkeypatch.setattr(api_module, "available_runtimes", counted)
        listed = api_data(c.get("/api/v2/projects"))
        assert len(listed) == 3
        assert all(
            g["recommendation"]
            == {"build_version": "8.3.7", "kind": "exact", "runtime_id": "renpy:8.3.7"}
            for g in listed
        )
        assert len(calls) == 1


def test_engine_source_parsing_is_reused_but_changed_content_is_detected(tmp_path, monkeypatch):
    from vn_stream.engines.renpy import version_detection as detection

    source = project(tmp_path / "project")
    (source / "renpy/__init__.py").write_text("version_tuple = (8, 3, 7, 0)\n# cache-fixture")
    assert detect_engine(source).version == "8.3.7"
    parsed = []
    original = detection.ast.parse

    def counted(*args, **kwargs):
        parsed.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(detection.ast, "parse", counted)
    assert detect_engine(source).version == "8.3.7"
    assert not parsed, "Unchanged engine sources should not be parsed on every poll"
    (source / "renpy/__init__.py").write_text("version_tuple = (8, 4, 1, 0)\n# cache-fixture")
    assert detect_engine(source).version == "8.4.1"


def test_control_waiting_for_startup_does_not_block_login_or_library(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    original = SessionManager.status

    def slow_status(self):
        entered.set()
        release.wait(5)
        return original(self)

    with TestClient(create_app(tmp_path / "data", [tmp_path], "secret")) as c:
        sign_in(c, "secret")
        monkeypatch.setattr(SessionManager, "status", slow_status)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with c.websocket_connect(
                "wss://testserver/api/v2/session/control", headers={"origin": "https://testserver"}
            ) as ws:
                ws.send_json(
                    {
                        "csrf_token": c.headers["X-CSRF-Token"],
                        "session_id": "a" * 32,
                        "connection_id": "c",
                    }
                )
                try:
                    assert entered.wait(2)
                    request = pool.submit(c.get, "/api/v2/auth/session")
                    assert request.result(timeout=1).status_code == 200
                    assert api_data(c.get("/api/v2/projects")) == []
                finally:
                    release.set()
