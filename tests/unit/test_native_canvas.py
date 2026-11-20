import json

import pytest

from vn_stream.streaming.native_canvas import NativeCanvas, canvas_geometry


@pytest.mark.parametrize(
    "source,expected",
    [
        ((800, 600), (800, 600)),
        ((801, 451), (801, 451)),
        ((1920, 1080), (1920, 1080)),
        ((5120, 2880), (3840, 2160)),
        ((3000, 4000), (2160, 2880)),
        ((10000, 1000), (3840, 384)),
    ],
)
def test_native_geometry_never_upscales_and_caps_both_axes(source, expected):
    assert canvas_geometry(*source) == expected


@pytest.mark.parametrize("size", [(0, 1080), (-1, 1), (True, 1080), (1920.0, 1080), (99999999, 1)])
def test_invalid_reports_are_rejected(size):
    with pytest.raises(ValueError):
        canvas_geometry(*size)


def report(canvas, session="one", width=801, height=451):
    canvas.report.write_text(json.dumps({"session_id": session, "width": width, "height": height}))


def test_realized_padding_is_recorded_and_ack_bound_to_session(tmp_path):
    requests = []
    canvas = NativeCanvas(
        tmp_path, "one", apply=lambda w, h: requests.append((w, h)) or (808, 452), now=0
    )
    report(canvas)
    assert canvas.poll(1)
    assert requests == [(808, 452)]
    assert canvas.status["content"] == [801, 451]
    assert canvas.status["capture"] == [808, 452]
    assert canvas.status["state"] == "native"
    assert json.loads(canvas.ack.read_text())["session_id"] == "one"
    assert not canvas.poll(2)


def test_deadline_is_provisional_and_late_same_session_can_upgrade(tmp_path):
    calls = []
    canvas = NativeCanvas(tmp_path, "one", apply=lambda w, h: calls.append((w, h)) or (w, h), now=0)
    assert not canvas.poll(4.99)
    assert canvas.poll(5)
    assert canvas.status["state"] == "fallback"
    assert canvas.status["notice"]
    report(canvas, width=2560, height=1440)
    assert canvas.poll(7)
    assert canvas.status["revision"] == 2
    assert canvas.status["state"] == "native"
    assert calls == [(1920, 1080), (2560, 1440)]
    assert not canvas.poll(8)


def test_first_observation_after_late_upgrade_has_no_expired_fallback_notice(tmp_path):
    canvas = NativeCanvas(tmp_path, "one", apply=lambda w, h: (w, h), now=0)
    assert canvas.poll(5)
    report(canvas, width=1024, height=768)
    assert canvas.poll(7)
    for observed in (canvas.status, json.loads(canvas.ack.read_text())):
        assert observed["state"] == "native"
        assert observed["revision"] == 2
        assert observed["capture"] == [1024, 768]
        assert observed["diagnostic"] is None
        assert observed["notice"] is None


def test_late_upgrade_keeps_new_ack_write_failure_notice(tmp_path):
    canvas = NativeCanvas(tmp_path, "one", apply=lambda w, h: (w, h), now=0)
    assert canvas.poll(5)
    fallback_notice = canvas.status["notice"]
    canvas.ack.unlink()
    canvas.ack.mkdir()
    report(canvas, width=1024, height=768)
    assert canvas.poll(7)
    assert canvas.status["state"] == "native"
    assert canvas.status["capture"] == [1024, 768]
    assert canvas.status["diagnostic"] == "ack_write_failed"
    assert canvas.status["notice"] and canvas.status["notice"] != fallback_notice


def test_old_session_cannot_resize_new_session(tmp_path):
    calls = []
    old = NativeCanvas(tmp_path, "old", apply=lambda w, h: (w, h), now=0)
    current = NativeCanvas(
        tmp_path, "new", apply=lambda w, h: calls.append((w, h)) or (w, h), now=0
    )
    report(old, session="old")
    report(current, session="old")
    current.poll(1)
    assert calls == []
    assert current.status["state"] == "pending"
    report(current, session="new")
    current.poll(2)
    assert calls == [(808, 452)]


def test_apply_failure_falls_back_once_without_claiming_requested_dimensions(tmp_path):
    calls = []

    def apply(w, h):
        calls.append((w, h))
        if len(calls) == 1:
            raise OSError("test display unavailable")
        return w, h

    canvas = NativeCanvas(tmp_path, "one", apply=apply, now=0)
    report(canvas)
    canvas.poll(1)
    assert calls == [(808, 452), (1920, 1080)]
    assert canvas.status["capture"] == [1920, 1080]
    assert canvas.status["diagnostic"] == "apply_failed"
    assert not canvas.poll(6)


