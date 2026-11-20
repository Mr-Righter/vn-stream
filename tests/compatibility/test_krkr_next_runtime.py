"""Exercise the real Linux Next host in an isolated, self-authored project."""

import io
import os
import shutil
import struct
import subprocess
import time
import uuid
import wave
from pathlib import Path

import pytest
from PIL import Image

from tests.native_asset_fixtures import xp3

pytestmark = pytest.mark.skipif(
    os.environ.get("VN_STREAM_NEXT_RUNTIME_TEST") != "1",
    reason="requires the isolated vn-engine-layout-verify container and built Next host",
)

ROOT = Path(__file__).resolve().parents[2]
CONTAINER = "vn-engine-layout-verify"


def command(*args):
    return subprocess.check_output(
        ["docker", "exec", "-u", "abc", "-e", "DISPLAY=:1", CONTAINER, *args], text=True
    ).strip()


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("native host condition did not become ready")


@pytest.fixture
def modal_host(request):
    command("xrandr", "--output", "screen", "--mode", "1920x1080")
    name = "Next-Test-" + uuid.uuid4().hex[:10]
    folder = ROOT / "var/engine-layout/games/kirikiri" / name
    folder.mkdir()
    (folder / ".vn-stream/home").mkdir(parents=True)
    script = (ROOT / "tests/fixtures/krkr-smoke/modal.tjs").read_text()
    mode = getattr(request, "param", "normal")
    if mode == "nested":
        script = script.replace(
            "class Confirmation extends Window {",
            """class Confirmation extends Window {
    function onKeyDown(key, shift) {
        if (key != 78) return;
        var child = new global.Confirmation();
        child.showModal();
        [child.result ? "yes" : "no"].save("nested.txt", "utf-8");
        invalidate child;
    }
""",
        )
    elif mode == "veto":
        script = "global.allowClose = false;\n" + script.replace(
            "class Confirmation extends Window {",
            """class Confirmation extends Window {
    function onCloseQuery(canClose) { super.onCloseQuery(global.allowClose); }
    function onKeyDown(key, shift) { if (key == 86) global.allowClose = true; }
""",
        )
    elif mode == "mouse-open":
        script = script.replace(
            "class ProbeWindow extends Window {",
            """class ProbeWindow extends Window {
    function onMouseDown(x, y, button, shift) {
        var dialog = new global.Confirmation();
        ["waiting"].save("ready.txt", "utf-8");
        dialog.showModal();
        [dialog.result ? "yes" : "no"].save("result.txt", "utf-8");
        invalidate dialog;
    }
""",
        )
    elif mode == "plugin":
        script = (
            """Plugins.link("kirikiroid2.dll");
Storages.setTextEncoding("UTF-8");
[_str_ord("\\x4e2d")].save("plugin.txt", "utf-8");
"""
            + script
        )
    elif mode == "layer-open":
        script += """
class Opener extends Layer {
    var tag;
    function Opener(parent, x, label) {
        super.Layer(main, parent);
        tag = label;
        setSize(200, 150); setImageSize(200, 150); setPos(x, 0);
        fillRect(0, 0, 200, 150, 0xff008000);
        hitThreshold = 0; visible = true;
    }
    function onMouseDown(x, y, button, shift) {
        [tag].save("hit.txt", "utf-8");
        if (tag == "first") {
            var dialog = new global.Confirmation();
            dialog.showModal();
            invalidate dialog;
        }
    }
}
global.first = new Opener(main.body, 0, "first");
global.second = new Opener(main.body, 300, "second");
"""
    elif mode == "alpha":
        script = (
            """Plugins.link("AlphaMovie.dll");
class DerivedMovie extends AlphaMovie {
    function DerivedMovie() {}
    function stop() { super.stop(); }
}
var movie = new DerivedMovie();
movie.stop();
["stopped"].save("alpha.txt", "utf-8");
invalidate movie;
"""
            + script
        )
    elif mode == "alpha-animation":
        sample = ROOT / "var/runtime-candidates/alpha-corpus/lovely-0.amv"
        if not sample.is_file():
            pytest.skip("requires the private read-only AMV verification corpus")
        shutil.copyfile(sample, folder / "sample.amv")
        script += """
Plugins.link("AlphaMovie.dll");
global.movie = new AlphaMovie();
movie.open("sample.amv");
[movie.numOfFrame, movie.screenWidth, movie.screenHeight].save("metadata.txt", "utf-8");
global.drawLayer = new Layer(main, main.body);
drawLayer.visible = true;
movie.play();
global.animation = new Timer(function() {
    var frame = movie.showNextImage(drawLayer);
    if (frame >= 3) {
        [frame].save("decoded.txt", "utf-8");
        animation.enabled = false;
        movie.stop();
    }
}, "");
animation.interval = 30;
animation.enabled = true;
"""
    elif mode == "startup":
        script += """
var startupDialog = new global.Confirmation();
["waiting"].save("ready.txt", "utf-8");
try { startupDialog.showModal(); } catch (e) { [e.message].save("error.txt", "utf-8"); throw e; }
[startupDialog.result ? "yes" : "no"].save("result.txt", "utf-8");
invalidate startupDialog;
"""
    if mode == "audio-archive":
        tone = io.BytesIO()
        with wave.open(tone, "wb") as output:
            output.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            output.writeframes(struct.pack("<hh", 1000, -1000) * 11025)
        script += 'global.sound = new WaveSoundBuffer(main); sound.open("tone.wav"); sound.looping = true; sound.play();'
        (folder / "data.xp3").write_bytes(
            xp3(
                [
                    ("startup.tjs", [(script.encode("utf-16"), True)]),
                    ("tone.wav", [(tone.getvalue(), False)]),
                ]
            )
        )
    else:
        (folder / "startup.tjs").write_text(script)
    project = "/games/kirikiri/" + name
    entry = project + "/data.xp3" if mode == "audio-archive" else project
    log_path = ROOT / "var/engine-layout" / (name + ".log")
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                "docker",
                "exec",
                "-u",
                "abc",
                "-e",
                "DISPLAY=:1",
                "-e",
                "HOME=" + project + "/.vn-stream/home",
                "-w",
                project,
                CONTAINER,
                "/data/tmp/next-host/runner",
                "--game",
                entry,
            ],
            stdout=log,
            stderr=log,
        )
        try:
            wait_until(
                lambda: (
                    (folder / "ready.txt").exists()
                    or "StartApplication returned successfully" in log_path.read_text()
                )
            )
            window = command("xdotool", "getactivewindow")
            assert command("xdotool", "getwindowname", window) == "KrKr2-Next"
            command("xdotool", "windowmove", window, "0", "0")
            yield folder, window, process, log_path
        finally:
            if process.poll() is None:
                command("xdotool", "key", "alt+F4")
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Only this fixture's process is eligible for forced cleanup.
                    result = subprocess.run(
                        [
                            "docker",
                            "exec",
                            CONTAINER,
                            "pgrep",
                            "-f",
                            "^/data/tmp/next-host/runner --game " + entry + "$",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    for pid in result.stdout.split():
                        subprocess.run(
                            ["docker", "exec", CONTAINER, "kill", "-KILL", pid], check=True
                        )
                    process.wait(timeout=5)


def frame_pixel(folder, window, x, y):
    image_path = "/data/tmp/" + folder.name + ".png"
    command(
        "ffmpeg",
        "-loglevel",
        "error",
        "-f",
        "x11grab",
        "-window_id",
        window,
        "-video_size",
        "1280x720",
        "-i",
        ":1",
        "-frames:v",
        "1",
        "-y",
        image_path,
    )
    with Image.open(ROOT / "var/engine-layout/data/tmp" / (folder.name + ".png")) as image:
        return image.convert("RGB").getpixel((x, y))


def test_modal_waits_for_answer_and_restores_parent(modal_host):
    folder, window, process, log = modal_host
    command("xdotool", "key", "m")
    time.sleep(0.7)
    assert not (folder / "result.txt").exists(), "showModal returned without a user answer"
    assert process.poll() is None, log.read_text()[-1500:]
    assert frame_pixel(folder, window, 640, 200) == (0, 0, 255), "modal was not composited"
    assert frame_pixel(folder, window, 200, 100) == (32, 32, 32), "parent disappeared"
    command("xdotool", "mousemove", "--window", window, "505", "300", "click", "1")
    result = wait_until(lambda: (folder / "result.txt").exists())
    assert result
    assert (folder / "result.txt").read_text(encoding="utf-16").strip() == "yes"
    wait_until(lambda: frame_pixel(folder, window, 640, 200) == (0, 170, 0))
    command("xdotool", "key", "m")
    time.sleep(0.3)
    command("xdotool", "mousemove", "--window", window, "775", "300", "click", "1")
    wait_until(lambda: (folder / "result.txt").read_text(encoding="utf-16").strip() == "no")
    assert process.poll() is None, "closing a child window terminated the project"


def read_result(folder, name="result.txt"):
    path = folder / name
    return path.read_text(encoding="utf-16").strip() if path.exists() else None


@pytest.mark.parametrize("modal_host", ["nested"], indirect=True)
def test_nested_dialog_keeps_outer_wait_and_input_ownership(modal_host):
    folder, window, process, _ = modal_host
    command("xdotool", "key", "m")
    time.sleep(0.2)
    command("xdotool", "key", "n")
    time.sleep(0.2)
    assert read_result(folder) is None
    command("xdotool", "mousemove", "--window", window, "505", "300", "click", "1")
    wait_until(lambda: read_result(folder, "nested.txt") == "yes")
    assert read_result(folder) is None, "answering a nested dialog also closed its parent"
    command("xdotool", "mousemove", "--window", window, "775", "300", "click", "1")
    wait_until(lambda: read_result(folder) == "no")
    assert process.poll() is None


@pytest.mark.parametrize("modal_host", ["veto"], indirect=True)
def test_close_query_can_keep_modal_open(modal_host):
    folder, window, process, _ = modal_host
    command("xdotool", "key", "m")
    time.sleep(0.2)
    command("xdotool", "mousemove", "--window", window, "505", "300", "click", "1")
    time.sleep(0.2)
    assert read_result(folder) is None, "close-query veto was ignored"
    command("xdotool", "key", "v")
    command("xdotool", "mousemove", "--window", window, "775", "300", "click", "1")
    wait_until(lambda: read_result(folder) == "no")
    assert process.poll() is None


@pytest.mark.parametrize("modal_host", ["startup"], indirect=True)
def test_startup_modal_can_receive_input_before_open_returns(modal_host):
    folder, window, process, log = modal_host
    assert read_result(folder, "ready.txt") == "waiting"
    assert read_result(folder) is None
    command("xdotool", "mousemove", "--window", window, "505", "300", "click", "1")
    wait_until(lambda: read_result(folder) == "yes")
    wait_until(lambda: "StartApplication returned successfully" in log.read_text())
    assert process.poll() is None


def test_closing_host_while_modal_waits_exits_without_hanging(modal_host):
    folder, _, process, _ = modal_host
    command("xdotool", "key", "m")
    time.sleep(0.2)
    assert read_result(folder) is None
    command("xdotool", "key", "alt+F4")
    assert process.wait(timeout=5) == 0


@pytest.mark.parametrize("modal_host", ["mouse-open"], indirect=True)
def test_opening_pointer_release_does_not_answer_dialog(modal_host):
    folder, window, process, _ = modal_host
    command("xdotool", "mousemove", "--window", window, "505", "300", "mousedown", "1")
    wait_until(lambda: read_result(folder, "ready.txt") == "waiting")
    command("xdotool", "mouseup", "1")
    time.sleep(0.3)
    assert read_result(folder) is None, "parent press leaked into the modal as a click"
    command("xdotool", "mousemove", "--window", window, "505", "300", "click", "1")
    wait_until(lambda: read_result(folder) == "yes")
    assert process.poll() is None


@pytest.mark.parametrize("modal_host", ["plugin"], indirect=True)
def test_project_text_plugin_provides_real_decoding_functions(modal_host):
    folder, _, _, _ = modal_host
    assert read_result(folder, "plugin.txt") == "20013"


@pytest.mark.parametrize("modal_host", ["alpha"], indirect=True)
def test_inherited_alpha_movie_can_stop_before_open(modal_host):
    folder, _, _, _ = modal_host
    assert read_result(folder, "alpha.txt") == "stopped"


@pytest.mark.parametrize("modal_host", ["audio-archive"], indirect=True)
def test_closing_packed_project_stops_audio_and_archive_workers(modal_host):
    _, _, process, _ = modal_host
    command("xdotool", "key", "alt+F4")
    assert process.wait(timeout=5) == 0


@pytest.mark.parametrize("modal_host", ["alpha-animation"], indirect=True)
def test_alpha_movie_decodes_real_frames_into_game_layer(modal_host):
    folder, _, process, log = modal_host
    wait_until(lambda: (folder / "decoded.txt").exists())
    assert int(read_result(folder, "decoded.txt")) >= 3
    assert process.poll() is None, log.read_text()[-1500:]


@pytest.mark.parametrize("modal_host", ["layer-open"], indirect=True)
def test_modal_releases_parent_layer_capture(modal_host):
    folder, window, process, _ = modal_host
    command("xdotool", "mousemove", "--window", window, "310", "100", "mousedown", "1")
    wait_until(lambda: read_result(folder, "hit.txt") == "first")
    command("xdotool", "mouseup", "1")
    command("xdotool", "mousemove", "--window", window, "775", "300", "click", "1")
    time.sleep(0.2)
    command("xdotool", "mousemove", "--window", window, "760", "100", "click", "1")
    wait_until(lambda: read_result(folder, "hit.txt") == "second")
    assert process.poll() is None
