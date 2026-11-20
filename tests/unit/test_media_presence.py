import json
from types import SimpleNamespace

from vn_stream.streaming.media_presence import report_owner


def test_only_real_controller_reports_and_old_socket_cannot_clear_new_owner(tmp_path):
    state = tmp_path
    (state / "session.json").write_text(json.dumps({"state": "running", "session_id": "a" * 32}))
    first, second = object(), object()
    server = SimpleNamespace(display_clients={"primary": {"ws": first}})
    permissions = {first: {"role": "controller"}, second: {"role": "controller"}}
    assert report_owner(server, first, "a" * 32, "first", "epoch1", permissions, state, 1)
    server.display_clients["primary"]["ws"] = second
    assert report_owner(server, second, "a" * 32, "second", "epoch2", permissions, state, 2)
    report_owner(server, first, "a" * 32, "first", "epoch1", permissions, state, 3, closing=True)
    assert json.loads((state / "media-owner.json").read_text())["epoch"] == "epoch2"
    report_owner(server, second, "a" * 32, "second", "epoch2", permissions, state, 4, closing=True)
    assert json.loads((state / "media-owner.json").read_text())["connected"] is False


def test_viewer_and_wrong_game_never_report_authority(tmp_path):
    (tmp_path / "session.json").write_text(json.dumps({"state": "running", "session_id": "a" * 32}))
    ws = object()
    server = SimpleNamespace(display_clients={"primary": {"ws": ws}})
    assert not report_owner(
        server, ws, "b" * 32, "client", "epoch", {ws: {"role": "controller"}}, tmp_path, 1
    )
    assert not report_owner(
        server, ws, "a" * 32, "client", "epoch", {ws: {"role": "viewer"}}, tmp_path, 1
    )
    assert not (tmp_path / "media-owner.json").exists()
