"""Cookie auth must cover every path without a browser Basic challenge."""

import hashlib
import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from tests.auth_helpers import api_data
from vn_stream.api.app import create_app


def client(root, password="secret", username="player", **kwargs):
    return TestClient(
        create_app(root / "data", [], password, username=username, **kwargs),
        base_url="https://testserver",
    )


def login(c, remember=True, password="secret", username="player"):
    return c.post(
        "/api/v2/auth/login",
        json={"username": username, "password": password, "remember": remember},
    )


def test_cookie_login_covers_root_api_and_preserves_only_digest(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>Library</html>")
    with client(tmp_path, web=web) as c:
        assert c.get("/api/v2/session").status_code == 401
        response = login(c)
        assert response.status_code == 200
        value = api_data(response)
        assert value["authenticated"] and value["username"] == "player"
        cookie = response.headers.get_list("set-cookie")
        auth = next(s for s in cookie if s.startswith("vn_stream_session="))
        assert (
            "HttpOnly" in auth and "Secure" in auth and "SameSite=lax" in auth and "Path=/" in auth
        )
        # Disk scheduling can consume seconds between durable issuance and headers.
        age = int(auth.split("Max-Age=", 1)[1].split(";", 1)[0])
        assert 2591900 <= age <= 2592000
        assert "no-store" in response.headers["cache-control"]
        assert "www-authenticate" not in response.headers
        token = c.cookies.get("vn_stream_session")
        assert token and token not in response.text
        assert c.get("/").status_code == 200
        assert c.get("/api/v2/session").status_code == 200
        assert api_data(c.get("/api/v2/auth/session"))["csrf_token"] == c.cookies.get(
            "vn_stream_csrf"
        )
        assert c.get("/api/v2/auth/verify").status_code == 204
    db = tmp_path / "data/database/auth.sqlite3"
    assert token.encode() not in db.read_bytes()
    with sqlite3.connect(db) as connection:
        assert (
            connection.execute("select token_hash from auth_sessions").fetchone()[0]
            == hashlib.sha256(token.encode()).hexdigest()
        )


def test_basic_credentials_no_longer_bypass_login_and_wrong_password_has_no_challenge(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/api/v2/session", auth=("player", "secret")).status_code == 401
        response = login(c, password="wrong")
        assert response.status_code == 401
        assert "www-authenticate" not in response.headers
        assert "wrong" not in response.text
        assert "vn_stream_session" not in c.cookies


@pytest.mark.parametrize(
    ("url", "secure"),
    [
        ("http://127.0.0.1:8443", False),
        ("http://localhost:8443", False),
        ("https://host.test", True),
    ],
)
def test_compose_login_cookie_uses_the_direct_endpoint_scheme(tmp_path, url, secure):
    app = create_app(
        tmp_path / "data", [], "secret", public_prefix="/library", allow_insecure_local=True
    )
    with TestClient(app, base_url=url) as c:
        response = c.post(
            "/api/v2/auth/login",
            json={"username": "player", "password": "secret"},
            headers={"Origin": url},
        )
        assert response.status_code == 200
        cookies = response.headers.get_list("set-cookie")
        assert len(cookies) == 2 and all(
            ("; secure" in cookie.lower()) == secure for cookie in cookies
        )
        assert c.get("/api/v2/auth/session").status_code == 200


def test_csrf_and_origin_checked_before_any_mutation(tmp_path):
    with client(tmp_path) as c:
        assert login(c, password="secret").status_code == 200
        assert c.post("/api/v2/projects/refresh").status_code == 403
        csrf = c.cookies.get("vn_stream_csrf")
        assert (
            c.post("/api/v2/projects/refresh", headers={"X-CSRF-Token": "bad"}).status_code == 403
        )
        assert c.post("/api/v2/projects/refresh", headers={"X-CSRF-Token": csrf}).status_code == 200
        for origin in ["https://foreign.invalid", "null"]:
            assert (
                c.post(
                    "/api/v2/projects/refresh", headers={"Origin": origin, "X-CSRF-Token": csrf}
                ).status_code
                == 403
            )
        assert (
            c.post(
                "/api/v2/auth/login",
                json={"username": "player", "password": "secret"},
                headers={"Origin": "https://foreign.invalid"},
            ).status_code
            == 403
        )
        assert (
            c.post(
                "/api/v2/auth/login",
                content="username=player&password=secret",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ).status_code
            == 415
        )


def test_unremembered_cookie_has_twelve_hour_server_limit(tmp_path, monkeypatch):
    import time

    now = time.time()
    with client(tmp_path) as c:
        response = login(c, remember=False)
        assert response.status_code == 200
        auth = next(
            s for s in response.headers.get_list("set-cookie") if s.startswith("vn_stream_session=")
        )
        assert "Max-Age" not in auth and "expires=" not in auth.lower()
        assert datetime.fromisoformat(
            api_data(response)["expires_at"]
        ).timestamp() == pytest.approx(now + 43200, abs=3)
        monkeypatch.setattr("vn_stream.auth.repository.time.time", lambda: now + 43201)
        assert c.get("/api/v2/session").status_code == 401


def test_restart_persists_session_password_change_revokes_it(tmp_path):
    with client(tmp_path) as c:
        assert login(c).status_code == 200
        cookies = dict(c.cookies)
    with client(tmp_path) as c:
        c.cookies.update(cookies)
        assert c.get("/api/v2/session").status_code == 200
    with client(tmp_path, password="new-secret") as c:
        c.cookies.update(cookies)
        assert c.get("/api/v2/session").status_code == 401
        assert login(c, password="secret").status_code == 401
        assert login(c, password="new-secret").status_code == 200


def test_logout_revokes_cookie_but_does_not_stop_game(tmp_path, monkeypatch):
    with client(tmp_path) as c:
        assert login(c).status_code == 200
        cookies = dict(c.cookies)
        monkeypatch.setattr(
            "vn_stream.sessions.supervisor.SessionManager.stop",
            lambda *a: pytest.fail("Logout must not stop game"),
        )
        response = c.post(
            "/api/v2/auth/logout", headers={"X-CSRF-Token": c.cookies.get("vn_stream_csrf")}
        )
        assert response.status_code == 200
        assert not c.cookies.get("vn_stream_session")
        c.cookies.update(cookies)
        assert c.get("/api/v2/session").status_code == 401
        monkeypatch.undo()


def test_page_redirect_and_json_401_never_challenge(tmp_path):
    with client(tmp_path, public_prefix="/library") as c:
        response = c.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("/library/login?")
        assert "www-authenticate" not in response.headers
        response = c.get("/api/v2/auth/session")
        assert (
            response.status_code == 401 and "application/json" in response.headers["content-type"]
        )


def test_invalid_cookie_secret_redaction_and_unicode_credentials(tmp_path):
    with client(tmp_path, password="测试密码", username="玩家") as c:
        response = login(c, password="测试密码", username="玩家")
        assert response.status_code == 200
        c.cookies.set("vn_stream_session", "forged", domain="testserver.local", path="/")
        assert c.get("/api/v2/session").status_code == 401
        response = c.post(
            "/api/v2/auth/login", json={"username": "玩家", "password": ["do-not-echo"]}
        )
        assert response.status_code == 422 and "do-not-echo" not in response.text


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.invalid",
        "//evil.invalid",
        "/%2f%2fevil.invalid",
        "/\\evil.invalid",
        "/%255cevil.invalid",
    ],
)
def test_login_return_cannot_leave_same_site(target):
    from vn_stream.auth.policy import login_url

    assert login_url("/library", target) == "/library/login?next=%2Flibrary%2F"


