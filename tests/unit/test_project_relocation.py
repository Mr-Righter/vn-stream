"""Offline layout moves are reversible and never rewrite game content."""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vn_stream.projects.catalog import ProjectCatalog


def fixture(tmp_path):
    root = tmp_path / "games"
    old = root / "slg/Test"
    (old / "game/saves").mkdir(parents=True)
    (old / "game/script.rpy").write_text("label start:\n    return\n")
    (old / "game/saves/1.save").write_bytes(b"original-progress")
    catalog = ProjectCatalog(root / ".vn-stream-data", [root], library_root=root)
    game = catalog.register(old, "Test")
    with catalog.db.transaction() as db:
        db.execute(
            "UPDATE projects SET path=? WHERE project_id=?", ("/games/Test", game.project_id)
        )
    return root, old, catalog, game


def test_relocation_preserves_identity_and_can_roll_back(tmp_path):
    from vn_stream.projects.relocation import LayoutMigration

    root, old, catalog, game = fixture(tmp_path)
    operation = LayoutMigration(catalog.data, root, {"/games": root / "slg"})
    plan = operation.preview()
    assert old.is_dir() and not (catalog.data / "layout-maintenance.json").exists()
    operation.apply(plan)
    destination = root / "renpy/Test"
    assert not old.exists()
    assert (destination / "game/saves/1.save").read_bytes() == b"original-progress"
    assert catalog.get(game.project_id).path == "/games/renpy/Test"
    operation.rollback(plan)
    assert old.is_dir() and not destination.exists()
    assert catalog.get(game.project_id).path == "/games/Test"


def test_relocation_rejects_collision_before_moving_anything(tmp_path):
    from vn_stream.projects.relocation import LayoutMigration

    root, old, catalog, _ = fixture(tmp_path)
    (root / "renpy/Test").mkdir(parents=True)
    with pytest.raises(ValueError):
        LayoutMigration(catalog.data, root, {"/games": root / "slg"}).preview()
    assert old.is_dir()


def test_relocation_updates_backup_paths_but_not_user_text(tmp_path):
    from vn_stream.projects.relocation import LayoutMigration

    root, _, catalog, game = fixture(tmp_path)
    record = {
        "project_id": game.project_id,
        "entries": [{"path": "/games/Test/game/saves/1.save"}],
        "message": "/games/Test is a game title, do not rewrite",
    }
    backup = catalog.data / "save-backups" / game.project_id / "backup/record.json"
    backup.parent.mkdir(parents=True)
    backup.write_text(json.dumps(record))
    operation = LayoutMigration(catalog.data, root, {"/games": root / "slg"})
    plan = operation.preview()
    operation.apply(plan)
    updated = json.loads(backup.read_text())
    assert updated["entries"][0]["path"] == "/games/renpy/Test/game/saves/1.save"
    assert updated["message"] == record["message"]
    operation.rollback(plan)
    assert json.loads(backup.read_text()) == record


def test_relocation_restart_can_rollback_after_move_before_database(tmp_path, monkeypatch):
    from vn_stream.projects.relocation import LayoutMigration

    root, old, catalog, _ = fixture(tmp_path)
    operation = LayoutMigration(catalog.data, root, {"/games": root / "slg"})
    plan = operation.preview()

    def interrupted(*args):
        raise RuntimeError("simulated power loss")

    monkeypatch.setattr(operation, "_update_database", interrupted)
    with pytest.raises(RuntimeError):
        operation.apply(plan)
    assert (catalog.data / "layout-maintenance.json").is_file()
    restarted = LayoutMigration(catalog.data, root, {"/games": root / "slg"})
    restarted.rollback(plan)
    assert old.is_dir() and not (catalog.data / "layout-maintenance.json").exists()


@given(st.lists(st.text(alphabet="abc/ .中", min_size=1, max_size=20), max_size=12))
def test_relocation_path_mapping_is_boundary_aware(names):
    from vn_stream.projects.relocation import relocate_value

    mapping = {"/games/A": "/games/renpy/A"}
    for name in names:
        value = "/games/A/" + name
        assert relocate_value(value, mapping) == "/games/renpy/A/" + name
    assert relocate_value("/games/Another/file", mapping) == "/games/Another/file"


def test_running_service_lease_prevents_offline_moves(tmp_path):
    from vn_stream.projects.relocation import LayoutMigration
    from vn_stream.storage.leases import ServiceLease

    root, old, catalog, _ = fixture(tmp_path)
    operation = LayoutMigration(catalog.data, root, {"/games": root / "slg"})
    plan = operation.preview()
    with ServiceLease(catalog.data):
        with pytest.raises(ValueError, match="Stop the service"):
            operation.apply(plan)
    assert old.exists()


