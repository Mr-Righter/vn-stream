"""Only self-authored saves exercise signatures, independent backups and recovery."""

import io
import json
import os
import pickle
import zipfile
import zlib

import pytest

from tests.auth_helpers import api_data
from vn_stream.projects.catalog import ProjectCatalog
from vn_stream.saves.operations import Saves
from vn_stream.saves.renpy import pickle_codec as save_codec


class Idle:
    def status(self):
        return {"state": "stopped", "project_id": None}


def sample(money=5):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(zipfile.ZipInfo("log"), pickle.dumps(({"store.money": money}, {}), 2))
        z.writestr(zipfile.ZipInfo("json"), '{"_save_name":"Fixture"}')
        z.writestr(zipfile.ZipInfo("screenshot.png"), b"screenshot fixture")
    return out.getvalue()


@pytest.fixture
def setup(tmp_path):
    p = tmp_path / "nas/project"
    (p / "game/saves").mkdir(parents=True)
    (p / "game/script.rpy").write_text("label start: pass")
    library = ProjectCatalog(tmp_path / "data", [tmp_path / "nas"])
    game = library.register(p, "Fixture")
    return Saves(library, Idle()), game, p


def test_manual_backup_is_repeatable_idempotent_and_preserves_current(setup):
    saves, game, p = setup
    path = p / "game/saves/1-1.save"
    path.write_bytes(sample())
    uid = saves.identifier(path.name)
    revision = saves.variables(game.project_id, uid)["revision"]
    first = saves.backup(game.project_id, uid, revision, "first")
    assert saves.backup(game.project_id, uid, revision, "first")["id"] == first["id"]
    second = saves.backup(game.project_id, uid, revision, "second")
    assert second["id"] != first["id"]
    assert saves.variables(game.project_id, uid)["revision"] == revision
    assert [b["id"] for b in saves.backups(game.project_id, uid)] == [second["id"], first["id"]]
    assert saves.backup_file(game.project_id, uid, first["id"]).read_bytes() == sample()
    assert first["kind"] == "manual"
    saves.sessions.status = lambda: {"state": "running", "project_id": game.project_id}
    with pytest.raises(ValueError):
        saves.backup(game.project_id, uid, revision, "busy")


def test_manual_backup_uses_latest_copy_and_restore_signs_it(setup):
    saves, game, p = setup
    first = p / "game/saves/1-1.save"
    second = p / ".vn-stream/home/.renpy/fixture/1-1.save"
    second.parent.mkdir(parents=True)
    (p / ".vn-stream/save-locations.json").write_text(
        json.dumps({"locations": ["game/saves", ".vn-stream/home/.renpy/fixture"]})
    )
    first.write_bytes(sample(1))
    second.write_bytes(sample(2))
    os.utime(first, ns=(1, 1))
    os.utime(second, ns=(2, 2))
    uid = saves.identifier(first.name)
    revision = saves.variables(game.project_id, uid)["revision"]
    backup = saves.backup(game.project_id, uid, revision, "latest")
    assert saves.backup_file(game.project_id, uid, backup["id"]).read_bytes() == sample(2)
    saves.restore(game.project_id, uid, backup["id"], revision)
    for path in (first, second):
        assert save_codec.unpack(path.read_bytes())[0] == save_codec.unpack(sample(2))[0]
        assert saves._trusted(game.project_id, path.read_bytes(), False)


def test_external_upload_is_locally_signed_without_changing_payload(setup):
    saves, game, p = setup
    original = sample()
    result = saves.upload(game.project_id, "external.save", original)
    signed = saves.read(game.project_id, result["id"])
    assert saves._trusted(game.project_id, signed, False)
    with zipfile.ZipFile(io.BytesIO(original)) as old, zipfile.ZipFile(io.BytesIO(signed)) as new:
        for name in old.namelist():
            assert old.read(name) == new.read(name)
    key = p / ".vn-stream/home/.renpy/tokens/security_keys.txt"
    assert key.stat().st_mode & 0o777 == 0o600
    revision = saves.variables(game.project_id, result["id"])["revision"]
    saves.upload(game.project_id, "external.save", sample(8), revision)
    assert saves.backups(game.project_id, result["id"])


