"""Opt-in checks against the isolated Docker deployment, without mocked transport."""

import json
import os
import time
from pathlib import Path

import httpx
import pytest

from tests.auth_helpers import api_data

pytestmark = pytest.mark.skipif(
    not os.environ.get("VN_STREAM_CONTAINER_URL"),
    reason="opt-in: requires an isolated running container and local credentials",
)


class ContainerConnection:
    def __init__(self):
        path = Path(os.environ.get("VN_STREAM_CONTAINER_ENV", "containers/.env"))
        self.settings = dict(
            line.split("=", 1)
            for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#") and "=" in line
        )
        password = self.settings["VN_STREAM_PASSWORD"]
        self.client = httpx.Client(
            base_url=os.environ["VN_STREAM_CONTAINER_URL"],
            trust_env=False,
            timeout=15,
        )
        response = self.client.post(
            "/api/v2/auth/login",
            json={
                "username": self.settings.get("VN_STREAM_USERNAME", "player"),
                "password": password,
            },
        )
        assert response.status_code == 200
        self.headers = {"X-CSRF-Token": api_data(response)["csrf_token"]}
        self.client.headers.update(self.headers)
        self.anonymous = httpx.Client(
            base_url=os.environ["VN_STREAM_CONTAINER_URL"],
            trust_env=False,
            timeout=15,
        )

    def __repr__(self):
        return "<ContainerConnection credentials hidden>"

    def request(self, path, *, authenticated=False, body=None, headers=None):
        values = dict(self.headers) if authenticated else {}
        values.update(headers or {})
        return (self.client if authenticated else self.anonymous).request(
            "GET" if body is None else "POST", path, json=body, headers=values
        )


@pytest.fixture(scope="module")
def container():
    connection = ContainerConnection()
    yield connection
    connection.client.close()
    connection.anonymous.close()


