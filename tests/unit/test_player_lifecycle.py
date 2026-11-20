import json

from vn_stream.sessions.actions import ActionQueue
from vn_stream.sessions.presence import Presence


def owner(path, *, session="s", connection="c", epoch="e", connected=True, at=1):
    path.write_text(
        json.dumps(
            dict(
                session_id=session,
                connection_id=connection,
                epoch=epoch,
                connected=connected,
                at=at,
            )
        )
    )


def test_initial_and_disconnected_deadline_and_reconnect(tmp_path):
    media = tmp_path / "media.json"
    p = Presence("s", media, now=0)
    assert not p.expired(59.99)
    assert p.expired(60)
    owner(media, at=50)
    assert p.heartbeat("c", "e", 50)
    assert not p.expired(109)
    owner(media, at=105)
    assert p.heartbeat("c", "e", 105)
    assert not p.expired(164)
    assert p.expired(165)


def test_control_only_or_old_generation_cannot_renew(tmp_path):
    media = tmp_path / "media.json"
    p = Presence("s", media, now=0)
    owner(media, connection="new", epoch="new-epoch", at=10)
    assert not p.heartbeat("old", "old-epoch", 10)
    assert p.heartbeat("new", "new-epoch", 10)
    owner(media, connection="new", epoch="new-epoch", connected=False, at=15)
    assert not p.heartbeat("new", "new-epoch", 15)
    assert p.expired(70)
    owner(media, session="other", at=69)
    assert not p.heartbeat("c", "e", 69)


def test_stale_media_report_is_not_a_connection(tmp_path):
    media = tmp_path / "media.json"
    owner(media, at=10)
    p = Presence("s", media, now=0)
    assert not p.heartbeat("c", "e", 20)
    assert p.expired(60)


def test_actions_are_whitelisted_deduplicated_and_bound_to_real_media(tmp_path):
    media = tmp_path / "media-owner.json"
    owner(media, at=10)
    q = ActionQueue(tmp_path, "s", media, clock=lambda: 10)
    first = q.submit("save", "a" * 32, "c", "e")
    assert first["state"] == "accepted"
    assert q.submit("save", "a" * 32, "c", "e") == first
    assert len(list((q.directory / "commands").glob("*.json"))) == 1
    assert q.submit("eval", "b" * 32, "c", "e")["state"] == "rejected"
    owner(media, epoch="new", at=10)
    assert q.submit("quit", "c" * 32, "c", "e")["state"] == "rejected"


def test_control_socket_requires_login_origin_and_csrf(tmp_path):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    with TestClient(create_app(tmp_path / "data", [tmp_path], "secret")) as c:
        with pytest.raises(WebSocketDisconnect) as error:
            with c.websocket_connect(
                "wss://testserver/api/v2/session/control", headers={"origin": "https://testserver"}
            ):
                pass
        assert error.value.code == 4401
        sign_in(c, "secret")
        with pytest.raises(WebSocketDisconnect) as error:
            with c.websocket_connect(
                "wss://testserver/api/v2/session/control",
                headers={"origin": "https://foreign.invalid"},
            ):
                pass
        assert error.value.code == 4403
        with pytest.raises(WebSocketDisconnect) as error:
            with c.websocket_connect(
                "wss://testserver/api/v2/session/control", headers={"origin": "https://testserver"}
            ) as ws:
                ws.send_json({"csrf_token": "wrong"})
                ws.receive_json()
        assert error.value.code == 4403


def test_adapter_uses_real_engine_exports_not_store_only_aliases(tmp_path, monkeypatch):
    import sys
    import textwrap
    from types import SimpleNamespace

    q = ActionQueue(tmp_path, "a" * 32, tmp_path / "media.json")
    env = q.environment()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = SimpleNamespace(periodic_callbacks=[], skipping=None)
    renpy = SimpleNamespace(
        game=SimpleNamespace(post_init=[]),
        exports=SimpleNamespace(can_rollback=lambda: False),
        store=SimpleNamespace(
            _skipping=True,
            Skip=lambda: SimpleNamespace(get_sensitive=lambda: False),
            ShowMenu=lambda name: SimpleNamespace(get_sensitive=lambda: True),
        ),
        display=SimpleNamespace(screen=SimpleNamespace(has_screen=lambda name: True)),
    )
    monkeypatch.setitem(sys.modules, "renpy", renpy)
    source = (
        __import__("importlib.resources", fromlist=["files"]).files("vn_stream.engines.renpy.hooks")
        / "native_actions.rpy"
    ).read_text()
    exec(textwrap.dedent("\n".join(source.splitlines()[2:])), {"config": config, "renpy": renpy})
    renpy.game.post_init[0]()
    config.periodic_callbacks[0]()
    assert q.status()["ready"] is True
    assert q.status()["skip"] is False
    assert "skip" not in q.status()["available"]