def test_damaged_existing_key_is_never_replaced(setup):
    saves, game, p = setup
    key = p / ".vn-stream/home/.renpy/tokens/security_keys.txt"
    key.parent.mkdir(parents=True)
    key.write_bytes(b"signing-key invalid invalid\n")
    with pytest.raises(ValueError):
        saves.upload(game.project_id, "external.save", sample())
    assert key.read_bytes() == b"signing-key invalid invalid\n"
    assert not (p / "game/saves/external.save").exists()


def test_external_change_during_signing_is_not_overwritten(setup, monkeypatch):
    from vn_stream.saves.renpy.signatures import Signatures

    saves, game, p = setup
    path = p / "game/saves/slot.save"
    path.write_bytes(sample())
    uid = saves.identifier(path.name)
    revision = saves.variables(game.project_id, uid)["revision"]
    original = Signatures.resign

    def replace(self, data, persistent=False):
        result = original(self, data, persistent)
        path.write_bytes(sample(99))
        return result

    monkeypatch.setattr(Signatures, "resign", replace)
    with pytest.raises(ValueError):
        saves.upload(game.project_id, path.name, sample(8), revision)
    assert path.read_bytes() == sample(99)


def test_resigning_persistent_keeps_compressed_payload_and_never_executes(setup):
    from vn_stream.saves.renpy.signatures import Signatures

    saves, game, p = setup
    marker = p / "never"

    class Executable:
        def __reduce__(self):
            return (eval, (f"open({str(marker)!r},'w').write('bad')",))

    payload = zlib.compress(pickle.dumps({"custom": Executable()}, 2))
    signed = Signatures(saves.library, game.project_id).resign(payload, True)
    assert signed.startswith(payload)
    assert saves._trusted(game.project_id, signed, True)
    assert not marker.exists()


def test_signing_queue_preserves_distinct_copies_mtime_and_only_runs_idle(setup):
    from vn_stream.saves.signing import SigningJobs

    saves, game, p = setup
    first = p / "game/saves/1-1.save"
    second = p / ".vn-stream/home/.renpy/fixture/1-1.save"
    second.parent.mkdir(parents=True)
    (p / ".vn-stream/save-locations.json").write_text(
        json.dumps({"locations": ["game/saves", ".vn-stream/home/.renpy/fixture"]})
    )
    first.write_bytes(sample(1))
    second.write_bytes(sample(2))
    os.utime(first, ns=(1234, 1234))
    os.utime(second, ns=(5678, 5678))
    jobs = SigningJobs(saves)
    jobs.ensure(game.project_id)
    saves.sessions.status = lambda: {"state": "running", "project_id": game.project_id}
    assert jobs.process(game.project_id)["state"] == "waiting"
    assert first.read_bytes() == sample(1)
    saves.sessions.status = lambda: {"state": "stopped", "project_id": None}
    result = jobs.process(game.project_id)
    assert result["state"] == "completed" and result["signed"] == 2
    for path, value, stamp in ((first, 1, 1234), (second, 2, 5678)):
        assert path.stat().st_mtime_ns == stamp
        assert save_codec.unpack(path.read_bytes())[0] == save_codec.unpack(sample(value))[0]
        assert saves._trusted(game.project_id, path.read_bytes(), False)
    assert len(saves.backups(game.project_id, saves.identifier(first.name))) == 2
    jobs.retry(game.project_id)
    assert jobs.process(game.project_id)["skipped"] == 2
    assert len(saves.backups(game.project_id, saves.identifier(first.name))) == 2


