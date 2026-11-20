"""Opt-in direct HTTPS checks using the platform trust store or a test trust bundle."""

import os
import ssl
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("VN_STREAM_HTTPS_TEST_URL"),
    reason="opt-in: isolated container with direct HTTPS enabled",
)


@pytest.fixture(scope="module")
def trusted_client():
    url = os.environ["VN_STREAM_HTTPS_TEST_URL"]
    assert url.startswith("https://")
    path = Path(os.environ.get("VN_STREAM_CONTAINER_ENV", "containers/.env"))
    settings = dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    context = ssl.create_default_context(cafile=os.environ.get("VN_STREAM_HTTPS_TEST_CA"))
    with httpx.Client(base_url=url, verify=context, trust_env=False) as client:
        response = client.post(
            "/api/v2/auth/login",
            json={
                "username": settings.get("VN_STREAM_USERNAME", "player"),
                "password": settings["VN_STREAM_PASSWORD"],
            },
        )
        assert response.status_code == 200
        cookies = response.headers.get_list("set-cookie")
        assert len(cookies) == 2 and all("; secure" in cookie.lower() for cookie in cookies)
        yield client


def test_https_library_and_authenticated_media(trusted_client):
    assert trusted_client.get("/library/").status_code == 200
    assert trusted_client.get("/api/health").status_code == 200
    assert trusted_client.get("/api/health", headers={"Cookie": ""}).status_code == 401


def test_https_rejects_foreign_media_origin_and_does_not_serve_private_key(trusted_client):
    assert (
        trusted_client.get(
            "/api/websockets",
            headers={
                "Origin": "https://foreign-origin.invalid",
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            },
        ).status_code
        == 403
    )
    response = trusted_client.get("/etc/vn-stream/tls/cert.key")
    assert response.status_code == 404
    assert b"PRIVATE KEY" not in response.content