def test_rendered_window_and_viewport_are_session_bound(tmp_path):
    canvas = NativeCanvas(tmp_path, "one", apply=lambda w, h: (w, h), now=0)
    report(canvas)
    canvas.poll(1)
    rendered = canvas.directory / "render.json"
    data = {"session_id": "old", "physical": [808, 452], "viewport": [3.5, 0.5, 801, 451]}
    rendered.write_text(json.dumps(data))
    assert not canvas.poll(2)
    assert "render" not in canvas.status
    data["session_id"] = "one"
    rendered.write_text(json.dumps(data))
    assert canvas.poll(3)
    assert canvas.status["render"]["physical"] == [808, 452]
    assert canvas.status["render"]["viewport"] == [3.5, 0.5, 801, 451]
    assert not canvas.poll(4)


def test_ack_write_failure_does_not_abort_game_display_supervision(tmp_path):
    canvas = NativeCanvas(tmp_path, "one", apply=lambda w, h: (w, h), now=0)
    canvas.ack.mkdir()
    report(canvas)
    assert canvas.poll(1)
    assert canvas.status["capture"] == [808, 452]
    assert canvas.status["diagnostic"] == "ack_write_failed"


def test_deep_json_report_is_optional_failure_not_game_failure(tmp_path):
    canvas = NativeCanvas(tmp_path, "one", apply=lambda w, h: (w, h), now=0)
    canvas.report.write_text("[" * 1000 + "]" * 1000)
    assert canvas.poll(1)
    assert canvas.status["state"] == "fallback"
    assert canvas.status["diagnostic"] == "invalid_report"


def test_extreme_render_viewport_does_not_escape_optional_probe_boundary(tmp_path):
    canvas = NativeCanvas(tmp_path, "one", apply=lambda w, h: (w, h), now=0)
    report(canvas)
    canvas.poll(1)
    data = {"session_id": "one", "physical": [808, 452], "viewport": [10**1000, 0, 801, 451]}
    (canvas.directory / "render.json").write_text(json.dumps(data))
    assert not canvas.poll(2)
    assert canvas.status["state"] == "native"


@pytest.mark.parametrize(
    "state,capture,fullscreen",
    [
        ("fallback", [1920, 1080], True),
        ("native", [1920, 1080], True),
        ("native", [2568, 1442], False),
    ],
)
def test_trusted_hook_never_places_content_window_larger_than_acknowledged_display(
    tmp_path, monkeypatch, state, capture, fullscreen
):
    import sys
    import textwrap
    import types

    config = types.SimpleNamespace(
        screen_width=2561,
        screen_height=1441,
        display_start_callbacks=[],
        interact_callbacks=[],
        periodic_callbacks=[],
        gl2_modify_window_flags=None,
    )
    preferences = types.SimpleNamespace(
        fullscreen=None, window_position_layout=None, window_position=None
    )
    renpy = types.SimpleNamespace(
        config=config, game=types.SimpleNamespace(post_init=[], preferences=preferences)
    )
    monkeypatch.setitem(sys.modules, "renpy", renpy)
    pygame = types.SimpleNamespace(
        display=types.SimpleNamespace(
            init=lambda: None,
            get_num_video_displays=lambda: 1,
            get_display_bounds=lambda i: (0, 0, capture[0], capture[1]),
        )
    )
    monkeypatch.setitem(sys.modules, "pygame_sdl2", pygame)
    ack = tmp_path / "ack.json"
    ack.write_text(json.dumps({"session_id": "one", "state": state, "capture": capture}))
    monkeypatch.setenv("VN_STREAM_CANVAS_REPORT", str(tmp_path / "report.json"))
    monkeypatch.setenv("VN_STREAM_CANVAS_ACK", str(ack))
    monkeypatch.setenv("VN_STREAM_SESSION_ID", "one")
    code = (
        (
            __import__("importlib.resources", fromlist=["files"]).files(
                "vn_stream.engines.renpy.hooks"
            )
            / "native_canvas.rpy"
        )
        .read_text()
        .split("init 999 python:\n", 1)[1]
    )
    namespace = {
        "config": config,
        "renpy": renpy,
        "_preferences": preferences,
        "dict": type("RevertableDict", (dict,), {}),
        "list": type("RevertableList", (list,), {}),
    }
    exec(compile(textwrap.dedent(code), "trusted-native-hook", "exec"), namespace)
    renpy.game.post_init[0]()
    assert preferences.fullscreen is fullscreen
    assert bool(config.display_start_callbacks) is not fullscreen
    if not fullscreen:
        assert preferences.window_position_layout == ((0, 0, *capture),)
        assert preferences.window_position == (3, 0)
    width, height = config.adjust_view_size(*capture)
    assert width <= capture[0] and height <= capture[1]
