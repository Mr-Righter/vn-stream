"""Migration tests use only small, self-authored save containers."""

import base64
import io
import json
import os
import pickle
import shutil
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from tests.auth_helpers import api_data
from tests.unit.test_save_polish import Idle, sample
from vn_stream.projects.catalog import ProjectCatalog
from vn_stream.projects.locations import check_busy
from vn_stream.saves.operations import Saves
from vn_stream.saves.renpy import pickle_codec as save_codec
from vn_stream.saves.renpy.signatures import Signatures


@pytest.fixture
def setup(tmp_path):
    library = ProjectCatalog(tmp_path / "data", [tmp_path / "nas"])
    games = []
    for name in ("source", "target"):
        path = tmp_path / "nas" / name
        (path / "game/saves").mkdir(parents=True)
        (path / "game/script.rpy").write_text("label start: pass")
        games.append(library.register(path, name))
    saves = Saves(library, Idle())
    from importlib.util import find_spec

    assert find_spec("vn_stream.saves.migrations"), "Migration service is missing"
    from vn_stream.saves.migrations import SaveMigrations

    migrations = SaveMigrations(saves)
    return migrations, saves, games[0], games[1]


def put(game, name, data, stamp=123456789):
    path = Path(game.path) / "game/saves" / name
    path.write_bytes(data)
    os.utime(path, ns=(stamp, stamp))
    return path


def run(migrations, target, preview, request="copy", **kwargs):
    task = migrations.commit(target.project_id, preview["id"], request, **kwargs)
    return migrations.process(task["id"])


def test_preview_is_readonly_and_copy_merges_with_target_signature(setup):
    migrations, saves, source, target = setup
    data = Signatures(saves.library, source.project_id).resign(sample(9))
    key = Path(source.path) / ".vn-stream/home/.renpy/tokens/security_keys.txt"
    key.chmod(0o644)
    original = put(source, "1-1.save", data)
    put(source, "auto-1.save", sample(4))
    put(source, "quick-1.save", sample(3))
    persistent = zlib.compress(pickle.dumps({"_changed": {"money": 5}, "money": 8}, 2))
    put(source, "persistent", persistent)
    old = put(target, "1-1.save", sample(1))
    unrelated = put(target, "2-1.save", sample(2))
    preview = migrations.preview(target.project_id, source.project_id)
    assert preview["counts"] == {"total": 4, "valid": 4, "invalid": 0, "add": 3, "overwrite": 1}
    assert key.stat().st_mode & 0o777 == 0o644
    assert not (Path(target.path) / ".vn-stream/home").exists()
    result = run(migrations, target, preview)
    assert result["state"] == "completed"
    assert result["completed"] == 4
    assert original.read_bytes() == data and key.stat().st_mode & 0o777 == 0o644
    assert unrelated.read_bytes() == sample(2)
    assert save_codec.unpack(old.read_bytes())[0] == save_codec.unpack(data)[0]
    assert old.stat().st_mtime_ns == 123456789
    assert saves._trusted(target.project_id, old.read_bytes(), False)
    assert save_codec.unpack((Path(target.path) / "game/saves/persistent").read_bytes(), True)[
        0
    ] == zlib.decompress(persistent)
    backups = saves.backups(target.project_id, saves.identifier(old.name))
    assert len(backups) == 1 and saves.backup_file(
        target.project_id, saves.identifier(old.name), backups[0]["id"]
    ).read_bytes() == sample(1)


def test_reservation_idempotency_revision_and_location_conflicts(setup):
    migrations, saves, source, target = setup
    put(source, "one.save", sample())
    preview = migrations.preview(target.project_id, source.project_id)
    task = migrations.commit(target.project_id, preview["id"], "once")
    assert migrations.commit(target.project_id, preview["id"], "once")["id"] == task["id"]
    for game in (source, target):
        with pytest.raises(ValueError):
            check_busy(saves.library, game.project_id)
    assert migrations.process(task["id"])["state"] == "completed"
    preview = migrations.preview(target.project_id, source.project_id)
    put(source, "one.save", sample(20))
    with pytest.raises(ValueError):
        migrations.commit(target.project_id, preview["id"], "changed")
    preview = migrations.preview(target.project_id, source.project_id)
    location = Path(target.path) / ".vn-stream/home/new"
    location.mkdir(parents=True)
    (Path(target.path) / ".vn-stream/save-locations.json").write_text(
        json.dumps({"locations": [".vn-stream/home/new"]})
    )
    with pytest.raises(ValueError):
        migrations.commit(target.project_id, preview["id"], "locations")


