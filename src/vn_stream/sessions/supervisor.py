"""One supervised game group per service, guarded across threads and processes."""

import fcntl
import json
import select
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from uuid import uuid4

from vn_stream.engines.contracts import LaunchPlan
from vn_stream.i18n import text
from vn_stream.sessions.worker import process_scope


class SessionManager:
    def __init__(self, state: Path):
        self.state = state.resolve()
        self.state.mkdir(parents=True, exist_ok=True)
        self.guard = RLock()
        self.process: subprocess.Popen | None = None
        self.lease = None
        self.project_id: str | None = None
        self.game_pid: int | None = None

    def _release(self):
        if self.process is not None:
            try:
                self.process.stdin.close()
            except BrokenPipeError:
                # Buffered control input may flush after the worker has exited.
                pass
            self.process.stdout.close()
            self.process = None
        if self.lease is not None:
            self.lease.close()
            self.lease = None
        self.game_pid = None

    def _record(self) -> dict:
        try:
            with (self.state / "session.json").open(encoding="utf-8") as file:
                data = json.loads(file.read(64 * 1024))
            if not isinstance(data, dict) or data.get("state") not in (
                "running",
                "failed",
                "stopped",
            ):
                raise ValueError("invalid state")
            return data
        except FileNotFoundError:
            return {"state": "stopped", "project_id": None, "exit_code": None}
        except (ValueError, OSError):
            return {
                "state": "failed",
                "project_id": None,
                "exit_code": None,
                "reason": "invalid_record",
            }

    def status(self) -> dict:
        with self.guard:
            if self.process is not None:
                if self.process.poll() is None:
                    return {
                        **self._record(),
                        "state": "running",
                        "project_id": self.project_id,
                        "pid": self.game_pid,
                    }
                self._release()
            # Probe the actual lease. Disk PIDs are diagnostic information only;
            # never send signals to them after a restart because they may be reused.
            with (self.state / "session.lock").open("a+") as lease:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return {**self._record(), "state": "recovering"}
                return self._unowned_status()

    def _unowned_status(self) -> dict:
        """Read only while the caller holds the actual exclusive session lease."""
        record = self._record()
        if record["state"] == "running":
            scope = process_scope()
            if scope is not None and record.get("scope") is not None and record["scope"] != scope:
                return {**record, "state": "failed", "reason": "previous_scope"}
            # A missing lease does not prove that an orphaned grandchild exited
            # after its supervisor was killed. Require recovery in this scope.
            return {**record, "state": "recovering", "reason": "supervisor_lost"}
        return record

    @staticmethod
    def _check_recovery(current: dict):
        if current["state"] == "recovering":
            if current.get("reason") == "supervisor_lost":
                raise ValueError(
                    text("errors.thePreviousSessionSupervisorFailedRestartTheContainerWithout")
                )
            raise ValueError(text("errors.anotherServiceOrPreviousSessionIsUsingTheGame"))

    def start(
        self,
        project_id: str,
        command: list[str],
        cwd: Path,
        env: dict,
        *,
        prepare: Callable[[], LaunchPlan] | None = None,
    ) -> dict:
        with self.guard:
            current = self.status()
            if current["state"] == "running":
                if current["project_id"] == project_id:
                    return current
                raise ValueError(text("errors.aGameIsRunningSaveAndStopItFirst"))
            self._check_recovery(current)
            lease = (self.state / "session.lock").open("a+")
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                lease.close()
                raise ValueError(
                    text("errors.anotherServiceOrLeftoverGameProcessIsUsingThe")
                ) from error
            self.lease = lease
            self.project_id = project_id
            try:
                # Another API may have run and failed after status() released its
                # probe lease. Recheck under the lease inherited by our supervisor.
                self._check_recovery(self._unowned_status())
                if prepare is not None:
                    launch = prepare()
                    command, cwd, env = launch.command, launch.cwd, dict(launch.env)
                    env["VN_STREAM_CANVAS_STRATEGY"] = launch.display_policy
                    env["VN_STREAM_ACTION_STRATEGY"] = launch.action_policy
                    if launch.canvas:
                        env["VN_STREAM_STATIC_CANVAS"] = ",".join(map(str, launch.canvas))
                with (cwd / ".vn-stream-session.log").open("ab") as log:
                    # A private pipe represents API ownership. Browser connections
                    # never own this pipe, so a browser disconnect leaves the game up.
                    self.process = subprocess.Popen(
                        [
                            sys.executable,
                            str(Path(__file__).with_name("worker.py")),
                            str(lease.fileno()),
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=log,
                        text=True,
                        start_new_session=True,
                        pass_fds=(lease.fileno(),),
                    )
                self.process.stdin.write(
                    json.dumps(
                        {
                            "project_id": project_id,
                            "session_id": uuid4().hex,
                            "command": command,
                            "cwd": str(cwd.resolve()),
                            "env": env,
                            "state": str(self.state),
                        }
                    )
                    + "\n"
                )
                self.process.stdin.flush()
                ready, _, _ = select.select([self.process.stdout], [], [], 12)
                if not ready:
                    raise OSError(text("errors.theGameSupervisorTimedOutWhileStarting"))
                reply = json.loads(self.process.stdout.readline())
                if "error" in reply:
                    raise OSError(reply.get("errno"), reply["error"])
                self.game_pid = reply["pid"]
            except Exception:
                if self.process is not None:
                    self.process.stdin.close()
                    # EOF makes the supervisor clean up any game already launched.
                    self.process.wait(timeout=12)
                self._release()
                raise
            return self.status()

    def heartbeat(self, session_id, connection_id, epoch):
        with self.guard:
            current = self.status()
            if (
                self.process is None
                or current.get("session_id") != session_id
                or current["state"] != "running"
            ):
                return False
            try:
                self.process.stdin.write(
                    json.dumps(
                        dict(
                            type="heartbeat",
                            session_id=session_id,
                            connection_id=connection_id,
                            epoch=epoch,
                        )
                    )
                    + "\n"
                )
                self.process.stdin.flush()
                return True
            except (BrokenPipeError, OSError):
                return False

    def stop(self) -> dict:
        with self.guard:
            if self.process is not None:
                if self.process.poll() is None:
                    try:
                        self.process.stdin.write("stop\n")
                        self.process.stdin.flush()
                    except BrokenPipeError:
                        pass
                    self.process.wait(timeout=12)
                self._release()
            return self.status()
