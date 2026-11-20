import io
import pickle
import time
import zipfile

import pytest

from vn_stream.assets.operations import Assets
from vn_stream.projects.catalog import ProjectCatalog
from vn_stream.saves.operations import Saves
from vn_stream.saves.renpy import pickle_codec as save_codec


class Sessions:
    def status(self):
        return {"state": "idle", "project_id": None}


@pytest.fixture
def setup(tmp_path):
    p = tmp_path / "nas/project"
    (p / "game/saves").mkdir(parents=True)
    (p / "game/script.rpy").write_text("label start: pass")
    (p / "game/image.png").write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"fixture")
    library = ProjectCatalog(tmp_path / "data", [tmp_path / "nas"])
    game = library.register(p, "Sample")
    return library, game, p


def settle(service, uid):
    for _ in range(1000):
        value = service.job(uid)
        if value["state"] not in ("running", "queued"):
            return value
        time.sleep(0.005)
    raise AssertionError("job did not finish")


def sample():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("log", pickle.dumps(({"store.money": 5, "store.name": "中文"}, {}), 2))
        z.writestr("json", "{}")
    return out.getvalue()


def test_extract_snapshot_pagination_and_rebuild(setup):
    lib, g, p = setup
    service = Assets(lib)
    job = settle(service, service.extract(g.project_id, "req")["id"])
    assert job["state"] == "completed", job
    assert service.extract(g.project_id, "req")["id"] == job["id"]
    listing = service.list(g.project_id, category="image")
    assert listing["total"] == 1
    entry = listing["entries"][0]
    original = service.file(g.project_id, entry["id"]).read_bytes()
    assert Assets(lib).file(g.project_id, entry["id"]).read_bytes() == original
    (p / "game/image.png").unlink()
    with pytest.raises(ValueError):
        service.file(g.project_id, entry["id"])
    assert service.summary(g.project_id)["counts"]["image"] == 1


def test_saves_revision_backup_delete_restore_and_busy(setup):
    lib, g, p = setup
    original = sample()
    (p / "game/saves/1-1-LT1.save").write_bytes(original)
    service = Saves(lib, Sessions())
    item = service.list(g.project_id)["saves"][0]
    view = service.variables(g.project_id, item["id"])
    field = next(f for f in view["fields"] if f["name"] == "store.money")
    modified = service.update(
        g.project_id, item["id"], view["revision"], [dict(field, value="999")]
    )
    assert (
        next(
            f
            for f in service.variables(g.project_id, item["id"])["fields"]
            if f["id"] == field["id"]
        )["value"]
        == "999"
    )
    with pytest.raises(ValueError):
        service.update(g.project_id, item["id"], view["revision"], [dict(field, value="0")])
    backups = service.backups(g.project_id, item["id"])
    assert len(backups) == 1
    service.restore(g.project_id, item["id"], backups[0]["id"], modified["revision"])
    assert (
        save_codec.unpack((p / "game/saves/1-1-LT1.save").read_bytes())[0]
        == save_codec.unpack(original)[0]
    )
    service.sessions.status = lambda: {"state": "running", "project_id": g.project_id}
    with pytest.raises(ValueError):
        service.delete(
            g.project_id, item["id"], service.variables(g.project_id, item["id"])["revision"]
        )
    assert (p / "game/saves/1-1-LT1.save").exists()


def test_explicit_external_upload_resigns_before_replacing(setup):
    import base64

    import ecdsa

    lib, g, p = setup
    key = ecdsa.SigningKey.generate(curve=ecdsa.NIST256p)
    raw = sample()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(out, "w") as dest:
        for item in source.infolist():
            dest.writestr(item, source.read(item))
        log = source.read("log")
        dest.writestr(
            "signatures",
            b"signature "
            + base64.b64encode(key.verifying_key.to_der())
            + b" "
            + base64.b64encode(key.sign(log))
            + b"\n",
        )
    target = p / "game/saves/1-1-LT1.save"
    target.write_bytes(raw)
    saves = Saves(lib, Sessions())
    item = saves.list(g.project_id)["saves"][0]
    revision = saves.variables(g.project_id, item["id"])["revision"]
    saves.upload(g.project_id, target.name, out.getvalue(), revision)
    assert saves._trusted(g.project_id, target.read_bytes(), False)
    assert save_codec.unpack(target.read_bytes())[0] == save_codec.unpack(raw)[0]
    assert saves.upload(g.project_id, "external-LT1.save", out.getvalue())["id"]


