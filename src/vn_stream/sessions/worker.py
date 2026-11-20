"""Own the game group independently, so API death cannot bypass cleanup.

This file is launched directly by the service interpreter. It deliberately uses
only the standard library, without importing any game or SDK Python modules.
"""

import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4


def process_scope() -> dict | None:
    """Identify a host boot and PID namespace, without trusting a persisted PID."""
    try:
        return {
            "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "namespace": Path("/proc/self/ns/pid").stat().st_ino,
            "init_start": Path("/proc/1/stat").read_text().split(") ", 1)[1].split()[19],
        }
    except (OSError, IndexError):
        return None


def write_state(path: Path, state: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def exited(process: subprocess.Popen) -> bool:
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def finish(process: subprocess.Popen, *, terminate: bool) -> int:
    if terminate and not exited(process):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while not exited(process) and time.monotonic() < deadline:
            time.sleep(0.02)
    # Do not reap the leader first: its PID must remain reserved while signalling
    # descendants, including children that ignored TERM after their parent exited.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return process.wait()


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    from history import record_session

    lease_fd = int(sys.argv[1])
    config = json.loads(sys.stdin.readline())
    config.setdefault("session_id", uuid4().hex)
    record = Path(config["state"]) / "session.json"
    game = None
    canvas = None
    actions = None
    presence = None
    announced = False
    reason = None
    started = None
    tick = 0.0

    def request_stop(signum, frame):
        nonlocal reason
        reason = "supervisor_signal"

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    engine_id = config["env"].get("VN_STREAM_ENGINE_ID", "renpy")
    canvas_strategy = config["env"].get("VN_STREAM_CANVAS_STRATEGY", "window")
    action_strategy = config["env"].get("VN_STREAM_ACTION_STRATEGY", "none")
    state = {
        "engine_id": engine_id,
        "action_policy": action_strategy,
        "project_id": config["project_id"],
        "session_id": config["session_id"],
        "state": "running",
        "exit_code": None,
        "scope": process_scope(),
    }
    try:
        # Publish intent while holding the lease, before any game can exist.
        # A crash between Popen and the next write must not leave an old stopped record.
        write_state(record, state)
        if os.environ.get("VN_STREAM_NATIVE_DISPLAY") == "selkies":
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from vn_stream.streaming.native_canvas import NativeCanvas

            if canvas_strategy == "engine_report":
                canvas = NativeCanvas(Path(config["state"]), config["session_id"])
            else:
                from vn_stream.streaming.window_canvas import WindowCanvas

                size = config["env"].get("VN_STREAM_STATIC_CANVAS")
                dimensions = tuple(map(int, size.split(","))) if size else None
                canvas = WindowCanvas(Path(config["state"]), config["session_id"], dimensions)
            config["env"].update(canvas.environment())
            state["display"] = canvas.status
        if os.environ.get("VN_STREAM_NATIVE_DISPLAY") == "selkies":
            from vn_stream.sessions.actions import ActionQueue
            from vn_stream.sessions.presence import Presence

            presence = Presence(
                config["session_id"],
                Path(config["state"]) / "media-owner.json",
                now=time.monotonic(),
            )
            if action_strategy == "renpy":
                actions = ActionQueue(
                    Path(config["state"]),
                    config["session_id"],
                    Path(config["state"]) / "media-owner.json",
                )
                search = (
                    Path(config["env"]["RENPY_SEARCHPATH"])
                    if config["env"].get("RENPY_SEARCHPATH")
                    else None
                )
                config["env"].update(actions.environment(search))
        with (Path(config["cwd"]) / ".vn-stream-session.log").open("ab") as log:
            game = subprocess.Popen(
                config["command"],
                cwd=config["cwd"],
                env={**os.environ, **config["env"]},
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                # If this supervisor itself is killed, the live game still holds
                # the lease. A new API must fail closed instead of writing twice.
                pass_fds=(lease_fd,),
            )
        if canvas is not None and canvas_strategy == "window":
            canvas.pid = game.pid
        started = time.time()
        tick = time.monotonic()
        beginning = tick
        record_session(
            Path(config["state"]), config["session_id"], config["project_id"], started, 0
        )
        state.update(state="running", pid=game.pid, started_at=started)
        write_state(record, state)
        if canvas is None:
            print(json.dumps({"pid": game.pid}), flush=True)
            announced = True
        while not exited(game):
            if canvas is not None:
                if canvas.poll():
                    state["readiness"] = (
                        "ready"
                        if canvas.status.get("ready", bool(canvas.status.get("render")))
                        else "starting"
                    )
                    write_state(record, state)
                if not announced and canvas.status["state"] != "pending":
                    print(json.dumps({"pid": game.pid}), flush=True)
                    announced = True
            if time.monotonic() - tick >= 5:
                record_session(
                    Path(config["state"]),
                    config["session_id"],
                    config["project_id"],
                    started,
                    time.monotonic() - beginning,
                )
                tick = time.monotonic()
            if presence is not None and presence.expired(time.monotonic()):
                reason = "player_disconnected"
            if reason is not None:
                break
            readable, _, _ = select.select([sys.stdin], [], [], 0.05)
            if readable:
                line = sys.stdin.readline()
                if not line:
                    reason = "service_disconnect"
                elif line.strip() == "stop":
                    reason = "stop_requested"
                elif presence is not None:
                    try:
                        message = json.loads(line)
                        if (
                            message.get("type") == "heartbeat"
                            and message.get("session_id") == config["session_id"]
                        ):
                            presence.heartbeat(
                                message.get("connection_id"), message.get("epoch"), time.monotonic()
                            )
                    except (ValueError, TypeError):
                        pass
    except OSError as error:
        print(json.dumps({"error": str(error), "errno": error.errno}), flush=True)
        reason = "launch_failed"
    finally:
        code = finish(game, terminate=True) if game is not None else None
        state.update(
            state="stopped"
            if reason
            in ("service_disconnect", "stop_requested", "supervisor_signal", "player_disconnected")
            or code == 0
            else "failed",
            exit_code=code,
            reason=reason or "game_exit",
        )
        if started is not None:
            record_session(
                Path(config["state"]),
                config["session_id"],
                config["project_id"],
                started,
                time.monotonic() - beginning,
                time.time(),
            )
        write_state(record, state)
        if game is not None and not announced:
            print(json.dumps({"pid": game.pid}), flush=True)
        if canvas is not None:
            canvas.close()
        os.close(lease_fd)


if __name__ == "__main__":
    main()