def test_signing_restart_recovers_queue_and_keeps_bad_file(setup):
    from vn_stream.saves.signing import SigningJobs

    saves, game, p = setup
    (p / "game/saves/good.save").write_bytes(sample())
    bad = p / "game/saves/bad.save"
    bad.write_bytes(b"broken")
    jobs = SigningJobs(saves)
    jobs.ensure(game.project_id)
    restarted = SigningJobs(saves)
    result = restarted.process(game.project_id)
    assert result["signed"] == 1 and result["failed"] == 1
    assert result["errors"][0]["code"] == "saves.unsupported"
    assert bad.read_bytes() == b"broken"


def test_signing_shutdown_resumes_without_duplicate_backups(setup, monkeypatch):
    from vn_stream.saves.signing import SigningJobs

    saves, game, p = setup
    for name in ("first.save", "second.save"):
        (p / "game/saves" / name).write_bytes(sample())
    jobs = SigningJobs(saves)
    jobs.ensure(game.project_id)
    original = saves.commit_files

    def interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        jobs.stop.set()
        return result

    monkeypatch.setattr(saves, "commit_files", interrupt)
    assert jobs.process(game.project_id)["state"] == "waiting"
    monkeypatch.setattr(saves, "commit_files", original)
    resumed = SigningJobs(saves).process(game.project_id)
    assert resumed["signed"] == 1 and resumed["skipped"] == 1
    assert (
        sum(
            len(saves.backups(game.project_id, saves.identifier(name)))
            for name in ("first.save", "second.save")
        )
        == 2
    )


def test_signing_backup_failure_never_changes_original_and_retry_works(setup, monkeypatch):
    from vn_stream.saves import operations as module
    from vn_stream.saves.signing import SigningJobs

    saves, game, p = setup
    path = p / "game/saves/slot.save"
    path.write_bytes(sample())
    jobs = SigningJobs(saves)
    jobs.ensure(game.project_id)
    original = module.shutil.copy2

    def full(*args, **kwargs):
        raise OSError("fixture disk full")

    monkeypatch.setattr(module.shutil, "copy2", full)
    assert jobs.process(game.project_id)["failed"] == 1
    assert path.read_bytes() == sample()
    monkeypatch.setattr(module.shutil, "copy2", original)
    jobs.retry(game.project_id)
    assert jobs.process(game.project_id)["signed"] == 1


def test_backup_and_signing_api_and_import_phase(tmp_path):
    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    p = tmp_path / "nas/project"
    (p / "game/saves").mkdir(parents=True)
    (p / "game/script.rpy").write_text("label start: pass")
    (p / "game/saves/slot.save").write_bytes(sample())
    app = create_app(tmp_path / "data", [tmp_path / "nas"], "secret")
    with TestClient(app) as c:
        sign_in(c, "secret")
        game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
        prefix = "/api/v2/projects/" + game["project_id"] + "/saves"
        status = api_data(c.get(prefix + "/signing"))
        assert status["state"] == "completed" and status["signed"] == 1
        item = api_data(c.get(prefix))["saves"][0]
        revision = api_data(c.get(prefix + "/" + item["id"] + "/variables"))["revision"]
        result = c.post(
            prefix + "/" + item["id"] + "/backups",
            json={"revision": revision, "request_id": "manual"},
        )
        assert result.status_code == 201, result.text
        assert api_data(result)["kind"] == "manual"
        (p / "game/saves/new.save").write_bytes(sample(8))
        again = c.post("/api/v2/projects", json={"path": str(p)})
        assert again.status_code == 201
        assert api_data(c.get(prefix + "/signing"))["total"] == 2
        assert app.state.saves._trusted(
            game["project_id"], (p / "game/saves/new.save").read_bytes(), False
        )
        c.cookies.clear()
        assert c.get(prefix + "/signing").status_code == 401


def test_running_signing_lease_excludes_mutations_except_owner(setup):
    from vn_stream.projects.locations import check_busy
    from vn_stream.saves.signing import SigningJobs

    saves, game, _ = setup
    jobs = SigningJobs(saves)
    value = jobs.ensure(game.project_id)
    value["state"] = "running"
    jobs._save(value)
    with pytest.raises(ValueError):
        check_busy(saves.library, game.project_id)
    assert not saves.writable(game.project_id)
    check_busy(saves.library, game.project_id, signing_owner=value["id"])


