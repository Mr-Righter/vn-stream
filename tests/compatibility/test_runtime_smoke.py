"""Opt-in real-engine tests: X11 input, save/persistent, and directory migration."""

import json
import os
import pickle
import secrets
import shutil
import subprocess
import tarfile
import time
import zipfile
import zlib
from io import BytesIO
from pathlib import Path

import pytest

from tests.auth_helpers import TestClient, api_data, sign_in
from vn_stream.api.app import create_app
from vn_stream.projects.catalog import ProjectCatalog
from vn_stream.runtimes.releases import RELEASES

pytestmark = pytest.mark.skipif(
    os.environ.get("VN_STREAM_RUNTIME_SMOKE") != "1",
    reason="opt-in: needs installed SDKs and private Xvfb/xdotool",
)


def wait_for(predicate, *, seconds=20, detail=lambda: "condition timed out"):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    pytest.fail(detail())


@pytest.fixture(scope="module")
def display(tmp_path_factory):
    work = tmp_path_factory.mktemp("x11")
    prefix = os.environ.get("VN_STREAM_TEST_X11_ROOT")
    tool_env = os.environ.copy()
    if prefix:
        prefix = Path(prefix).resolve()
        binary = prefix / "usr/bin"
        tool_env["LD_LIBRARY_PATH"] = str(prefix / "usr/lib/x86_64-linux-gnu")
    else:
        binary = Path(shutil.which("Xvfb") or "/missing/Xvfb").parent
    xvfb = binary / "Xvfb"
    xdotool = binary / "xdotool"
    if not xvfb.is_file() or not xdotool.is_file() or not shutil.which("xauth"):
        pytest.fail("Explicit smoke run requires Xvfb, xdotool and xauth")
    number = next(
        n
        for n in range(170, 270)
        if not Path(f"/tmp/.X{n}-lock").exists() and not Path(f"/tmp/.X11-unix/X{n}").exists()
    )
    address = f":{number}"
    authority = work / "authority"
    subprocess.run(
        [
            "xauth",
            "-f",
            str(authority),
            "add",
            address,
            "MIT-MAGIC-COOKIE-1",
            secrets.token_hex(16),
        ],
        capture_output=True,
        check=True,
    )
    authority.chmod(0o600)
    tool_env.update(DISPLAY=address, XAUTHORITY=str(authority))
    with (work / "xvfb.log").open("wb") as log:
        server = subprocess.Popen(
            [
                str(xvfb),
                address,
                "-screen",
                "0",
                "800x600x24",
                "-nolisten",
                "tcp",
                "-auth",
                str(authority),
            ],
            env=tool_env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        wait_for(
            lambda: (
                subprocess.run(
                    [str(xdotool), "getdisplaygeometry"], env=tool_env, capture_output=True
                ).returncode
                == 0
            ),
            seconds=5,
            detail=lambda: (work / "xvfb.log").read_text(),
        )
        yield xdotool, tool_env
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def events(project):
    path = project / "events.jsonl"
    if not path.exists():
        return []
    rows = path.read_text().splitlines()
    # A line can be observed between write and close; retry instead of trusting it.
    return [json.loads(line) for line in rows if line.endswith("}")]


def key(display, name):
    tool, environment = display
    found = subprocess.run(
        [str(tool), "search", "--onlyvisible", "--name", "^RenPy Stream Smoke$"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert found.returncode == 0, found.stderr
    window = found.stdout.splitlines()[0]
    subprocess.run(
        [str(tool), "windowfocus", "--sync", window, "key", "--clearmodifiers", name],
        env=environment,
        capture_output=True,
        check=True,
        timeout=5,
    )


def package_sample(tmp_path, sdk, package_format):
    project = tmp_path / "original"
    shutil.copytree(Path(__file__).parents[1] / "fixtures/runtime-smoke", project)
    if package_format == "directory":
        return project
    home = tmp_path / "compiler-home"
    home.mkdir()
    compiled = subprocess.run(
        [
            "/bin/sh",
            str(sdk / "renpy.sh"),
            "--savedir",
            str(tmp_path / "compiler-saves"),
            str(project),
            "compile",
        ],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert compiled.returncode == 0, compiled.stderr
    bytecode = (project / "game/script.rpyc").read_bytes()
    metadata = {
        name: (sdk / name).read_bytes() for name in ("renpy/__init__.py", "renpy/vc_version.py")
    }
    family = "py2" if any(sdk.glob("lib/py2-*")) else "py3"
    if package_format == "zip-rpa":
        package = tmp_path / "sample.zip"
        # A tiny self-authored RPA-3.0 using the official loader's two-item
        # index. Only serialize our generated index; never unpickle input here.
        offset = 34 + len(bytecode)
        header = f"RPA-3.0 {offset:016x} 00000000\n".encode("ascii")
        index = zlib.compress(pickle.dumps({"script.rpyc": [(34, len(bytecode))]}, protocol=2))
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("sample/game/archive.rpa", header + bytecode + index)
            archive.writestr(f"sample/lib/{family}-windows-x86_64/", b"")
            for name, content in metadata.items():
                archive.writestr("sample/" + name, content)
        return package
    package = tmp_path / "sample.apk"
    private = BytesIO()
    with tarfile.open(fileobj=private, mode="w:gz") as archive:
        metadata[f"lib/{family}-android-arm64/runtime-marker"] = b"fixture metadata only"
        for name, content in metadata.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, BytesIO(content))
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("assets/x-game/x-script.rpyc", bytecode)
        archive.writestr("assets/private.mp3", private.getvalue())
    return package


@pytest.mark.parametrize("version", [release.version for release in RELEASES])
@pytest.mark.parametrize("package_format", ["directory", "zip-rpa", "apk-rpyc"])
def test_save_survives_move_and_runtime_switch_without_project_snapshot(
    tmp_path, monkeypatch, display, version, package_format
):
    runtime_root = Path(os.environ.get("VN_STREAM_RUNTIME_ROOT", "var/runtimes")).resolve()
    sdk = runtime_root / "renpy" / version
    if not sdk.is_dir():
        pytest.fail(f"Explicit smoke run needs installed SDK {version}")
    data = tmp_path / "data"
    monkeypatch.setenv("VN_STREAM_BUNDLED_RUNTIMES", str(runtime_root))
    for name in ("DISPLAY", "XAUTHORITY"):
        monkeypatch.setenv(name, display[1][name])
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    monkeypatch.setenv("LIBGL_ALWAYS_SOFTWARE", "1")
    source = package_sample(tmp_path, sdk, package_format)
    original_bytes = source.read_bytes() if source.is_file() else None
    password = "compatibility-test-only"

    def client():
        return TestClient(create_app(data, [tmp_path], password))

    def wait_event(project, event):
        return wait_for(
            lambda: next((row for row in reversed(events(project)) if row["event"] == event), None),
            detail=lambda: (project / ".vn-stream-session.log").read_text()[-5000:],
        )

    with client() as api:
        sign_in(api, password)
        imported = api.post("/api/v2/projects", json={"path": str(source)})
        assert imported.status_code == 201, imported.text
        project_id = api_data(imported)["project_id"]
        project = Path(api_data(imported)["path"])
        # Packaged samples carry real SDK metadata and must use automatic match.
        selection = {"runtime_id": "renpy:" + version} if package_format == "directory" else {}
        if package_format != "directory":
            assert api_data(imported)["engine_version"] == version
            assert not list((project / "game").glob("*.rpy"))
        response = api.post(f"/api/v2/projects/{project_id}/start", json=selection)
        assert response.status_code == 200, response.text
        wait_event(project, "main_menu")
        key(display, "Return")
        assert wait_event(project, "ready")["counter"] == 42
        key(display, "p")

        def frame_complete():
            frame = project / "frame.png"
            content = frame.read_bytes() if frame.exists() else b""
            # Ren'Py creates the file before it finishes writing the PNG.
            return content.startswith(b"\x89PNG\r\n\x1a\n") and content.endswith(b"IEND\xaeB`\x82")

        wait_for(frame_complete)
        key(display, "s")
        wait_for(lambda: list((project / "game/saves").glob("smoke-1*.save")))
        assert (project / "game/saves/persistent").is_file()
        assert api_data(api.post("/api/v2/session/stop"))["state"] == "stopped"

    moved = tmp_path / "moved"
    project.rename(moved)
    with client() as api:
        sign_in(api, password)
        reindexed = api.post("/api/v2/projects", json={"path": str(moved)})
        assert reindexed.status_code == 201, reindexed.text
        assert api_data(reindexed)["project_id"] == project_id
        assert api.post(f"/api/v2/projects/{project_id}/start", json=selection).status_code == 200

        # Wait for a new process's event, rather than accepting the old menu event.
        def menu_count():
            return sum(row["event"] == "main_menu" for row in events(moved))

        wait_for(lambda: menu_count() >= 2)
        assert events(moved)[-1]["marker"] == "persisted"
        assert events(moved)[-1]["counter"] == 0
        key(display, "l")
        loaded = wait_event(moved, "loaded")
        assert loaded["counter"] == 42
        assert loaded["marker"] == "persisted"
        assert Path(loaded["savedir"]) == moved / "game/saves"
        assert Path(loaded["home"]) == moved / ".vn-stream/home"
        assert loaded["version"].startswith(version + ".")
        api.post("/api/v2/session/stop")

        library = ProjectCatalog(data, [tmp_path])
        alternative = "8.4.1" if version == "8.5.3" else "8.5.3"
        before = {p.name: p.read_bytes() for p in (moved / "game/saves").iterdir() if p.is_file()}
        library.select_runtime(project_id, alternative, override=alternative)
        assert not (data / "backups").exists()
        assert {
            p.name: p.read_bytes() for p in (moved / "game/saves").iterdir() if p.is_file()
        } == before
    if original_bytes is not None:
        assert source.read_bytes() == original_bytes