def test_unfinished_migration_blocks_service_start(tmp_path):
    from vn_stream.application.bootstrap import build_services

    root, _, catalog, _ = fixture(tmp_path)
    (catalog.data / "layout-maintenance.json").write_text('{"id":"pending"}')
    with pytest.raises(ValueError, match="migration"):
        build_services(catalog.data, [root], library_root=root)


def test_completed_apply_is_idempotent_and_does_not_rewrite_later_metadata(tmp_path):
    from vn_stream.projects.relocation import LayoutMigration

    root, _, catalog, game = fixture(tmp_path)
    backup = catalog.data / "save-backups" / game.project_id / "backup/record.json"
    backup.parent.mkdir(parents=True)
    backup.write_text('{"message":"before"}')
    operation = LayoutMigration(catalog.data, root, {"/games": root / "slg"})
    plan = operation.preview()
    operation.apply(plan)
    backup.write_text('{"message":"after"}')
    operation.apply(plan)
    assert json.loads(backup.read_text())["message"] == "after"


def test_relocation_updates_typed_directory_identity_keys():
    from vn_stream.projects.relocation import relocate_record

    record = {
        "directory_identities": {"/games/Test/game/saves": [1, 2]},
        "target_root": "/games/Test",
        "snapshots": {"id": {"root": "/games/Test"}},
        "variables": {"/games/Test": "user key"},
    }
    updated = relocate_record(record, {"/games/Test": "/games/renpy/Test"})
    assert updated["directory_identities"] == {"/games/renpy/Test/game/saves": [1, 2]}
    assert updated["variables"] == record["variables"]
    assert updated["target_root"] == "/games/renpy/Test"
    assert updated["snapshots"]["id"]["root"] == "/games/renpy/Test"


def test_preparation_interrupt_blocks_service_and_can_resume(tmp_path, monkeypatch):
    from vn_stream.projects.relocation import LayoutMigration
    from vn_stream.storage.leases import ServiceLease

    root, old, catalog, _ = fixture(tmp_path)
    operation = LayoutMigration(catalog.data, root, {"/games": root / "slg"})
    plan = operation.preview()
    original = operation._backup_database
    monkeypatch.setattr(
        operation,
        "_backup_database",
        lambda *args: (_ for _ in ()).throw(RuntimeError("power loss")),
    )
    with pytest.raises(RuntimeError):
        operation.apply(plan)
    with pytest.raises(ValueError, match="migration"):
        ServiceLease(catalog.data)
    monkeypatch.setattr(operation, "_backup_database", original)
    operation.apply(plan)
    assert not old.exists()
    assert (root / "renpy/Test/game/saves/1.save").read_bytes() == b"original-progress"


def test_preparation_interrupt_can_rollback_without_published_journal(tmp_path, monkeypatch):
    from vn_stream.projects.relocation import LayoutMigration

    root, old, catalog, _ = fixture(tmp_path)
    operation = LayoutMigration(catalog.data, root, {"/games": root / "slg"})
    plan = operation.preview()
    monkeypatch.setattr(
        operation,
        "_backup_database",
        lambda *args: (_ for _ in ()).throw(RuntimeError("power loss")),
    )
    with pytest.raises(RuntimeError):
        operation.apply(plan)
    operation.rollback(plan)
    assert old.is_dir()
    assert not operation.marker.exists()


def test_apply_rejects_target_parent_replaced_with_symlink(tmp_path):
    from vn_stream.projects.relocation import LayoutMigration

    root, old, catalog, _ = fixture(tmp_path)
    operation = LayoutMigration(catalog.data, root, {"/games": root / "slg"})
    plan = operation.preview()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "renpy").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        operation.apply(plan)
    assert old.is_dir() and not (outside / "Test").exists()


@given(
    st.text(alphabet="abcXYZ0123- 中文", min_size=1, max_size=12).filter(lambda s: s.strip() == s),
    st.binary(max_size=128),
)
def test_generated_layout_round_trip_preserves_progress(name, payload):
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from vn_stream.projects.relocation import LayoutMigration

    with TemporaryDirectory() as temporary:
        root, old, catalog, game = fixture(Path(temporary))
        renamed = old.with_name(name)
        old.rename(renamed)
        (renamed / "game/saves/1.save").write_bytes(payload)
        logical = "/games/" + name
        with catalog.db.transaction() as db:
            db.execute("UPDATE projects SET path=? WHERE project_id=?", (logical, game.project_id))
        operation = LayoutMigration(catalog.data, root, {"/games": root / "slg"})
        plan = operation.preview()
        operation.apply(plan)
        assert (root / "renpy" / name / "game/saves/1.save").read_bytes() == payload
        assert catalog.get(game.project_id).project_id == game.project_id
        operation.rollback(plan)
        assert (renamed / "game/saves/1.save").read_bytes() == payload
        assert catalog.get(game.project_id).path == logical
