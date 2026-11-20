import json
import time
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.auth_helpers import TestClient, sign_in
from vn_stream.api.app import create_app
from vn_stream.sessions.actions import ActionQueue
from vn_stream.sessions.supervisor import SessionManager

MESSAGES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/player-control.json").read_text()
)
SESSION = "a" * 32


def write_owner(path, connection="fixture-connection"):
    path.write_text(
        json.dumps(
            {
                "session_id": SESSION,
                "connection_id": connection,
                "epoch": MESSAGES["ready"]["epoch"],
                "connected": True,
                "at": time.monotonic(),
            }
        )
    )


@pytest.mark.parametrize("policy", ["renpy", "none"])
def test_control_frames_match_browser_contract(tmp_path, monkeypatch, policy):
    data = tmp_path / "data"
    path = data / "tasks/session"
    media = path / "media-owner.json"
    current = {"state": "running", "session_id": SESSION, "action_policy": policy}
    monkeypatch.setattr(SessionManager, "status", lambda self: current)

    with TestClient(create_app(data, [tmp_path], "secret")) as client:
        sign_in(client, "secret")
        queue = ActionQueue(path, SESSION, media)
        queue.environment()
        if policy == "none":
            write_owner(media)
        with client.websocket_connect(
            "wss://testserver/api/v2/session/control", headers={"origin": "https://testserver"}
        ) as control:
            control.send_json(
                {
                    "type": "hello",
                    "csrf_token": client.headers["X-CSRF-Token"],
                    "session_id": SESSION,
                    "connection_id": "fixture-connection",
                }
            )
            if policy == "renpy":
                assert control.receive_json() == MESSAGES["waiting"]
                ready = MESSAGES["ready"]
                queue.write(
                    queue.directory / "status.json",
                    {
                        "ready": True,
                        "skip": False,
                        "available": ["save", "quit"],
                        "quit_pending": False,
                        "last_quit_request": None,
                    },
                )
                write_owner(media)
                control.send_json({"type": "heartbeat", "epoch": ready["epoch"]})
                assert control.receive_json() == ready
                control.send_json(
                    {
                        "type": "action",
                        "action": "unsupported",
                        "request_id": "b" * 32,
                        "epoch": ready["epoch"],
                    }
                )
                assert control.receive_json() == MESSAGES["rejected"]
                assert control.receive_json() == ready
                write_owner(media, "replacement")
                control.send_json({"type": "heartbeat", "epoch": ready["epoch"]})
                assert control.receive_json() == MESSAGES["superseded"]
                with pytest.raises(WebSocketDisconnect) as error:
                    control.receive_json()
                assert error.value.code == 4009
            else:
                assert control.receive_json() == MESSAGES["stop"]
                current["state"] = "stopped"
                control.send_json({"type": "heartbeat", "epoch": MESSAGES["stop"]["epoch"]})
                assert control.receive_json() == MESSAGES["ended"]
