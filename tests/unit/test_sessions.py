import sys
import time
from pathlib import Path

import pytest

from vn_stream.engines.contracts import LaunchPlan
from vn_stream.sessions.supervisor import SessionManager


def test_start_is_idempotent_and_other_game_conflicts(tmp_path):
    manager = SessionManager(tmp_path)
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    try:
        first = manager.start("one", command, tmp_path, {})
        second = manager.start("one", command, tmp_path, {})
        assert first["pid"] == second["pid"]
        with pytest.raises(ValueError):
            manager.start("two", command, tmp_path, {})
        assert manager.status()["project_id"] == "one"
    finally:
        manager.stop()
    assert manager.status()["state"] == "stopped"


def test_process_exit_is_reported_as_failure(tmp_path):
    manager = SessionManager(tmp_path)
    manager.start("bad", [sys.executable, "-c", "raise SystemExit(3)"], tmp_path, {})
    deadline = time.monotonic() + 5
    while manager.status()["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.status()["state"] == "failed"
    assert manager.status()["exit_code"] == 3


def test_only_one_manager_can_own_the_session(tmp_path):
    first = SessionManager(tmp_path)
    other = SessionManager(tmp_path)
    try:
        first.start("one", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, {})
        with pytest.raises(ValueError):
            other.start("two", [sys.executable, "-c", "pass"], tmp_path, {})
    finally:
        first.stop()
        other.stop()


def test_launch_failure_does_not_keep_session_lock(tmp_path):
    manager = SessionManager(tmp_path)
    with pytest.raises(OSError):
        manager.start("bad", ["/does/not/exist"], tmp_path, {})
    manager.start("good", [sys.executable, "-c", "pass"], tmp_path, {})
    manager.stop()


def test_stop_kills_descendants_even_when_leader_exits_on_term(tmp_path):
    import os

    manager = SessionManager(tmp_path)
    marker = tmp_path / "child.pid"
    child = 'import signal,time,os; signal.signal(signal.SIGTERM, signal.SIG_IGN); open("child.pid","w").write(str(os.getpid())); time.sleep(30)'
    parent = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(30)'
    try:
        manager.start("one", [sys.executable, "-c", parent], tmp_path, {})
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        pid = int(marker.read_text())
        manager.stop()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            stat = Path(f"/proc/{pid}/stat")
            if not stat.exists() or stat.read_text().split(") ")[1].startswith("Z"):
                break
            time.sleep(0.01)
        else:
            pytest.fail("descendant survived session stop")
    finally:
        manager.stop()
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), 9)
            except ProcessLookupError:
                pass


def process_alive(pid):
    stat = Path(f"/proc/{pid}/stat")
    try:
        return not stat.read_text().split(") ")[1].startswith("Z")
    except (FileNotFoundError, ProcessLookupError):
        return False