def test_old_snapshot_cleanup_failure_does_not_fail_publication(setup, monkeypatch):
    from vn_stream.assets import operations as module

    lib, g, p = setup
    service = Assets(lib)
    first = settle(service, service.extract(g.project_id, "first")["id"])
    old = p / ".vn-stream/assets" / ("snapshot-" + first["id"])
    import os

    os.utime(old, (1, 1))
    original = module.shutil.rmtree

    def refuse(path, *args, **kwargs):
        if path == old:
            raise PermissionError("fixture cleanup denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "rmtree", refuse)
    second = settle(service, service.extract(g.project_id, "second")["id"])
    service.threads[second["id"]].join(3)
    second = service.job(second["id"])
    assert second["state"] == "completed"
    assert service.summary(g.project_id)["ready"]


def test_duplicate_save_locations_delete_and_restore(setup):
    import json

    lib, g, p = setup
    second = p / ".vn-stream/home/.renpy/fixture"
    second.mkdir(parents=True)
    original = sample()
    for folder in (p / "game/saves", second):
        (folder / "1-1.save").write_bytes(original)
    (p / ".vn-stream/save-locations.json").write_text(
        json.dumps({"locations": ["game/saves", ".vn-stream/home/.renpy/fixture"]})
    )
    service = Saves(lib, Sessions())
    item = service.list(g.project_id)["saves"][0]
    assert item["copies"] == 2
    revision = service.variables(g.project_id, item["id"])["revision"]
    service.delete(g.project_id, item["id"], revision)
    assert not (second / "1-1.save").exists()
    backup = service.backups(g.project_id, item["id"])[0]
    service.restore(g.project_id, item["id"], backup["id"], None)
    assert (
        save_codec.unpack((second / "1-1.save").read_bytes())[0] == save_codec.unpack(original)[0]
    )
    assert (
        save_codec.unpack((p / "game/saves/1-1.save").read_bytes())[0]
        == save_codec.unpack(original)[0]
    )


def test_interrupted_write_recovers_all_old_copies(setup):
    import json

    lib, g, p = setup
    path = p / "game/saves/1-1.save"
    original = sample()
    path.write_bytes(original)
    service = Saves(lib, Sessions())
    uid = service.identifier(path.name)
    operation = "a" * 32
    folder = lib.data / "save-backups" / g.project_id / operation
    folder.mkdir(parents=True)
    (folder / "0").write_bytes(original)
    record = dict(
        id=operation,
        project_id=g.project_id,
        save_id=uid,
        kind="edit",
        created_at=1,
        state="running",
        entries=[dict(path=str(path), backup="0", existed=True)],
    )
    (folder / "record.json").write_text(json.dumps(record))
    with lib.db.transaction() as db:
        db.execute(
            "INSERT INTO content_operations VALUES(?,?,?,?,?)",
            (operation, g.project_id, "saves", "running", json.dumps(record)),
        )
    path.write_bytes(b"interrupted write")
    Saves(lib, Sessions())
    assert path.read_bytes() == original
    with lib.db.transaction() as db:
        assert (
            db.execute("SELECT state FROM content_operations WHERE id=?", (operation,)).fetchone()[
                0
            ]
            == "recovered"
        )


@pytest.mark.parametrize("version", ["2.0", "3.0"])
def test_archive_extraction_streams_prefix_and_loose_override(setup, version):
    import pickle
    import zlib

    lib, g, p = setup
    content = b"archive bytes"
    prefix = "prefix:"
    empty = f"RPA-{version} {0:016x}" + (" 00000000" if version == "3.0" else "") + "\n"
    start = len(empty.encode())
    offset = start + len(content)
    header = f"RPA-{version} {offset:016x}" + (" 00000000" if version == "3.0" else "") + "\n"
    index = {
        "nested/blob.bin": [(start, len(content), prefix)],
        "image.png": [(start, len(content))],
    }
    (p / "game/archive.rpa").write_bytes(
        header.encode() + content + zlib.compress(pickle.dumps(index, 2))
    )
    service = Assets(lib)
    result = settle(service, service.extract(g.project_id, "archive")["id"])
    assert result["state"] == "completed", result
    entry = service.list(g.project_id, folder="nested")["entries"][0]
    assert service.file(g.project_id, entry["id"]).read_bytes() == prefix.encode() + content
    image = service.list(g.project_id, category="image")["entries"][0]
    assert (
        service.file(g.project_id, image["id"]).read_bytes().startswith(bytes.fromhex("89504e47"))
    )


def test_no_space_has_specific_task_message(setup, monkeypatch):
    from collections import namedtuple

    from vn_stream.assets import operations as module

    lib, g, p = setup
    monkeypatch.setattr(
        module.shutil, "disk_usage", lambda _: namedtuple("Usage", "total used free")(10, 10, 0)
    )
    service = Assets(lib)
    result = settle(service, service.extract(g.project_id, "full")["id"])
    assert result["state"] == "failed"
    assert result["messages"]["error"]["code"] == "assets.noSpace"


def test_malformed_display_metadata_stays_json_safe(setup):
    import json

    lib, g, p = setup
    path = p / "game/saves/odd.save"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("log", pickle.dumps(({"store.flag": True}, {}), 2))
        z.writestr("json", '{"_save_name":"\\ud800", "_ctime":NaN}')
    value = Saves(lib, Sessions()).list(g.project_id)
    json.dumps(value, allow_nan=False).encode("utf-8")
