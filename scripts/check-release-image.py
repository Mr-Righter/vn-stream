"""Check the public image with isolated, disposable Docker volumes."""

import argparse
import http.cookiejar
import json
import secrets
import subprocess
import time
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = "/opt/vn-stream/bundled-runtimes"


def docker(*args: str) -> str:
    return subprocess.check_output(("docker", *args), encoding="utf-8").strip()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_http(origin: str, password: str, native_lock: dict) -> None:
    client = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
    deadline = time.monotonic() + 90
    while True:
        try:
            with client.open(origin + "/api/v2/runtimes", timeout=3):
                raise RuntimeError("The runtime catalog is accessible without signing in")
        except HTTPError as error:
            error.close()
            if error.code == 401:
                break
            if error.code not in (502, 503, 504):
                raise
        except (URLError, TimeoutError, ConnectionResetError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("The isolated container did not become ready in 90 seconds")
        time.sleep(1)

    login = Request(
        origin + "/api/v2/auth/login",
        data=json.dumps({"username": "release-check", "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with client.open(login, timeout=10) as response:
        check(json.load(response)["data"]["authenticated"], "Sign-in failed")
    with client.open(origin + "/api/v2/runtimes", timeout=10) as response:
        runtimes = json.load(response)["data"]
    check(
        Counter(runtime["engine_id"] for runtime in runtimes) == {"renpy": 9, "ons": 4, "krkr": 2},
        "Expected nine Ren'Py, four ONScripter and two Kirikiri runtimes",
    )
    native = {
        runtime["runtime_id"]: runtime for runtime in runtimes if runtime["engine_id"] != "renpy"
    }
    for item in native_lock["runtimes"]:
        manifest = item["manifest"]
        runtime = native[manifest["id"]]
        for field in ("binary_sha256", "source_commit", "validation"):
            check(runtime[field] == manifest[field], f"{field} mismatch for {manifest['id']}")
        check(
            runtime.get("default", False) == manifest.get("default", False),
            f"Default selection mismatch for {manifest['id']}",
        )
    with client.open(origin + "/api/v2/runtimes/releases", timeout=10) as response:
        releases = json.load(response)["data"]
    official = {runtime["build_version"] for runtime in runtimes if runtime["engine_id"] == "renpy"}
    check(
        {release["version"] for release in releases if release["installed"]} == official,
        "Official releases and bundled Ren'Py SDKs differ",
    )

    # Check the same public files a mobile browser reads before sign-in.
    client = build_opener(ProxyHandler({}))
    for size in (32, 192, 512):
        path = f"/app-icons/icon-{size}.png"
        with client.open(origin + path, timeout=10) as response:
            check(response.headers.get_content_type() == "image/png", f"Incorrect type for {path}")
            check(response.read() == (ROOT / "web/public" / path[1:]).read_bytes(), f"Wrong {path}")
    with client.open(origin + "/manifest.webmanifest", timeout=10) as response:
        check(json.load(response)["name"] == "VN Stream", "Incorrect web app identity")


def verify_image(image: str, *, isolated: bool) -> None:
    metadata = json.loads(docker("image", "inspect", image))[0]
    check((metadata["Os"], metadata["Architecture"]) == ("linux", "amd64"), "Expected Linux amd64")
    password = secrets.token_urlsafe(24)
    container = docker(
        "run",
        "--detach",
        "--rm",
        "--shm-size",
        "1g",
        "--publish",
        "127.0.0.1::3000",
        "--tmpfs",
        "/games:uid=1000,gid=1000,mode=0755",
        "--env",
        "PUID=1000",
        "--env",
        "PGID=1000",
        "--env",
        "CUSTOM_USER=release-check",
        "--env",
        f"PASSWORD={password}",
        image,
    )
    try:
        port = docker("port", container, "3000/tcp").rsplit(":", 1)[-1]
        native_lock = json.loads((ROOT / "containers/runtime-bundle.lock.json").read_text())
        provenance = json.loads(
            docker("exec", container, "cat", "/opt/vn-stream/runtime-provenance.json")
        )
        check(
            provenance["native_archive"]["sha256"] == native_lock["archive_sha256"],
            "Image provenance names a different native archive",
        )
        if not isolated:
            check(
                (provenance["source_archive"] or {}).get("sha256")
                == native_lock["source_archive_sha256"],
                "Image is missing the matching corresponding-source provenance",
            )
        verify_http(f"http://127.0.0.1:{port}", password, native_lock)
        docker("exec", container, "test", "!", "-e", "/config/ssl")
        docker("exec", "--user", "abc", container, "test", "!", "-w", BUNDLE)
        check(
            not docker("exec", container, "ls", "-A", "/data/runtimes"),
            "Local runtime data changed",
        )
        linked = docker(
            "exec",
            container,
            "find",
            BUNDLE + "/renpy",
            "-type",
            "f",
            "-links",
            "+1",
            "-print",
            "-quit",
        )
        check(bool(linked), "Ren'Py SDK hard links did not survive the image build")
    finally:
        docker("rm", "--force", "--volumes", container)
    print(
        f"Verified {image} ({metadata['Size'] / 1024**2:.0f} MiB), 15 runtimes, sign-in and web app icons"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Local Docker image reference")
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="Check a local image built without the source archive",
    )
    args = parser.parse_args()
    verify_image(args.image, isolated=args.isolated)


if __name__ == "__main__":
    main()