def test_api_process_crash_cleans_game_group_and_releases_lease(tmp_path):
    import json
    import os
    import subprocess

    child = 'import signal,time,os; signal.signal(signal.SIGTERM, signal.SIG_IGN); open("descendant.pid","w").write(str(os.getpid())); time.sleep(60)'
    game = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(60)'
    api_script = """
import json,sys,time
from pathlib import Path
from vn_stream.engines.contracts import LaunchPlan
from vn_stream.sessions.supervisor import SessionManager
root=Path(sys.argv[1])
manager=SessionManager(root)
state=manager.start('crashed',[sys.executable,'-c',sys.argv[2]],root,{})
(root/'api-ready.json').write_text(json.dumps(state))
time.sleep(60)
"""
    environment = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    api = subprocess.Popen([sys.executable, "-c", api_script, str(tmp_path), game], env=environment)
    pids = []
    replacement = SessionManager(tmp_path)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (tmp_path / "api-ready.json").exists() and (tmp_path / "descendant.pid").exists():
                break
            time.sleep(0.02)
        assert (tmp_path / "api-ready.json").exists()
        assert (tmp_path / "descendant.pid").exists()
        pids = [
            json.loads((tmp_path / "api-ready.json").read_text())["pid"],
            int((tmp_path / "descendant.pid").read_text()),
        ]
        assert all(process_alive(pid) for pid in pids)
        api.kill()
        api.wait(timeout=5)
        deadline = time.monotonic() + 8
        while any(process_alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not any(process_alive(pid) for pid in pids), "API crash left a game process alive"
        deadline = time.monotonic() + 3
        while replacement.status()["state"] == "recovering" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert replacement.status()["state"] == "stopped"
        assert replacement.status()["reason"] == "service_disconnect"
        assert (
            replacement.start(
                "new", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, {}
            )["state"]
            == "running"
        )
    finally:
        api.kill() if api.poll() is None else None
        api.wait(timeout=5)
        replacement.stop()
        for pid in pids:
            if process_alive(pid):
                try:
                    os.killpg(pid, 9) if pid == pids[0] else os.kill(pid, 9)
                except ProcessLookupError:
                    pass


def test_failed_session_is_visible_after_manager_restart(tmp_path):
    manager = SessionManager(tmp_path)
    manager.start("bad", [sys.executable, "-c", "raise SystemExit(7)"], tmp_path, {})
    deadline = time.monotonic() + 5
    while manager.status()["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    recovered = SessionManager(tmp_path).status()
    assert recovered["state"] == "failed"
    assert recovered["project_id"] == "bad"
    assert recovered["exit_code"] == 7


def test_other_manager_reports_busy_instead_of_stopped(tmp_path):
    manager = SessionManager(tmp_path)
    other = SessionManager(tmp_path)
    try:
        manager.start("one", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, {})
        assert other.status()["state"] == "recovering"
        assert other.status()["project_id"] == "one"
    finally:
        manager.stop()


def test_lost_supervisor_requires_recovery_even_after_game_leader_exits(tmp_path):
    import os
    import signal

    manager = SessionManager(tmp_path)
    replacement = SessionManager(tmp_path)
    pid = None
    try:
        state = manager.start(
            "lost", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, {}
        )
        pid = state["pid"]
        manager.process.kill()
        manager.process.wait(timeout=5)
        os.killpg(pid, signal.SIGKILL)
        deadline = time.monotonic() + 3
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        manager.status()  # Reap the supervisor and close this API's copy of the lease.
        # A lost supervisor cannot attest that grandchildren have been cleaned.
        assert replacement.status()["state"] == "recovering"
        with pytest.raises(ValueError):
            replacement.start("new", [sys.executable, "-c", "pass"], tmp_path, {})
    finally:
        manager.stop()
        if pid is not None and process_alive(pid):
            os.killpg(pid, signal.SIGKILL)


def test_previous_container_scope_does_not_permanently_block_launch(tmp_path):
    import json

    (tmp_path / "session.json").write_text(
        json.dumps(
            {
                "state": "running",
                "project_id": "old",
                "scope": {"boot": "previous-boot", "namespace": 1, "init_start": "0"},
            }
        )
    )
    manager = SessionManager(tmp_path)
    assert manager.status()["state"] == "failed"
    try:
        assert (
            manager.start(
                "new", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, {}
            )["state"]
            == "running"
        )
    finally:
        manager.stop()


def test_start_rechecks_recovery_record_after_acquiring_lease(tmp_path, monkeypatch):
    from vn_stream.sessions.worker import process_scope, write_state

    manager = SessionManager(tmp_path)
    original_status = manager.status
    injected = False

    def concurrent_failure():
        nonlocal injected
        state = original_status()
        if not injected:
            injected = True
            write_state(
                tmp_path / "session.json",
                {"state": "running", "project_id": "other", "scope": process_scope()},
            )
        return state

    monkeypatch.setattr(manager, "status", concurrent_failure)
    try:
        with pytest.raises(ValueError):
            manager.start(
                "new", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, {}
            )
    finally:
        manager.stop()


def test_supervisor_records_intent_before_creating_game(tmp_path):
    import fcntl
    import json
    import os
    import signal
    import subprocess

    from vn_stream.sessions import worker

    # Pause exactly after the OS created a real game, before Popen returns to
    # supervisor code. Killing here must still leave a recovery record.
    wrapper = tmp_path / "paused-supervisor.py"
    wrapper.write_text(f"""
import importlib.util,json,os,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('session_worker', {worker.__file__!r})
worker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
actual_popen=worker.subprocess.Popen
def paused_popen(*args,**kwargs):
    child=actual_popen(*args,**kwargs)
    Path({str(tmp_path / "launched.pid")!r}).write_text(str(child.pid))
    time.sleep(30)
    return child
worker.subprocess.Popen=paused_popen
worker.main()
""")
    child_code = 'import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open("survivor.pid","w").write(str(os.getpid())); time.sleep(30)'
    game_code = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child_code!r}]); time.sleep(30)'
    lease = (tmp_path / "session.lock").open("a+")
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    supervisor = subprocess.Popen(
        [sys.executable, str(wrapper), str(lease.fileno())],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        pass_fds=(lease.fileno(),),
    )
    lease.close()
    replacement = SessionManager(tmp_path)
    game_pid = child_pid = None
    try:
        supervisor.stdin.write(
            json.dumps(
                {
                    "state": str(tmp_path),
                    "project_id": "interrupted",
                    "command": [sys.executable, "-c", game_code],
                    "cwd": str(tmp_path),
                    "env": {},
                }
            )
            + "\n"
        )
        supervisor.stdin.flush()
        deadline = time.monotonic() + 5
        markers = [tmp_path / "launched.pid", tmp_path / "survivor.pid"]
        while (
            not all(p.exists() and p.stat().st_size for p in markers)
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert all(p.exists() and p.stat().st_size for p in markers)
        game_pid, child_pid = [int(p.read_text()) for p in markers]
        supervisor.kill()
        supervisor.wait(timeout=5)
        os.kill(game_pid, signal.SIGKILL)
        deadline = time.monotonic() + 3
        while process_alive(game_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert process_alive(child_pid)
        with pytest.raises(ValueError):
            replacement.start(
                "new", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, {}
            )
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
        supervisor.wait(timeout=5)
        supervisor.stdin.close()
        supervisor.stdout.close()
        replacement.stop()
        if game_pid is not None:
            try:
                os.killpg(game_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_stop_budget_includes_two_bounded_display_applies_and_game_cleanup(tmp_path):
    import io
    import json

    manager = SessionManager(tmp_path)

    class BoundedSupervisor:
        stdin = io.StringIO()
        stdout = io.StringIO()

        def poll(self):
            return None

        def wait(self, timeout):
            # Native apply + fallback each <=2.5s, TERM grace <=5s, then reap.
            # A supervisor in this valid worst-case must not be abandoned at 8s.
            assert timeout > 2.5 + 2.5 + 5
            (tmp_path / "session.json").write_text(json.dumps({"state": "stopped"}))
            return 0

    manager.process = BoundedSupervisor()
    assert manager.stop()["state"] == "stopped"
    assert manager.process is None


def test_launch_preparation_can_supply_engine_working_directory(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    working = root / "engine"
    working.mkdir()
    manager = SessionManager(tmp_path / "state")
    try:
        manager.start(
            "prepared",
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import time; Path('ready').write_text(str(Path.cwd())); time.sleep(30)",
            ],
            root,
            {},
            prepare=lambda: LaunchPlan(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import time; Path('ready').write_text(str(Path.cwd())); time.sleep(30)",
                ],
                {},
                working,
                "test",
            ),
        )
        deadline = time.monotonic() + 3
        while not (working / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (working / "ready").read_text() == str(working)
        assert not (root / "ready").exists()
    finally:
        manager.stop()


def test_stop_releases_streams_and_lease_after_peer_closes_input(tmp_path):
    import io
    from types import SimpleNamespace

    from vn_stream.sessions.supervisor import SessionManager

    class DisconnectedInput:
        def close(self):
            raise BrokenPipeError("worker already exited")

    manager = SessionManager(tmp_path)
    output = io.StringIO()
    lease = io.StringIO()
    manager.process = SimpleNamespace(stdin=DisconnectedInput(), stdout=output, poll=lambda: 0)
    manager.lease = lease
    manager.game_pid = 123
    assert manager.stop()["state"] == "stopped"
    assert output.closed and lease.closed
    assert manager.process is None and manager.game_pid is None