def test_control_malformed_hello_is_closed_without_server_error(tmp_path):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    with TestClient(create_app(tmp_path / "data", [tmp_path], "secret")) as c:
        sign_in(c, "secret")
        with pytest.raises(WebSocketDisconnect) as error:
            with c.websocket_connect(
                "wss://testserver/api/v2/session/control", headers={"origin": "https://testserver"}
            ) as ws:
                ws.send_json([])
                ws.receive_json()
        assert error.value.code == 4400


def test_control_forwards_native_dispatched_ack(tmp_path, monkeypatch):
    import time

    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app
    from vn_stream.sessions.supervisor import SessionManager

    sid = "a" * 32
    state = tmp_path / "data/tasks/session"
    with TestClient(create_app(tmp_path / "data", [tmp_path], "secret")) as c:
        sign_in(c, "secret")
        monkeypatch.setattr(
            SessionManager,
            "status",
            lambda self: {"state": "running", "session_id": sid, "action_policy": "renpy"},
        )
        owner(state / "media-owner.json", session=sid, at=time.monotonic())
        queue = ActionQueue(state, sid, state / "media-owner.json")
        queue.environment()
        with c.websocket_connect(
            "wss://testserver/api/v2/session/control", headers={"origin": "https://testserver"}
        ) as ws:
            ws.send_json(
                {"csrf_token": c.headers["X-CSRF-Token"], "session_id": sid, "connection_id": "c"}
            )
            ws.receive_json()
            ws.send_json({"type": "action", "action": "save", "request_id": "b" * 32, "epoch": "e"})
            reply = ws.receive_json()
            assert reply["state"] == "accepted"
            queue.write(
                queue.directory / "requests" / ("b" * 32 + ".json"),
                {**reply, "state": "dispatched"},
            )
            responses = [ws.receive_json() for _ in range(3)]
            assert any(
                r.get("type") == "action" and r.get("state") == "dispatched" for r in responses
            )


def test_control_support_is_stable_before_and_after_game_hook_reports(tmp_path, monkeypatch):
    import time

    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app
    from vn_stream.sessions.supervisor import SessionManager

    sid = "d" * 32
    for policy, supported in [
        ("renpy", {"rollback", "skip", "save", "load", "menu", "quit"}),
        ("none", {"stop"}),
    ]:
        data = tmp_path / policy
        state = data / "tasks/session"
        with TestClient(create_app(data, [tmp_path], "secret")) as client:
            sign_in(client, "secret")
            monkeypatch.setattr(
                SessionManager,
                "status",
                lambda self, policy=policy: {
                    "state": "running",
                    "session_id": sid,
                    "action_policy": policy,
                },
            )
            owner(state / "media-owner.json", session=sid, at=time.monotonic())
            queue = ActionQueue(state, sid, state / "media-owner.json")
            queue.environment()
            with client.websocket_connect(
                "wss://testserver/api/v2/session/control", headers={"origin": "https://testserver"}
            ) as ws:
                ws.send_json(
                    {
                        "csrf_token": client.headers["X-CSRF-Token"],
                        "session_id": sid,
                        "connection_id": "c",
                    }
                )
                first = ws.receive_json()
                assert set(first["supported"]) == supported
                queue.write(
                    queue.directory / "status.json",
                    {"ready": True, "available": ["menu", "quit"], "skip": False},
                )
                ws.send_json({"type": "heartbeat", "epoch": "e"})
                second = ws.receive_json()
                assert set(second["supported"]) == supported
                assert second["available"] == (["menu", "quit"] if policy == "renpy" else ["stop"])
