"""Cross-project migration and undo of saves produced by real SDK fixtures."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.compatibility.test_runtime_smoke import (
    display,  # noqa: F401
    events,
    key,
    wait_for,
)
from vn_stream.projects.catalog import ProjectCatalog
from vn_stream.saves.operations import Saves
from vn_stream.saves.renpy import pickle_codec as save_codec
from vn_stream.sessions.actions import ActionQueue

pytestmark = pytest.mark.skipif(
    os.environ.get("VN_STREAM_RUNTIME_SMOKE") != "1", reason="opt-in real SDK/X11 matrix"
)
VERSIONS = ["7.5.3", "7.8.2", "8.0.3", "8.1.3", "8.2.3", "8.3.2", "8.3.7", "8.4.1", "8.5.3"]


class Idle:
    def status(self):
        return {"state": "stopped", "project_id": None}


@pytest.mark.parametrize(
    "source_version,target_version",
    [(v, v) for v in VERSIONS] + [("8.3.2", "8.3.7"), ("8.4.1", "8.5.3")],
)
def test_real_engine_migration_and_batch_undo(
    tmp_path,
    display,  # noqa: F811
    source_version,
    target_version,
):
    from vn_stream.saves.migrations import SaveMigrations

    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    source, target = tmp_path / "old", tmp_path / "new"
    shutil.copytree(Path(__file__).parents[1] / "fixtures/runtime-smoke", source)
    script = source / "game/script.rpy"
    script.write_text(
        script.read_text().replace(
            "$ counter = 42",
            '$ counter = int(open(os.path.join(config.basedir, "initial.txt")).read())',
        )
    )
    shutil.copytree(source, target)
    (source / "initial.txt").write_text("42")
    (target / "initial.txt").write_text("5")
    old = library.register(source, "Fixture old")
    new = library.register(target, "Fixture new")

    def play(path, version, load=False):
        (path / "events.jsonl").unlink(missing_ok=True)
        home = path / ".vn-stream/home"
        home.mkdir(parents=True, exist_ok=True)
        sdk = Path(os.environ.get("VN_STREAM_RUNTIME_ROOT", "var/runtimes")).resolve() / version
        with (path / "fixture.log").open("wb") as output:
            process = subprocess.Popen(
                [
                    "sh",
                    str(sdk / "renpy.sh"),
                    "--savedir",
                    str(path / "game/saves"),
                    str(path),
                    "run",
                ],
                env={
                    **display[1],
                    "HOME": str(home),
                    "SDL_AUDIODRIVER": "dummy",
                    "LIBGL_ALWAYS_SOFTWARE": "1",
                    **ActionQueue(
                        tmp_path / "data", "migration-" + version, tmp_path / "media"
                    ).environment(),
                },
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_for(
                    lambda: any(e["event"] == "main_menu" for e in events(path)),
                    detail=lambda: (path / "fixture.log").read_text()[-2500:],
                )
                key(display, "l" if load else "Return")
                result = wait_for(
                    lambda: next(
                        (
                            e
                            for e in reversed(events(path))
                            if e["event"] == ("loaded" if load else "ready")
                        ),
                        None,
                    ),
                    detail=lambda: (path / "fixture.log").read_text()[-2500:],
                )
                if not load:
                    key(display, "s")
                    wait_for(lambda: (path / "game/saves/smoke-1-LT1.save").exists())
                    wait_for(lambda: (path / "game/saves/persistent").exists())
                return result
            finally:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)

    assert play(source, source_version)["counter"] == 42
    shutil.copy2(source / "game/script.rpyc", target / "game/script.rpyc")
    assert play(target, target_version)["counter"] == 5
    old_files = {
        str(p.relative_to(source / "game/saves")): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in (source / "game/saves").rglob("*")
        if p.is_file()
    }
    original = {p.name: p.read_bytes() for p in (target / "game/saves").iterdir() if p.is_file()}
    name = "smoke-1-LT1.save"
    untouched = target / "game/saves/target-only.save"
    untouched.write_bytes(original[name])
    saves = Saves(library, Idle())
    service = SaveMigrations(saves)
    try:
        preview = service.preview(new.project_id, old.project_id)
        assert preview["counts"]["invalid"] == 0
        task = service.commit(new.project_id, preview["id"], "sdk-migration")
        service.process(task["id"])
        assert service.get(task["id"])["state"] == "completed", service.get(task["id"])
        assert untouched.read_bytes() == original[name]
        for filename, (digest, stamp) in old_files.items():
            p = source / "game/saves" / filename
            assert hashlib.sha256(p.read_bytes()).hexdigest() == digest
            assert p.stat().st_mtime_ns == stamp
        for filename in (name, "persistent"):
            a = saves.resolve_save(old.project_id, saves.identifier(filename))[1][0]
            b = target / "game/saves" / filename
            assert (
                save_codec.unpack(a.read_bytes(), filename == "persistent")[0]
                == save_codec.unpack(b.read_bytes(), filename == "persistent")[0]
            )
            assert b.stat().st_mtime_ns == a.stat().st_mtime_ns
            assert saves._trusted(new.project_id, b.read_bytes(), filename == "persistent")
        loaded = play(target, target_version, True)
        assert loaded["counter"] == 42 and loaded["marker"] == "persisted"
        undo_preview = service.undo_preview(task["id"])
        undo = service.undo(task["id"], undo_preview["id"], "sdk-undo")
        service.process(undo["id"])
        assert service.get(undo["id"])["state"] == "completed", service.get(undo["id"])
        loaded = play(target, target_version, True)
        assert loaded["counter"] == 5 and loaded["marker"] == "persisted"
        assert untouched.read_bytes() == original[name]
    finally:
        service.close()