def test_invalid_requires_explicit_skip_and_allinvalid_is_rejected(setup):
    migrations, _, source, target = setup
    put(source, "bad.save", b"bad")
    preview = migrations.preview(target.project_id, source.project_id)
    assert preview["entries"][0]["reason"]["code"] == "saves.unsupported"
    with pytest.raises(ValueError):
        migrations.commit(target.project_id, preview["id"], "none", True)
    put(source, "good.save", sample())
    preview = migrations.preview(target.project_id, source.project_id)
    with pytest.raises(ValueError):
        migrations.commit(target.project_id, preview["id"], "skip")
    result = run(migrations, target, preview, "skip", skip_invalid=True)
    assert result["state"] == "completed" and result["skipped"] == 1
    assert not (Path(target.path) / "game/saves/bad.save").exists()


def test_undo_uses_independent_primary_backup_and_preserves_new_names(setup):
    migrations, saves, source, target = setup
    put(source, "old.save", sample(10))
    put(source, "added.save", sample(20))
    put(target, "old.save", sample(1), 10)
    other = Path(target.path) / ".vn-stream/home/second"
    other.mkdir(parents=True)
    (other / "old.save").write_bytes(sample(2))
    os.utime(other / "old.save", ns=(20, 20))
    (Path(target.path) / ".vn-stream/save-locations.json").write_text(
        json.dumps({"locations": [".vn-stream/home/second"]})
    )
    task = run(migrations, target, migrations.preview(target.project_id, source.project_id))
    put(target, "old.save", sample(30))
    put(target, "new.save", sample(40))
    shutil.rmtree(source.path)
    preview = migrations.undo_preview(task["id"])
    undo = migrations.undo(task["id"], preview["id"], "undo")
    result = migrations.process(undo["id"])
    assert result["state"] == "completed"
    for path in (Path(target.path) / "game/saves/old.save", other / "old.save"):
        assert save_codec.unpack(path.read_bytes())[0] == save_codec.unpack(sample(2))[0]
        assert path.stat().st_mtime_ns == 20
        assert saves._trusted(target.project_id, path.read_bytes(), False)
    assert not (Path(target.path) / "game/saves/added.save").exists()
    assert not (other / "added.save").exists()
    assert (Path(target.path) / "game/saves/new.save").read_bytes() == sample(40)
    assert len(saves.backups(target.project_id, saves.identifier("old.save"))) == 2


def test_partial_write_rolls_back_and_crash_recovers_before_unlock(setup, monkeypatch):
    migrations, saves, source, target = setup
    put(source, "one.save", sample(10))
    put(source, "two.save", sample(20))
    old = put(target, "one.save", sample(1))
    original = migrations._write
    calls = []

    def fail(*args):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("fixture disk full")
        return original(*args)

    monkeypatch.setattr(migrations, "_write", fail)
    task = run(migrations, target, migrations.preview(target.project_id, source.project_id))
    assert task["state"] == "failed" and old.read_bytes() == sample(1)
    assert not (Path(target.path) / "game/saves/two.save").exists()
    calls.clear()

    def crash(*args):
        original(*args)
        raise KeyboardInterrupt("fixture process loss")

    monkeypatch.setattr(migrations, "_write", crash)
    queued = migrations.commit(
        target.project_id, migrations.preview(target.project_id, source.project_id)["id"], "crash"
    )
    with pytest.raises(KeyboardInterrupt):
        migrations.process(queued["id"])
    from vn_stream.saves.migrations import SaveMigrations

    recovered = SaveMigrations(Saves(saves.library, Idle()))
    assert recovered.get(queued["id"])["state"] == "failed"
    assert old.read_bytes() == sample(1)
    check_busy(saves.library, source.project_id)
    check_busy(saves.library, target.project_id)


def test_trusted_source_bypasses_editor_budget_and_first_target_saves(setup, monkeypatch):
    migrations, saves, source, target = setup
    put(source, "one.save", Signatures(saves.library, source.project_id).resign(sample()))
    (Path(target.path) / "game/saves").rmdir()

    def limited(_):
        raise save_codec.Invalid("fixture parse limit")

    monkeypatch.setattr(save_codec, "parse", limited)
    preview = migrations.preview(target.project_id, source.project_id)
    assert preview["counts"]["valid"] == 1
    assert run(migrations, target, preview)["state"] == "completed"


