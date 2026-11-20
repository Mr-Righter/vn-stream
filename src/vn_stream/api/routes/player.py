"""Authenticated application control, bound to the real Selkies media owner."""

import asyncio
import re
import secrets
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from vn_stream.auth.constants import COOKIE
from vn_stream.sessions.actions import ACTIONS, ActionQueue
from vn_stream.sessions.presence import media_owner


def player_routes(app, sessions, state):
    router = APIRouter()

    def submit_action(queue, action, request_id, connection, epoch):
        with sessions.guard:
            return queue.submit(action, request_id, connection, epoch)

    def stop_native(session_id, connection, epoch):
        with sessions.guard:
            current = sessions.status()
            owner = media_owner(state / "media-owner.json", session_id, time.monotonic())
            if (
                current.get("session_id") == session_id
                and owner
                and owner.get("epoch") == epoch
                and owner.get("connection_id") == connection
            ):
                sessions.stop()

    @router.websocket("/api/v2/session/control")
    async def control(ws: WebSocket):
        token = ws.cookies.get(COOKIE)
        auth = await run_in_threadpool(app.state.auth.lookup, token)
        if not auth:
            await ws.close(code=4401)
            return
        expected_origin = (
            (
                "https"
                if ws.url.scheme == "wss" or ws.headers.get("x-forwarded-proto") == "https"
                else "http"
            )
            + "://"
            + ws.headers.get("host", "")
        )
        if ws.headers.get("origin") != expected_origin:
            await ws.close(code=4403)
            return
        await ws.accept()
        try:
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            if not isinstance(hello, dict):
                await ws.close(code=4400)
                return
            supplied = hello.get("csrf_token", "")
            if not isinstance(supplied, str) or not secrets.compare_digest(
                supplied.encode(), auth["csrf_token"].encode()
            ):
                await ws.close(code=4403)
                return
            session_id, connection = hello.get("session_id"), hello.get("connection_id")
            if not isinstance(connection, str) or not 1 <= len(connection) <= 64:
                await ws.close(code=4403)
                return
            if not isinstance(session_id, str) or not re.fullmatch("[a-f0-9]{32}", session_id):
                await ws.close(code=4403)
                return
            queue = ActionQueue(state, session_id, state / "media-owner.json")
            bound_epoch = None
            pending = set()
            while True:
                if not await run_in_threadpool(app.state.auth.lookup, token):
                    await ws.close(code=4401)
                    return
                for request_id in list(pending):
                    result = await run_in_threadpool(queue.result, request_id)
                    if result and result.get("state") != "accepted":
                        await ws.send_json({"type": "action", **result})
                        pending.discard(request_id)
                current = await run_in_threadpool(sessions.status)
                if current.get("session_id") != session_id or current["state"] != "running":
                    await ws.send_json({"type": "ended"})
                    await ws.close()
                    return
                owner = await run_in_threadpool(
                    media_owner, state / "media-owner.json", session_id, time.monotonic()
                )
                matching = owner and owner.get("connection_id") == connection
                if bound_epoch and owner and not matching:
                    await ws.send_json({"type": "superseded"})
                    await ws.close(code=4009)
                    return
                if matching:
                    bound_epoch = owner["epoch"]
                await ws.send_json(
                    {
                        "type": "status",
                        "controller": bool(matching),
                        "epoch": bound_epoch,
                        **(
                            await run_in_threadpool(queue.status)
                            if current.get("action_policy") == "renpy"
                            else dict(ready=True, available=["stop"], skip=False)
                        ),
                        "supported": (
                            sorted(ACTIONS) if current.get("action_policy") == "renpy" else ["stop"]
                        ),
                    }
                )
                try:
                    message = await asyncio.wait_for(ws.receive_json(), 1)
                except TimeoutError:
                    continue
                if not isinstance(message, dict):
                    await ws.close(code=4400)
                    return
                if matching and message.get("epoch") == bound_epoch:
                    if message.get("type") == "heartbeat":
                        await run_in_threadpool(
                            sessions.heartbeat, session_id, connection, bound_epoch
                        )
                    elif message.get("type") == "action":
                        if current.get("action_policy") != "renpy":
                            if message.get("action") == "stop":
                                await run_in_threadpool(
                                    stop_native, session_id, connection, bound_epoch
                                )
                                return
                            await ws.send_json(
                                {
                                    "type": "action",
                                    "state": "rejected",
                                    "request_id": message.get("request_id"),
                                }
                            )
                            continue
                        reply = await run_in_threadpool(
                            submit_action,
                            queue,
                            message.get("action"),
                            message.get("request_id", ""),
                            connection,
                            bound_epoch,
                        )
                        if reply["state"] == "accepted":
                            pending.add(reply["request_id"])
                        await ws.send_json({"type": "action", **reply})
        except (WebSocketDisconnect, TimeoutError, ValueError, TypeError, RuntimeError):
            return

    return router