def test_page_control_and_media_authentication(container):
    assert container.request("/library/").status_code == 302
    assert container.request("/api/health").status_code == 401
    assert container.request("/api/v2/session").status_code == 401
    assert container.request("/api/v2/session", authenticated=True).status_code == 200
    assert (
        container.request("/api/v2/session", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    for asset in ("game-controls.js", "game-controls.css"):
        assert container.request(f"/library/{asset}").status_code == 302
        assert container.request(f"/library/{asset}", authenticated=True).status_code == 200
    page = container.request("/", authenticated=True)
    assert page.status_code == 200
    assert page.text.count('src="/library/game-controls.js?v=') == 1
    assert container.request("/library/", authenticated=True).status_code == 200
    assert container.request("/api/health", authenticated=True).status_code == 200
    assert container.request("/api/v2/session", authenticated=True).status_code == 200


def test_foreign_websocket_is_rejected_even_with_valid_cookie(container):
    response = container.request(
        "/api/websockets",
        authenticated=True,
        headers={
            "Origin": "https://foreign-origin.invalid",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        },
    )
    assert response.status_code == 403


def test_installed_runtimes_are_visible_to_the_container_service(container):
    response = container.request("/api/v2/runtimes", authenticated=True)
    assert response.status_code == 200
    assert {row["build_version"] for row in api_data(response)} >= {
        "7.5.3",
        "7.8.2",
        "8.0.3",
        "8.1.3",
        "8.2.3",
        "8.3.7",
        "8.4.1",
        "8.5.3",
    }


def test_real_game_starts_as_normal_user_with_game_owned_save_paths(container):
    project = Path(
        os.environ.get("VN_STREAM_CONTAINER_PROBE", "var/container-verify/inbox/Stream-Probe")
    )
    events = project / "events.jsonl"
    before = len(events.read_text().splitlines()) if events.exists() else 0
    status = api_data(container.request("/api/v2/session", authenticated=True))
    assert status["state"] in ("stopped", "failed"), "Use an isolated idle verification container"
    imported = container.request(
        "/api/v2/projects", authenticated=True, body={"path": "/projects/Stream-Probe"}
    )
    assert imported.status_code == 201, imported.text
    game = api_data(imported)
    assert game["engine_version"] == "8.5.3"
    started = container.request(
        f"/api/v2/projects/{game['project_id']}/start", authenticated=True, body={}
    )
    assert started.status_code == 200, started.text
    try:
        deadline = time.monotonic() + 20
        rows = []
        while time.monotonic() < deadline:
            if events.exists():
                rows = [
                    json.loads(line)
                    for line in events.read_text().splitlines()[before:]
                    if line.endswith("}")
                ]
                if any(row["event"] == "main_menu" for row in rows):
                    break
            time.sleep(0.1)
        assert rows, "Game did not reach its main menu; inspect the isolated game log"
        menu = next(row for row in rows if row["event"] == "main_menu")
        assert menu["uid"] == int(container.settings["PUID"])
        assert menu["uid"] != 0
        assert menu["savedir"] == "/projects/Stream-Probe/game/saves"
        assert menu["home"] == "/projects/Stream-Probe/.vn-stream/home"
        current = api_data(container.request("/api/v2/session", authenticated=True))
        assert current["state"] == "running" and current["project_id"] == game["project_id"]
    finally:
        container.request("/api/v2/session/stop", authenticated=True, body={})


def test_foreign_mutation_is_rejected_with_cached_credentials(container):
    assert (
        container.request(
            "/api/v2/session/stop",
            authenticated=True,
            body={},
            headers={"Origin": "https://foreign.invalid"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize("extension", ["zip", "7z", "tar", "tar.gz", "tar.bz2", "tar.xz", "apk"])
def test_authenticated_upload_extracts_package(container, tmp_path, extension):
    import io
    import tarfile
    import zipfile

    import py7zr
    import pyzipper

    members = {
        "Demo/game/script.rpy": b"label start:\n return",
        "Demo/game/cache/build_info.json": b'{"name":"Uploaded Story","version":"0.4"}',
        "Demo/game/saves/persistent": b"preserved-test-data",
    }
    archive_path = tmp_path / ("Demo." + extension)
    archive_password = "test-package-password"
    if extension == "zip":
        with pyzipper.AESZipFile(archive_path, "w", encryption=pyzipper.WZ_AES) as archive:
            archive.setpassword(archive_password.encode())
            for name, content in members.items():
                archive.writestr(name, content)
    elif extension == "apk":
        with zipfile.ZipFile(archive_path, "w") as archive:
            for name, content in members.items():
                archive.writestr(
                    "assets/" + "/".join("x-" + part for part in name.split("/")[1:]), content
                )
    elif extension == "7z":
        with py7zr.SevenZipFile(
            archive_path, "w", password=archive_password, header_encryption=True
        ) as archive:
            for name, content in members.items():
                archive.writestr(content, name)
    else:
        mode = {"tar": "w", "tar.gz": "w:gz", "tar.bz2": "w:bz2", "tar.xz": "w:xz"}[extension]
        with tarfile.open(archive_path, mode) as archive:
            root = tarfile.TarInfo("./")
            root.type = tarfile.DIRTYPE
            archive.addfile(root)
            for name, content in members.items():
                info = tarfile.TarInfo("./" + name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    payload = archive_path.read_bytes()
    created = container.request(
        "/api/v2/uploads/archives",
        authenticated=True,
        body={"filename": archive_path.name, "size_bytes": len(payload)},
    )
    assert created.status_code == 201, created.text
    uid = api_data(created)["upload_id"]
    url = f"/api/v2/uploads/archives/{uid}"
    response = container.client.put(
        url + "?offset_bytes=0", headers=container.headers, content=payload
    )
    assert response.status_code == 200, response.text
    assert api_data(response)["offset_bytes"] == len(payload)
    if extension in ("zip", "7z"):
        assert (
            container.request(
                url + "/import", authenticated=True, body={"password": "wrong"}
            ).status_code
            == 400
        )
        assert api_data(container.request(url, authenticated=True))["offset_bytes"] == len(payload)
    imported = container.request(
        url + "/import", authenticated=True, body={"password": archive_password}
    )
    assert imported.status_code == 201, imported.text
    assert api_data(imported)["name"] == "Uploaded Story"
    assert api_data(imported)["release_version"] == "0.4"
    assert container.request(url, authenticated=True).status_code == 404


def test_real_proxy_upload_chunks_and_server_browser(container):
    assert container.request("/api/v2/imports/sources").status_code == 401
    roots = api_data(container.request("/api/v2/imports/sources", authenticated=True))["roots"]
    assert roots
    listing = api_data(container.request("/api/v2/imports/sources?root=0", authenticated=True))
    assert any(row["directory"] and row["project"] for row in listing["entries"])
    assert (
        container.request("/api/v2/imports/sources?root=0&path=..", authenticated=True).status_code
        == 400
    )
    size = 8 * 1024**2 + 31
    created = container.request(
        "/api/v2/uploads/archives",
        authenticated=True,
        body={"filename": "chunks.zip", "size_bytes": size},
    )
    assert created.status_code == 201
    url = "/api/v2/uploads/archives/" + api_data(created)["upload_id"]
    try:
        for offset, count in ((0, 8 * 1024**2), (8 * 1024**2, 31)):
            result = container.client.put(
                url + f"?offset_bytes={offset}",
                headers=container.headers,
                content=b"x" * count,
            )
            assert result.status_code == 200, result.text
            assert api_data(result)["offset_bytes"] == offset + count
    finally:
        assert container.client.delete(url, headers=container.headers).status_code == 200
    assert container.request(url, authenticated=True).status_code == 404