@pytest.mark.parametrize("public", [False, True])
def test_invalid_curve_key_is_recorded_without_killing_queue(setup, public):
    import base64

    import ecdsa

    from vn_stream.saves.signing import SigningJobs

    saves, game, p = setup
    path = p / "game/saves/slot.save"
    path.write_bytes(sample())
    key = ecdsa.SigningKey.generate(curve=ecdsa.NIST256p)
    private = key.to_der().replace(key.to_string(), bytes(32), 1)
    good_public = key.verifying_key.to_der()
    bad_public = good_public[:-64] + bytes(64)
    material = b"signing-key " + base64.b64encode(key.to_der() if public else private)
    material += b" " + base64.b64encode(good_public) + b"\n"
    if public:
        material += b"verifying-key " + base64.b64encode(bad_public) + b"\n"
    keyfile = p / ".vn-stream/home/.renpy/tokens/security_keys.txt"
    keyfile.parent.mkdir(parents=True)
    keyfile.write_bytes(material)
    jobs = SigningJobs(saves)
    result = jobs.process(game.project_id)
    assert result["state"] == "completed" and result["failed"] == 1
    assert result["errors"][0]["code"] == "saves.keyDamaged"
    assert keyfile.read_bytes() == material and path.read_bytes() == sample()
    keyfile.unlink()
    jobs.retry(game.project_id)
    assert jobs.process(game.project_id)["signed"] == 1


def test_automatic_backup_primary_is_latest_regardless_of_discovery_order(setup):
    saves, game, p = setup
    first = p / "game/saves/slot.save"
    second = p / ".vn-stream/home/.renpy/fixture/slot.save"
    second.parent.mkdir(parents=True)
    (p / ".vn-stream/save-locations.json").write_text(
        json.dumps({"locations": ["game/saves", ".vn-stream/home/.renpy/fixture"]})
    )
    first.write_bytes(sample(1))
    second.write_bytes(sample(2))
    os.utime(first, ns=(1, 1))
    os.utime(second, ns=(2, 2))
    uid = saves.identifier(first.name)
    revision = saves.variables(game.project_id, uid)["revision"]
    saves.upload(game.project_id, first.name, sample(3), revision)
    backup = saves.backups(game.project_id, uid)[0]
    assert saves.backup_file(game.project_id, uid, backup["id"]).read_bytes() == sample(2)


def test_import_recovery_waits_for_signing_before_source_cleanup(setup):
    from dataclasses import asdict

    from vn_stream.imports.jobs import ImportJobs

    saves, game, p = setup
    jobs = ImportJobs(saves.library)
    source = p.parent / "original.zip"
    source.write_bytes(b"fixture source")
    record = dict(
        id="recovery",
        request_id="recovery",
        upload_id=None,
        state="running",
        phase="signing",
        registered=True,
        reserved_project=asdict(game),
        result=asdict(game),
        source=str(source),
        identity=jobs._identity(source),
        delete_source=True,
        warning=None,
        abandoned=False,
        cleanup_done=False,
    )
    with saves.library.db.transaction() as db:
        db.execute(
            "INSERT INTO import_jobs (id,request_id,upload_id,state,record) VALUES(?,?,?,?,?)",
            ("recovery", "recovery", None, "running", json.dumps(record)),
        )
    resumed = ImportJobs(saves.library, recover=False)
    resumed.after_register = lambda *_: {"state": "waiting"}
    resumed.recover()
    assert source.exists() and resumed.get("recovery")["state"] == "running"
    resumed.after_register = lambda *_: {"state": "completed", "failed": 0}
    resumed.resume_signing(game.project_id)
    assert not source.exists() and resumed.get("recovery")["state"] == "completed"