def test_reserved_projects_cannot_be_reregistered(setup):
    migrations, saves, source, target = setup
    put(source, "slot.save", sample())
    migrations.commit(
        target.project_id,
        migrations.preview(target.project_id, source.project_id)["id"],
        "reserved",
    )
    for game in (source, target):
        with pytest.raises(ValueError):
            saves.library.register(Path(game.path), game.name)


def test_failed_recovery_keeps_both_guards_and_recovery_is_repeatable(setup, monkeypatch):
    migrations, saves, source, target = setup
    put(source, "slot.save", sample(9))
    destination = put(target, "slot.save", sample(1))
    original = migrations._write

    def crash(*args):
        original(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(migrations, "_write", crash)
    task = migrations.commit(
        target.project_id, migrations.preview(target.project_id, source.project_id)["id"], "crash"
    )
    with pytest.raises(KeyboardInterrupt):
        migrations.process(task["id"])
    backup = next((saves.root / target.project_id).glob("*/0"))
    moved = backup.with_name("temporarily-unavailable")
    backup.rename(moved)
    from vn_stream.saves.migrations import SaveMigrations

    recovered = SaveMigrations(Saves(saves.library, Idle()))
    assert recovered.get(task["id"])["state"] == "recovering"
    for game in (source, target):
        with pytest.raises(ValueError):
            check_busy(saves.library, game.project_id)
    moved.rename(backup)
    recovered.recover()
    recovered.recover()
    assert destination.read_bytes() == sample(1)
    assert recovered.get(task["id"])["state"] == "failed"


def test_missing_and_damaged_keys_and_busy_sessions(setup):
    migrations, saves, source, target = setup
    put(source, "slot.save", sample())
    source_key = Path(source.path) / ".vn-stream/home/.renpy/tokens/security_keys.txt"
    source_key.parent.mkdir(parents=True)
    source_key.write_text("damaged")
    assert migrations.preview(target.project_id, source.project_id)["counts"]["valid"] == 1
    target_key = Path(target.path) / ".vn-stream/home/.renpy/tokens/security_keys.txt"
    target_key.parent.mkdir(parents=True)
    target_key.write_text("damaged")
    with pytest.raises(ValueError):
        migrations.preview(target.project_id, source.project_id)
    assert target_key.read_text() == "damaged"
    target_key.unlink()
    for game in (source, target):
        saves.sessions.status = lambda game=game: {
            "state": "running",
            "project_id": game.project_id,
        }
        with pytest.raises(ValueError):
            migrations.preview(target.project_id, source.project_id)


def test_disk_space_is_checked_before_queue_and_worker(setup, monkeypatch):
    migrations, _, source, target = setup
    put(source, "slot.save", sample())
    preview = migrations.preview(target.project_id, source.project_id)
    usage = shutil.disk_usage(migrations.root)
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage._replace(free=0))
    with pytest.raises(ValueError):
        migrations.commit(target.project_id, preview["id"], "full")
    assert migrations.history(target.project_id) == []


def test_undo_preview_rejects_later_progress(setup):
    migrations, _, source, target = setup
    put(source, "slot.save", sample())
    task = run(migrations, target, migrations.preview(target.project_id, source.project_id))
    preview = migrations.undo_preview(task["id"])
    put(target, "slot.save", sample(90))
    with pytest.raises(ValueError):
        migrations.undo(task["id"], preview["id"], "stale")


