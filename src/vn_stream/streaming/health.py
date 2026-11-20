"""Container readiness: control, proxy authentication, transport, X11 and audio."""

import json
import os
import subprocess
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


def expect_status(url: str, expected: int, headers: dict | None = None) -> None:
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(Request(url, headers=headers or {}), timeout=3) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
        error.close()
    if status != expected:
        raise RuntimeError(f"{url}: expected {expected}, received {status}")


def main() -> None:
    endpoint = "http://127.0.0.1:3000"
    expect_status(endpoint + "/api/v2/auth/session", 401)
    expect_status(endpoint + "/api/health", 401)
    # Retain a private health-only session instead of adding one DB record every
    # 30 seconds. No game is launched or media connection taken by these probes.
    path = Path("/data/tmp/health.cookies")
    jar = MozillaCookieJar(str(path))
    try:
        jar.load(ignore_discard=True)
    except (OSError, ValueError):
        pass
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))
    try:
        with opener.open(endpoint + "/api/v2/auth/session", timeout=3):
            pass
    except HTTPError as error:
        if error.code != 401:
            raise
        error.close()
        payload = json.dumps(
            {
                "username": os.environ.get("CUSTOM_USER", "player"),
                "password": os.environ["PASSWORD"],
                "remember": True,
            }
        ).encode()
        with opener.open(
            Request(
                endpoint + "/api/v2/auth/login",
                data=payload,
                headers={"Content-Type": "application/json"},
            ),
            timeout=3,
        ) as response:
            if response.status != 200:
                raise RuntimeError("Health login failed") from None
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create privately before CookieJar writes its session credential.
        with path.open("a"):
            path.chmod(0o600)
        jar.save(ignore_discard=True)
    for route in ("/api/health", "/api/v2/session"):
        with opener.open(endpoint + route, timeout=3) as response:
            if response.status != 200:
                raise RuntimeError("Authenticated health probe failed")
    cookie = Request(endpoint + "/api/websockets")
    jar.add_cookie_header(cookie)
    expect_status(
        endpoint + "/api/websockets",
        403,
        {
            "Cookie": cookie.get_header("Cookie", ""),
            "Origin": "https://foreign-origin.invalid",
            "Connection": "Upgrade",
            "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        },
    )
    for command in (["xset", "q"], ["pactl", "info"]):
        subprocess.run(command, check=True, capture_output=True, timeout=3)


if __name__ == "__main__":
    main()