def test_waiting_import_signing_resumes_cleanup_when_game_becomes_idle(setup):
    import time
    from dataclasses import asdict

    from vn_stream.imports.jobs import ImportJobs
    from vn_stream.saves.signing import SigningJobs

    saves, game, p = setup
    (p / "game/saves/slot.save").write_bytes(sample())
    imports = ImportJobs(saves.library, recover=False)
    saves.imports = imports
    queue = SigningJobs(saves)
    imports.after_register = queue.imported
    queue.on_completed = imports.resume_signing
    source = p.parent / "accepted.zip"
    source.write_bytes(b"fixture")
    record = dict(
        id="waiting-import",
        request_id="waiting-import",
        upload_id=None,
        state="running",
        phase="registering",
        registered=True,
        reserved_project=asdict(game),
        result=asdict(game),
        source=str(source),
        identity=imports._identity(source),
        delete_source=True,
        warning=None,
        abandoned=False,
        cleanup_done=False,
    )
    with saves.library.db.transaction() as db:
        db.execute(
            "INSERT INTO import_jobs (id,request_id,upload_id,state,record) VALUES(?,?,?,?,?)",
            (record["id"], record["request_id"], None, "running", json.dumps(record)),
        )
    saves.sessions.status = lambda: {"state": "running", "project_id": game.project_id}
    imports.recover()
    assert source.exists() and queue.status(game.project_id)["state"] == "waiting"
    saves.sessions.status = Idle().status
    queue.start()
    try:
        for _ in range(100):
            if imports.get(record["id"])["state"] == "completed":
                break
            time.sleep(0.02)
        assert imports.get(record["id"])["state"] == "completed"
        assert not source.exists()
        assert saves._trusted(game.project_id, (p / "game/saves/slot.save").read_bytes(), False)
    finally:
        queue.close()


def test_valid_local_signature_skips_static_editor_limits_without_backup(setup, monkeypatch):
    from vn_stream.saves.renpy.signatures import Signatures
    from vn_stream.saves.signing import SigningJobs

    saves, game, project = setup
    path = project / "game/saves/large-but-trusted.save"
    signed = Signatures(saves.library, game.project_id).resign(sample())
    path.write_bytes(signed)

    def editor_limit(_):
        raise save_codec.Invalid("Fixture exceeds editor instruction budget")

    monkeypatch.setattr(save_codec, "parse", editor_limit)
    jobs = SigningJobs(saves)
    result = jobs.process(game.project_id)
    assert result["skipped"] == 1 and result["failed"] == 0
    assert path.read_bytes() == signed
    assert not saves.backups(game.project_id, saves.identifier(path.name))


def test_background_signing_retry_retains_import_owner(setup, monkeypatch):
    from vn_stream.saves.signing import SigningJobs

    saves, game, _ = setup
    jobs = SigningJobs(saves)
    observed = []

    def busy(project_id, **owners):
        observed.append(owners.get("import_owner"))
        raise ValueError("busy")

    monkeypatch.setattr(saves, "require_writable", busy)
    jobs.process(game.project_id, import_owner="active-import")
    # A background iteration may have read no owner before the import acquired it.
    jobs.process(game.project_id, import_owner=None)
    assert observed == ["active-import", "active-import"]
    assert jobs.status(game.project_id)["import_owner"] == "active-import"


def test_signing_worker_retries_transient_database_read_lock(setup, monkeypatch):
    import sqlite3
    import threading
    import time
    from contextlib import contextmanager

    from vn_stream.saves.signing import SigningJobs

    saves, game, _ = setup
    jobs = SigningJobs(saves)
    jobs.ensure(game.project_id)
    original = saves.library.db.transaction
    attempts = []

    @contextmanager
    def database():
        if threading.current_thread() is thread and not attempts:
            attempts.append(1)
            raise sqlite3.OperationalError("database is locked")
        with original() as db:
            yield db

    monkeypatch.setattr(saves.library.db, "transaction", database)
    thread = threading.Thread(target=jobs._run)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if jobs.status(game.project_id)["state"] == "completed":
                break
            time.sleep(0.02)
        assert jobs.status(game.project_id)["state"] == "completed"
    finally:
        jobs.stop.set()
        jobs.wake.set()
        thread.join(3)
