import sys
import time

from vn_stream.projects.metadata import game_info
from vn_stream.sessions.history import summary
from vn_stream.sessions.supervisor import SessionManager


def test_metadata_is_static_cached_and_refreshable(tmp_path):
    project = tmp_path / "project"
    (project / "game/gui").mkdir(parents=True)
    (project / "game/options.rpy").write_text(
        'define config.name = _("My Story")\ndefine config.version = "1.2"\nraise RuntimeError("must not execute")'
    )
    (project / "game/gui/window_icon.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    cache = tmp_path / "cache.json"
    info = game_info(project, cache)
    assert info["display_name"] == "My Story"
    assert info["release_version"] == "1.2"
    assert info["icon"] == "game/gui/window_icon.png"
    (project / "extra").write_bytes(b"abc")
    assert game_info(project, cache)["size_bytes"] == info["size_bytes"]
    assert game_info(project, cache, refresh=True)["size_bytes"] == info["size_bytes"] + 3


def test_history_is_idempotent_and_session_id_changes(tmp_path):
    manager = SessionManager(tmp_path / "state")
    try:
        first = manager.start(
            "one", [sys.executable, "-c", "import time;time.sleep(30)"], tmp_path, {}
        )
        assert manager.start("one", [], tmp_path, {})["session_id"] == first["session_id"]
        time.sleep(0.1)
        manager.stop()
        second = manager.start(
            "one", [sys.executable, "-c", "import time;time.sleep(30)"], tmp_path, {}
        )
        assert second["session_id"] != first["session_id"]
    finally:
        manager.stop()
    before = summary(tmp_path / "state", "one")
    assert before["play_seconds"] > 0
    assert before == summary(tmp_path / "state", "one")
    assert before["last_played_at"] is not None


def test_broken_statistics_never_ends_game_or_blocks_terminal_state(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "play-history.sqlite3").write_bytes(b"broken sqlite")
    manager = SessionManager(state)
    try:
        started = manager.start(
            "one", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, {}
        )
        assert started["state"] == "running"
    finally:
        result = manager.stop()
    assert result["state"] == "stopped"
    assert summary(state, "one")["play_seconds"] is None


def test_heartbeat_statistics_failure_does_not_kill_game(tmp_path):
    state = tmp_path / "state"
    manager = SessionManager(state)
    try:
        manager.start("one", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, {})
        (state / "play-history.sqlite3").unlink()
        (state / "play-history.sqlite3").mkdir()
        time.sleep(5.3)
        assert manager.status()["state"] == "running"
    finally:
        assert manager.stop()["state"] == "stopped"


def test_build_info_and_custom_icon_are_static_sources(tmp_path):
    import json

    project = tmp_path / "game-root"
    (project / "game/cache").mkdir(parents=True)
    (project / "game/cache/build_info.json").write_text(
        json.dumps({"name": "Eternum", "version": "0.9.0", "info": {}})
    )
    (project / "game/options.rpy").write_text('define config.window_icon = "brand.png"')
    (project / "game/brand.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    result = game_info(project, tmp_path / "cache.json")
    assert result["display_name"] == "Eternum"
    assert result["release_version"] == "0.9.0"
    assert result["icon"] == "game/brand.png"


def test_metadata_ignores_expressions_and_updates_old_cache(tmp_path):
    import json

    project = tmp_path / "Story"
    (project / "game/cache").mkdir(parents=True)
    (project / "game/cache/build_info.json").write_text(
        json.dumps({"name": "Build Name", "version": "1.2"})
    )
    (project / "game/options.rpy").write_text(
        'define config.name = _("Display Name")\ndefine config.version = __import__("os").getcwd()\ndefine config.window_icon = "../../outside.png"'
    )
    (tmp_path / "outside.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"display_name": None, "size_bytes": 0}))
    result = game_info(project, cache)
    assert result["display_name"] == "Display Name"
    assert result["release_version"] == "1.2"
    assert result["icon"] is None


def test_icon_resource_assembly_rejects_invalid_sizes():
    import struct

    from vn_stream.projects.windows_icon import assemble_icon

    image = b"png"
    group = struct.pack("<HHH", 0, 1, 1) + struct.pack(
        "<BBBBHHIH", 16, 16, 0, 0, 1, 32, len(image), 1
    )
    result = assemble_icon(group, {1: image})
    assert result[-3:] == image
    assert struct.unpack_from("<I", result, 18)[0] == 22
    assert assemble_icon(group, {1: b"too long"}) is None
    assert assemble_icon(group, {}) is None
    assert assemble_icon(b"bad", {}) is None