def test_api_migration_routes_are_static_authenticated_and_async(tmp_path):
    import time

    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    paths = []
    for name in ("source", "target"):
        path = tmp_path / "nas" / name
        (path / "game/saves").mkdir(parents=True)
        (path / "game/script.rpy").write_text("label start: pass")
        paths.append(path)
    (paths[0] / "game/saves/slot.save").write_bytes(sample())
    app = create_app(tmp_path / "data", [tmp_path / "nas"], "secret")
    with TestClient(app) as client:
        sign_in(client, "secret")
        games = [
            api_data(client.post("/api/v2/projects", json={"path": str(path)})) for path in paths
        ]
        prefix = "/api/v2/projects/" + games[1]["project_id"] + "/saves"
        response = client.get(prefix + "/migration-sources")
        assert response.status_code == 200, response.text
        assert api_data(response)[0]["counts"]["manual"] == 1
        response = client.post(
            prefix + "/migrations/preview", json={"source_id": games[0]["project_id"]}
        )
        assert response.status_code == 200, response.text
        preview = api_data(response)
        response = client.post(
            prefix + "/migrations", json={"preview_id": preview["id"], "request_id": "api"}
        )
        assert response.status_code == 202, response.text
        uid = api_data(response)["task_id"]
        for _ in range(200):
            task = api_data(client.get("/api/v2/tasks/save-migrations/" + uid))
            if task["state"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        assert task["state"] == "completed", task
        assert api_data(client.get(prefix + "/migrations"))[0]["task_id"] == uid
        preview = api_data(client.post("/api/v2/tasks/save-migrations/" + uid + "/undo-preview"))
        response = client.post(
            "/api/v2/tasks/save-migrations/" + uid + "/undo",
            json={"preview_id": preview["id"], "request_id": "undo"},
        )
        assert response.status_code == 202, response.text
        assert "target_root" not in api_data(response) and "journal" not in api_data(response)
        client.cookies.clear()
        assert client.get("/api/v2/tasks/save-migrations/" + uid).status_code == 401


def test_crash_before_new_save_directories_are_created_recovers(setup, monkeypatch):
    migrations, saves, source, target = setup
    put(source, "slot.save", sample())
    (Path(target.path) / "game/saves").rmdir()
    task = migrations.commit(
        target.project_id,
        migrations.preview(target.project_id, source.project_id)["id"],
        "before-directories",
    )
    original = migrations._save

    def crash(record):
        result = original(record)
        if record["journal"]:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(migrations, "_save", crash)
    with pytest.raises(KeyboardInterrupt):
        migrations.process(task["id"])
    from vn_stream.saves.migrations import SaveMigrations

    recovered = SaveMigrations(Saves(saves.library, Idle()))
    assert recovered.get(task["id"])["state"] == "failed"
    assert not (Path(target.path) / "game/saves").exists()


def test_recovery_rejects_replaced_destination_directory(setup, monkeypatch):
    migrations, saves, source, target = setup
    put(source, "slot.save", sample())
    destination = put(target, "slot.save", sample(1))
    original = migrations._write

    def crash(*args):
        original(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(migrations, "_write", crash)
    task = migrations.commit(
        target.project_id,
        migrations.preview(target.project_id, source.project_id)["id"],
        "replace-directory",
    )
    with pytest.raises(KeyboardInterrupt):
        migrations.process(task["id"])
    destination.parent.rename(destination.parent.with_name("old-saves"))
    destination.parent.mkdir()
    destination.write_bytes(sample(99))
    from vn_stream.saves.migrations import SaveMigrations

    recovered = SaveMigrations(Saves(saves.library, Idle()))
    assert recovered.get(task["id"])["state"] == "recovering"
    assert destination.read_bytes() == sample(99)


def test_sources_use_cached_display_name_and_release_without_refresh(setup):
    migrations, saves, source, target = setup
    cache = saves.library.data / "cache/projects" / (source.project_id + ".json")
    cache.parent.mkdir()
    cache.write_text(json.dumps({"display_name": "Cached title", "release_version": "1.2.3"}))
    result = migrations.sources(target.project_id)[0]
    assert result["name"] == "Cached title" and result["release_version"] == "1.2.3"


def test_import_request_cannot_reserve_migrating_source(setup):
    from vn_stream.imports.jobs import ImportJobs

    migrations, saves, source, target = setup
    imports = ImportJobs(saves.library, recover=False)
    saves.imports = imports
    put(source, "slot.save", sample())
    migrations.commit(
        target.project_id,
        migrations.preview(target.project_id, source.project_id)["id"],
        "reserved",
    )
    with pytest.raises(ValueError):
        imports.create("import", path=source.path)
    assert imports.rows() == []


def test_change_after_backup_before_first_write_is_never_rolled_back(setup, monkeypatch):
    migrations, _, source, target = setup
    put(source, "slot.save", sample(9))
    destination = put(target, "slot.save", sample(1))
    backup = migrations._backup

    def changed(*args):
        result = backup(*args)
        destination.write_bytes(sample(77))
        return result

    monkeypatch.setattr(migrations, "_backup", changed)
    task = run(migrations, target, migrations.preview(target.project_id, source.project_id))
    assert task["state"] == "failed"
    assert destination.read_bytes() == sample(77)


def test_concurrent_commits_have_one_reservation_and_durable_idempotency(setup):
    from concurrent.futures import ThreadPoolExecutor

    from vn_stream.saves.migrations import SaveMigrations

    migrations, saves, source, target = setup
    put(source, "slot.save", sample())
    preview = migrations.preview(target.project_id, source.project_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(migrations.commit, target.project_id, preview["id"], "same")
            for _ in range(2)
        ]
        tasks = [future.result() for future in futures]
    assert tasks[0]["id"] == tasks[1]["id"]
    with pytest.raises(ValueError):
        migrations.commit(target.project_id, preview["id"], "different")
    migrations.process(tasks[0]["id"])
    restarted = SaveMigrations(saves)
    assert restarted.commit(target.project_id, preview["id"], "same")["state"] == "completed"
    assert len(restarted.history(target.project_id)) == 1


def test_persistent_resigning_enforces_final_size_limit(setup, monkeypatch):
    _, saves, _, target = setup
    payload = zlib.compress(pickle.dumps({"value": 1}, 2))
    monkeypatch.setattr(save_codec, "MAX_FILE", len(payload) + 1)
    with pytest.raises(ValueError):
        Signatures(saves.library, target.project_id).resign(payload, True)


def test_undo_invalid_before_file_fails_safely_without_changing_progress(setup):
    migrations, _, source, target = setup
    put(source, "slot.save", sample())
    original = put(target, "slot.save", b"original invalid container")
    task = run(migrations, target, migrations.preview(target.project_id, source.project_id))
    current = original.read_bytes()
    with pytest.raises(ValueError) as error:
        migrations.undo_preview(task["id"])
    assert error.value.args[0].code == "saves.unsupported"
    assert original.read_bytes() == current
    assert len(migrations.history(target.project_id)) == 1


def test_task_reports_phases_and_safe_per_file_results(setup, monkeypatch):
    migrations, _, source, target = setup
    put(source, "slot.save", sample())
    put(source, "bad.save", b"invalid")
    preview = migrations.preview(target.project_id, source.project_id)
    task = migrations.commit(target.project_id, preview["id"], "phases", True)
    assert task["phase"] == "queued"
    write = migrations._write
    observed = []

    def inspect(*args):
        status = migrations.get(task["id"])
        observed.append(status["phase"])
        assert status["prepared"] == 1 and status["backed_up"] == 1
        return write(*args)

    monkeypatch.setattr(migrations, "_write", inspect)
    result = migrations.process(task["id"])
    assert observed == ["writing"] and result["phase"] == "completed"
    assert result["results"] == [
        {
            "name": "bad.save",
            "action": "add",
            "state": "skipped",
            "reason": {"code": "saves.unsupported", "params": {}},
        },
        {"name": "slot.save", "action": "add", "state": "completed", "reason": None},
    ]


def test_corrupt_recovery_backup_keeps_games_locked(setup, monkeypatch):
    migrations, saves, source, target = setup
    put(source, "slot.save", sample(9))
    destination = put(target, "slot.save", sample(1))
    write = migrations._write

    def crash(*args):
        write(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(migrations, "_write", crash)
    task = migrations.commit(
        target.project_id,
        migrations.preview(target.project_id, source.project_id)["id"],
        "corrupt-backup",
    )
    with pytest.raises(KeyboardInterrupt):
        migrations.process(task["id"])
    before_recovery = destination.read_bytes()
    backup = next((saves.root / target.project_id).glob("*/0"))
    backup.write_bytes(b"corrupt")
    from vn_stream.saves.migrations import SaveMigrations

    restarted = SaveMigrations(saves)
    assert restarted.get(task["id"])["state"] == "recovering"
    assert destination.read_bytes() == before_recovery
    for game in (source, target):
        with pytest.raises(ValueError):
            check_busy(saves.library, game.project_id)


def test_engine_sync_location_is_discovered_before_first_target_launch(setup):
    migrations, saves, source, target = setup
    put(source, "slot.save", sample(42), 10)
    put(target, "slot.save", sample(1), 20)
    sync = Path(target.path) / "game/saves/sync"
    sync.mkdir()
    (sync / "slot.save").write_bytes(sample(5))
    os.utime(sync / "slot.save", ns=(30, 30))
    preview = migrations.preview(target.project_id, source.project_id)
    assert preview["entries"][0]["target_copies"] == 2
    task = run(migrations, target, preview)
    assert task["state"] == "completed"
    for path in (Path(target.path) / "game/saves/slot.save", sync / "slot.save"):
        assert save_codec.unpack(path.read_bytes())[0] == save_codec.unpack(sample(42))[0]
        assert path.stat().st_mtime_ns == 10
        assert saves._trusted(target.project_id, path.read_bytes(), False)
    before = migrations.undo_preview(task["id"])
    undo = migrations.undo(task["id"], before["id"], "undo-sync")
    assert migrations.process(undo["id"])["state"] == "completed"
    for path in (Path(target.path) / "game/saves/slot.save", sync / "slot.save"):
        assert save_codec.unpack(path.read_bytes())[0] == save_codec.unpack(sample(5))[0]
        assert path.stat().st_mtime_ns == 30


def test_adapter_confirmed_save_subdirectory_participates_in_source_primary(setup):
    migrations, _, source, target = setup
    put(source, "slot.save", sample(1), 10)
    extra = Path(source.path) / "game/saves/custom-location"
    extra.mkdir()
    latest = extra / "slot.save"
    latest.write_bytes(sample(42))
    os.utime(latest, ns=(20, 20))
    (Path(source.path) / ".vn-stream/save-locations.json").write_text(
        json.dumps({"locations": ["game/saves/custom-location"]})
    )
    preview = migrations.preview(target.project_id, source.project_id)
    assert preview["entries"][0]["source_copies"] == 2
    assert run(migrations, target, preview)["state"] == "completed"
    target_save = Path(target.path) / "game/saves/slot.save"
    assert save_codec.unpack(target_save.read_bytes())[0] == save_codec.unpack(sample(42))[0]
    assert latest.read_bytes() == sample(42)


def test_known_sync_location_cannot_follow_a_symlink(setup, tmp_path):
    migrations, _, source, target = setup
    put(source, "slot.save", sample())
    outside = tmp_path / "outside"
    outside.mkdir()
    (Path(target.path) / "game/saves/sync").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        migrations.preview(target.project_id, source.project_id)


def corrupt_compressed_member(compression, member):
    """Damage one stream while retaining valid local and central ZIP headers."""
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(sample())) as original,
        zipfile.ZipFile(output, "w", compression=compression) as archive,
    ):
        for entry in original.infolist():
            archive.writestr(entry.filename, original.read(entry))
    with zipfile.ZipFile(output) as archive:
        offset = archive.getinfo(member).header_offset
    data = bytearray(output.getvalue())
    name_size, extra_size = struct.unpack_from("<HH", data, offset + 26)
    compressed = offset + 30 + name_size + extra_size
    # LZMA has a four-byte ZIP codec header before its filter properties.
    data[compressed + (4 if compression == zipfile.ZIP_LZMA else 0)] = 0xFF
    return bytes(data)


@pytest.mark.parametrize("compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_LZMA, zipfile.ZIP_BZIP2])
@pytest.mark.parametrize("member", ["log", "screenshot.png"])
def test_corrupt_compressed_member_is_invalid_and_can_be_explicitly_skipped(
    setup, compression, member
):
    migrations, _, source, target = setup
    damaged = corrupt_compressed_member(compression, member)
    put(source, "damaged.save", damaged)
    put(source, "valid.save", sample(42))
    preview = migrations.preview(target.project_id, source.project_id)
    assert preview["counts"]["valid"] == 1 and preview["counts"]["invalid"] == 1
    assert preview["entries"][0]["reason"] == {"code": "saves.unsupported", "params": {}}
    with pytest.raises(ValueError):
        migrations.commit(target.project_id, preview["id"], "confirm-invalid")
    task = run(migrations, target, preview, "confirm-invalid", skip_invalid=True)
    assert task["state"] == "completed" and task["skipped"] == 1
    assert not (Path(target.path) / "game/saves/damaged.save").exists()
    assert (
        save_codec.unpack((Path(target.path) / "game/saves/valid.save").read_bytes())[0]
        == save_codec.unpack(sample(42))[0]
    )
    assert (Path(source.path) / "game/saves/damaged.save").read_bytes() == damaged


@pytest.mark.parametrize("persistent", [False, True])
def test_migration_and_undo_sign_with_target_key_even_when_source_key_is_trusted(setup, persistent):
    migrations, saves, source, target = setup
    source_signer = Signatures(saves.library, source.project_id)
    target_signer = Signatures(saves.library, target.project_id)
    source_keys, _ = source_signer.keys(create=True)
    target_keys, _ = target_signer.keys(create=True)
    source_public = base64.b64encode(source_keys[0].verifying_key.to_der())
    target_public = base64.b64encode(target_keys[0].verifying_key.to_der())
    target_keyfile = Path(target.path) / ".vn-stream/home/.renpy/tokens/security_keys.txt"
    with target_keyfile.open("ab") as stream:
        stream.write(b"verifying-key " + source_public + b"\n")

    def raw(value):
        return zlib.compress(pickle.dumps({"value": value}, 2)) if persistent else sample(value)

    incoming = source_signer.resign(raw(42), persistent)
    before = source_signer.resign(raw(5), persistent)
    name = "persistent" if persistent else "slot.save"
    put(source, name, incoming, 20)
    destination = put(target, name, before, 10)
    # Existing callers may retain a signature already accepted by target trust policy.
    assert target_signer.resign(incoming, persistent) == incoming

    def assert_target_signature(data, original):
        log, signatures, _ = save_codec.unpack(data, persistent)
        target_lines = [
            line.split() for line in signatures.splitlines() if line.split()[1] == target_public
        ]
        assert len(target_lines) == 1
        assert target_keys[0].verifying_key.verify(base64.b64decode(target_lines[0][2]), log)
        assert log == save_codec.unpack(original, persistent)[0]
        if persistent:
            assert data.startswith(raw(42) if original == incoming else raw(5))
        else:
            with (
                zipfile.ZipFile(io.BytesIO(original)) as previous,
                zipfile.ZipFile(io.BytesIO(data)) as current,
            ):
                for member in previous.namelist():
                    if member != "signatures":
                        assert current.read(member) == previous.read(member)

    task = run(migrations, target, migrations.preview(target.project_id, source.project_id))
    assert task["state"] == "completed"
    assert_target_signature(destination.read_bytes(), incoming)
    assert destination.stat().st_mtime_ns == 20
    undo_preview = migrations.undo_preview(task["id"])
    undo = migrations.undo(task["id"], undo_preview["id"], "undo-local-signature")
    assert migrations.process(undo["id"])["state"] == "completed"
    assert_target_signature(destination.read_bytes(), before)
    assert destination.stat().st_mtime_ns == 10
    assert (Path(source.path) / "game/saves" / name).read_bytes() == incoming
    assert (
        saves.backup_file(
            target.project_id,
            saves.identifier(name),
            saves.backups(target.project_id, saves.identifier(name))[-1]["id"],
        ).read_bytes()
        == before
    )


def test_undo_preview_rejects_invalid_before_file_before_creating_preview(setup):
    migrations, saves, source, target = setup
    put(source, "slot.save", sample())
    put(target, "slot.save", b"invalid before file")
    task = run(migrations, target, migrations.preview(target.project_id, source.project_id))
    with saves.library.db.transaction() as db:
        count = db.execute("SELECT COUNT(*) FROM save_migration_previews").fetchone()[0]
    with pytest.raises(ValueError) as error:
        migrations.undo_preview(task["id"])
    assert error.value.args[0].code == "saves.unsupported"
    with saves.library.db.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM save_migration_previews").fetchone()[0] == count


def test_migration_worker_retries_transient_database_read_lock(setup, monkeypatch):
    import sqlite3
    import threading
    import time
    from contextlib import contextmanager

    migrations, saves, source, target = setup
    put(source, "1-1.save", sample())
    preview = migrations.preview(target.project_id, source.project_id)
    task = migrations.commit(target.project_id, preview["id"], "database-busy")
    original = saves.library.db.transaction
    failed = []

    @contextmanager
    def database():
        if threading.current_thread() is thread and not failed:
            failed.append(True)
            raise sqlite3.OperationalError("database is locked")
        with original() as db:
            yield db

    monkeypatch.setattr(saves.library.db, "transaction", database)
    thread = threading.Thread(target=migrations._run)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if migrations.get(task["id"])["state"] == "completed":
                break
            time.sleep(0.02)
        assert migrations.get(task["id"])["state"] == "completed"
    finally:
        migrations.stop.set()
        migrations.wake.set()
        thread.join(3)
