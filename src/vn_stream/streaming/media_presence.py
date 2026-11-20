"""Small lifecycle hook for the pinned Selkies server; media bytes remain untouched."""

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4


def report_owner(
    server, ws, expected_session, connection, epoch, permissions, state, now, *, closing=False
):
    target = state / "media-owner.json"
    try:
        game = json.loads((state / "session.json").read_text())
        session = game.get("session_id")
        if game.get("state") != "running" or (expected_session and expected_session != session):
            return False
        active = (
            not closing
            and server.display_clients.get("primary", {}).get("ws") is ws
            and permissions.get(ws, {}).get("role") == "controller"
        )
        if not active:
            try:
                previous = json.loads(target.read_text())
            except (OSError, ValueError):
                return False
            if previous.get("epoch") != epoch:
                return False
        value = dict(
            session_id=session,
            connection_id=connection,
            epoch=epoch,
            connected=active,
            at=now,
            wall=time.time(),
        )
        temporary = target.with_suffix(".pending")
        temporary.write_text(json.dumps(value))
        temporary.replace(target)
        return active
    except (OSError, ValueError, TypeError):
        return False


async def watch(server, ws, request, permissions):
    state = Path(os.environ.get("VN_STREAM_STATE", "/data/tasks/session"))
    connection = request.query.get("vn_connection", "")
    expected = request.query.get("vn_stream_session", "")
    epoch = uuid4().hex  # Only this real server-side socket owns this generation.
    if not connection or len(connection) > 64 or (expected and len(expected) != 32):
        await ws.close(code=1008, message=b"Missing game connection binding")
        return
    try:
        while not ws.closed:
            report_owner(
                server, ws, expected, connection, epoch, permissions, state, time.monotonic()
            )
            await asyncio.sleep(0.5)
    finally:
        report_owner(
            server,
            ws,
            expected,
            connection,
            epoch,
            permissions,
            state,
            time.monotonic(),
            closing=True,
        )