def test_remembered_session_expires_at_fixed_deadline(tmp_path, monkeypatch):
    import time

    now = time.time()
    with client(tmp_path) as c:
        response = login(c)
        expiry = api_data(response)["expires_at"]
        assert datetime.fromisoformat(expiry).timestamp() == pytest.approx(now + 30 * 86400, abs=3)
        monkeypatch.setattr("vn_stream.auth.repository.time.time", lambda: now + 86400)
        assert api_data(c.get("/api/v2/auth/session"))["expires_at"] == expiry
        monkeypatch.setattr(
            "vn_stream.auth.repository.time.time",
            lambda: datetime.fromisoformat(expiry).timestamp() + 1,
        )
        assert c.get("/api/v2/session").status_code == 401


def test_two_sessions_logout_only_revokes_current_and_missing_csrf_resynchronizes(tmp_path):
    with client(tmp_path) as first, client(tmp_path) as second:
        assert login(first).status_code == 200
        assert login(second).status_code == 200
        first.cookies.delete("vn_stream_csrf")
        res = first.get("/api/v2/auth/session")
        assert (
            res.status_code == 200
            and first.cookies.get("vn_stream_csrf") == api_data(res)["csrf_token"]
        )
        assert (
            first.post(
                "/api/v2/auth/logout", headers={"X-CSRF-Token": first.cookies.get("vn_stream_csrf")}
            ).status_code
            == 200
        )
        assert first.get("/api/v2/session").status_code == 401
        assert second.get("/api/v2/session").status_code == 200


def test_login_assets_are_precisely_public_with_correct_mime(tmp_path):
    web = tmp_path / "web"
    (web / "icons").mkdir(parents=True)
    (web / "ui.css").write_text(".rs-login-card { max-width:400px }")
    for name in ("moon", "sun", "private"):
        (web / "icons" / f"{name}.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    with client(tmp_path, web=web) as c:
        for path, mime in (
            ("/ui.css", "text/css"),
            ("/icons/moon.svg", "image/svg+xml"),
            ("/icons/sun.svg", "image/svg+xml"),
        ):
            response = c.get(path, follow_redirects=False)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(mime)
        for path in ("/icons/private.svg", "/style.css", "/wa/old.css"):
            assert c.get(path, follow_redirects=False).status_code == 302
        assert c.get("/api/v2/session").status_code == 401


def test_only_build_listed_font_assets_are_anonymously_available(tmp_path):
    import json

    web = tmp_path / "web"
    (web / "fonts").mkdir(parents=True)
    (web / "fonts" / "used.woff2").write_bytes(b"wOF2fixture")
    (web / "fonts" / "private.woff2").write_bytes(b"wOF2private")
    (web / "theme.css").write_text(":root { color-scheme: dark }")
    (web / "public-assets.json").write_text(
        json.dumps(["/fonts/used.woff2", "/theme.css", "/api/v2/session", "/../secret"])
    )
    with client(tmp_path, web=web) as c:
        for path, mime in (("/fonts/used.woff2", "font/woff2"), ("/theme.css", "text/css")):
            response = c.get(path, follow_redirects=False)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(mime)
        assert c.get("/fonts/private.woff2", follow_redirects=False).status_code == 302
        assert c.get("/public-assets.json", follow_redirects=False).status_code == 302
        assert c.get("/api/v2/session").status_code == 401
