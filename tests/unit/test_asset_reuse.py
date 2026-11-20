"""Original resource references and bounded archive reads, without duplicate media."""

import base64
import hashlib
import pickle
import time
import zlib
from pathlib import Path

import pytest

from tests.auth_helpers import api_data
from vn_stream.assets.operations import Assets
from vn_stream.projects.catalog import ProjectCatalog
from vn_stream.storage.files import atomic_json


def environment(tmp_path):
    p = tmp_path / "nas/game-one"
    (p / "game").mkdir(parents=True)
    (p / "game/script.rpy").write_text("label start: pass")
    lib = ProjectCatalog(tmp_path / "data", [tmp_path / "nas"])
    return lib, lib.register(p, "Reuse"), p


def wait(service, job):
    for _ in range(1000):
        result = service.job(job["id"])
        if result["state"] != "running":
            return result
        time.sleep(0.005)
    pytest.fail("Index task timed out")


def test_source_virtual_seek_and_ranges(tmp_path):
    from vn_stream.assets.sources import Source

    p = tmp_path / "archive.rpa"
    p.write_bytes(b"XXabcYYdefZZ")
    descriptor = Source.describe(p, "archive.rpa", [(2, 3, b"P"), (7, 3, b"Q")])
    source = Source(tmp_path, descriptor)
    with source.open() as f:
        assert f.read(5) == b"PabcQ"
        assert f.seek(-4, 2) == 4
        assert f.read(100) == b"Qdef"
        f.seek(2)
        assert f.read(4) == b"bcQd"
    assert source.read_bytes() == b"PabcQdef"
    p.write_bytes(b"replacement")
    with pytest.raises(ValueError):
        source.open()


def test_loose_index_has_no_resource_copy_and_rejects_changes(tmp_path):
    lib, game, p = environment(tmp_path)
    target = p / "game/music.mp3"
    target.write_bytes(b"ID3" + b"x" * 1024)
    assets = Assets(lib)
    try:
        assert wait(assets, assets.extract(game.project_id, "new"))["state"] == "completed"
        root, entry = assets.entry(game.project_id, hashlib.sha256(b"music.mp3").hexdigest()[:32])
        assert not (root / "files").exists()
        assert assets.file(game.project_id, entry["id"]).read_bytes() == target.read_bytes()
        target.write_bytes(b"ID3changed")
        with pytest.raises(ValueError):
            assets.file(game.project_id, entry["id"]).read_bytes()
    finally:
        assets.close()


def test_migration_reuses_verified_original_and_keeps_missing_copy(tmp_path):
    lib, game, p = environment(tmp_path)
    original = p / "game/voice.ogg"
    original.write_bytes(b"OggSfixture")
    assets = Assets(lib)
    root = assets._root(game.project_id) / "snapshot-old"
    records = []
    for name, data in [("voice.ogg", original.read_bytes()), ("lost.png", b"lost original")]:
        uid = hashlib.sha256(name.encode()).hexdigest()[:32]
        path = root / "files" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        records.append(
            dict(
                id=uid,
                name=name,
                path=name,
                kind="audio" if name.endswith("ogg") else "image",
                mime="application/octet-stream",
                size_bytes=len(data),
                directory=False,
            )
        )
    atomic_json(root / "index.json", records)
    atomic_json(
        root.parent / "current.json",
        dict(generation=root.name, counts={}, size_bytes=23, updated_at=1),
    )
    try:
        result = wait(assets, assets.migrate(game.project_id, "migrate"))
        assert result["state"] == "completed", result
        assert not (root / "files/voice.ogg").exists()
        assert (root / "files/lost.png").read_bytes() == b"lost original"
        assert assets.file(game.project_id, records[0]["id"]).read_bytes() == original.read_bytes()
        assert assets.file(game.project_id, records[1]["id"]).read_bytes() == b"lost original"
        assert assets.summary(game.project_id)["retained_files"] == 1
        assert wait(assets, assets.migrate(game.project_id, "again"))["state"] == "completed"
    finally:
        assets.close()


def test_reference_rejects_symlinks_and_bounds(tmp_path):
    from vn_stream.assets.sources import Source

    original = tmp_path / "original"
    original.write_bytes(b"secret")
    record = Source.describe(original, "original")
    original.unlink()
    original.symlink_to("/etc/passwd")
    with pytest.raises((ValueError, OSError)):
        Source(tmp_path, record).open()
    original.unlink()
    original.write_bytes(b"secret")
    record = Source.describe(original, "original")
    record["chunks"] = [[4, 200, base64.b64encode(b"").decode()]]
    with pytest.raises(ValueError):
        Source(tmp_path, record).open()


def test_archive_range_download_is_bounded_and_never_materializes(tmp_path):
    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    p = tmp_path / "nas/project"
    (p / "game").mkdir(parents=True)
    (p / "game/script.rpy").write_text("label start: pass")
    data = b"SECRETabcSECRETdefSECRET"
    chunks = [(34 + 6, 3, "P"), (34 + 15, 3, "Q")]
    index = pickle.dumps({"clip.webm": chunks}, 2)
    (p / "game/media.rpa").write_bytes(
        f"RPA-3.0 {34 + len(data):016x} 00000000\n".encode() + data + zlib.compress(index)
    )
    with TestClient(create_app(tmp_path / "data", [tmp_path / "nas"], "secret")) as client:
        sign_in(client, "secret")
        game = api_data(client.post("/api/v2/projects", json={"path": str(p)}))
        prefix = "/api/v2/projects/" + game["project_id"] + "/assets"
        job = api_data(client.post(prefix + "/extract", json={"request_id": "range"}))
        for _ in range(200):
            state = api_data(client.get("/api/v2/tasks/assets/" + job["task_id"]))
            if state["state"] != "running":
                break
            time.sleep(0.01)
        assert state["state"] == "completed", state
        entry = api_data(client.get(prefix + "/entries?category=video"))["entries"][0]
        assert "source" not in entry
        url = prefix + "/" + entry["id"] + "/content"
        for header, wanted in [("bytes=2-5", b"bcQd"), ("bytes=-3", b"def"), ("bytes=4-", b"Qdef")]:
            response = client.get(url, headers={"Range": header})
            assert response.status_code == 206, response.text
            assert response.content == wanted
        assert client.get(url, headers={"Range": "bytes=8-9"}).status_code == 416
        full = client.get(url)
        assert full.content == b"PabcQdef"
        assert (
            client.get(url, headers={"Range": "bytes=0-1", "If-Range": '"outdated"'}).status_code
            == 200
        )
        assert not list((p / ".vn-stream/assets").glob("snapshot-*/files"))
        client.cookies.clear()
        assert client.get(url).status_code == 401


def test_decoder_single_chunk_uses_fd_and_multipart_temp_is_removed(tmp_path):
    from vn_stream.assets.sources import Source

    p = tmp_path / "archive"
    p.write_bytes(b"XXabcYYdefZZ")
    direct = Source(tmp_path, Source.describe(p, "archive", [(2, 3, b"")]))
    with direct.ffmpeg(tmp_path) as value:
        assert value.url.startswith("subfile,,start,2,end,5,,:/proc/self/fd/")
        assert value.pass_fds
        assert not list(tmp_path.glob("asset-input-*"))
    split = Source(tmp_path, Source.describe(p, "archive", [(2, 3, b"P"), (7, 3, b"Q")]))
    with pytest.raises(RuntimeError):
        with split.ffmpeg(tmp_path) as value:
            temporary = Path(value.url)
            assert temporary.read_bytes() == b"PabcQdef"
            raise RuntimeError("Decoder failed")
    assert not temporary.exists()


def test_migration_publication_failure_does_not_remove_copy(tmp_path, monkeypatch):
    from vn_stream.assets import reindex as asset_migration

    lib, game, p = environment(tmp_path)
    data = b"original contents"
    (p / "game/file.bin").write_bytes(data)
    assets = Assets(lib)
    root = assets._root(game.project_id) / "snapshot-legacy"
    (root / "files").mkdir(parents=True)
    (root / "files/file.bin").write_bytes(data)
    entry = dict(
        id="a" * 32,
        name="file.bin",
        path="file.bin",
        kind="other",
        mime="application/octet-stream",
        size_bytes=len(data),
        directory=False,
    )
    atomic_json(root / "index.json", [entry])
    atomic_json(
        root.parent / "current.json",
        dict(generation=root.name, counts={}, size_bytes=len(data), updated_at=1),
    )
    original = asset_migration.atomic_json

    def fail(path, value):
        if path.name == "index.json":
            raise OSError("Simulated disk failure")
        original(path, value)

    monkeypatch.setattr(asset_migration, "atomic_json", fail)
    assert wait(assets, assets.migrate(game.project_id, "fail"))["state"] == "failed"
    assert (root / "files/file.bin").read_bytes() == data
    monkeypatch.setattr(asset_migration, "atomic_json", original)
    assert wait(assets, assets.migrate(game.project_id, "retry"))["state"] == "completed"
    assert not (root / "files/file.bin").exists()
    assert assets.file(game.project_id, entry["id"]).read_bytes() == data
    assets.close()


def test_index_does_not_require_free_space_for_full_media(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from vn_stream.assets import operations as module

    lib, game, p = environment(tmp_path)
    with (p / "game/large.bin").open("wb") as f:
        f.truncate(128 * 1024**2)
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=70 * 1024**2))
    assets = Assets(lib)
    try:
        result = wait(assets, assets.extract(game.project_id, "space"))
        assert result["state"] == "completed", result
        assert not list((p / ".vn-stream/assets").glob("snapshot-*/files"))
    finally:
        assets.close()


def test_reindex_preserves_unique_legacy_resource_and_cleanup_cannot_remove_it(tmp_path):
    import os

    lib, game, p = environment(tmp_path)
    assets = Assets(lib)
    root = assets._root(game.project_id) / "snapshot-retained"
    (root / "files").mkdir(parents=True)
    (root / "files/unique.txt").write_bytes(b"only remaining copy")
    entry = dict(
        id=hashlib.sha256(b"unique.txt").hexdigest()[:32],
        name="unique.txt",
        path="unique.txt",
        kind="script",
        mime="text/plain",
        size_bytes=19,
        directory=False,
    )
    atomic_json(root / "index.json", [entry])
    atomic_json(
        root.parent / "current.json",
        dict(generation=root.name, counts={}, size_bytes=19, updated_at=1),
    )
    try:
        assert wait(assets, assets.migrate(game.project_id, "retain"))["state"] == "completed"
        assert wait(assets, assets.extract(game.project_id, "reindex"))["state"] == "completed"
        assert assets.file(game.project_id, entry["id"]).read_bytes() == b"only remaining copy"
        os.utime(root, (1, 1))
        assets._cleanup(root.parent, assets.summary(game.project_id)["generation"])
        assert (root / "files/unique.txt").is_file()
    finally:
        assets.close()


@pytest.mark.parametrize("multipart", [False, True])
def test_real_archive_video_poster_and_conversion_do_not_leave_inputs(tmp_path, multipart):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required")
    lib, game, p = environment(tmp_path)
    video = tmp_path / "authored.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=80x60:rate=5",
            "-t",
            "0.8",
            "-c:v",
            "libvpx",
            "-threads",
            "1",
            str(video),
        ],
        check=True,
    )
    data = video.read_bytes()
    chunks = (
        [(34, len(data))]
        if not multipart
        else [
            (38, len(data) // 2 - 4, data[:4].decode("latin1")),
            (34 + len(data) // 2, len(data) - len(data) // 2, ""),
        ]
    )
    index = pickle.dumps({"clip.webm": chunks}, 2)
    (p / "game/movie.rpa").write_bytes(
        f"RPA-3.0 {34 + len(data):016x} 00000000\n".encode() + data + zlib.compress(index)
    )
    assets = Assets(lib)
    try:
        result = wait(assets, assets.extract(game.project_id, "video"))
        assert result["state"] == "completed", result
        entry = assets.list(game.project_id, category="video")["entries"][0]
        for _ in range(200):
            poster = assets.thumbnail(game.project_id, entry["id"])
            if isinstance(poster, Path):
                break
            time.sleep(0.02)
        assert isinstance(poster, Path), poster
        assert poster.read_bytes().startswith(b"\xff\xd8")
        assets.preview(game.project_id, entry["id"])
        for _ in range(300):
            status = assets.preview_status(game.project_id, entry["id"])
            if status["state"] != "running":
                break
            time.sleep(0.02)
        assert status["state"] == "completed", status
        assert assets.preview_file(game.project_id, entry["id"]).is_file()
        assert not list((lib.data / "tmp").glob("asset-input-*"))
        assert not list((p / ".vn-stream/assets").glob("snapshot-*/files"))
    finally:
        assets.close()


def test_reindex_same_path_different_original_preserves_retained_copy(tmp_path):
    import os

    lib, game, p = environment(tmp_path)
    (p / "game/unique.txt").write_bytes(b"new original")
    assets = Assets(lib)
    root = assets._root(game.project_id) / "snapshot-conflict"
    (root / "files").mkdir(parents=True)
    (root / "files/unique.txt").write_bytes(b"only old copy")
    uid = hashlib.sha256(b"unique.txt").hexdigest()[:32]
    entry = dict(
        id=uid,
        name="unique.txt",
        path="unique.txt",
        kind="script",
        mime="text/plain",
        size_bytes=13,
        directory=False,
    )
    atomic_json(root / "index.json", [entry])
    atomic_json(
        root.parent / "current.json",
        dict(generation=root.name, counts={}, size_bytes=13, updated_at=1),
    )
    try:
        assert wait(assets, assets.migrate(game.project_id, "conflict"))["state"] == "completed"
        assert (
            wait(assets, assets.extract(game.project_id, "conflict-index"))["state"] == "completed"
        )
        os.utime(root, (1, 1))
        assets._cleanup(root.parent, assets.summary(game.project_id)["generation"])
        assert (root / "files/unique.txt").read_bytes() == b"only old copy"
        assert assets.file(game.project_id, uid).read_bytes() == b"only old copy"
        assert (p / "game/unique.txt").read_bytes() == b"new original"
    finally:
        assets.close()


def test_replacement_after_archive_indexing_is_rejected(tmp_path, monkeypatch):
    from vn_stream.assets.sources import Source

    lib, game, p = environment(tmp_path)
    archive = p / "game/a.rpa"

    def write(data, offset):
        index = pickle.dumps({"member.bin": [(offset, 3)]}, 2)
        archive.write_bytes(
            f"RPA-3.0 {34 + len(data):016x} 00000000\n".encode() + data + zlib.compress(index)
        )

    write(b"abc", 34)
    describe = Source.describe
    replaced = False

    def replace(path, relative, chunks=None):
        nonlocal replaced
        if chunks and not replaced:
            replaced = True
            write(b"SECRETabc", 40)
        return describe(path, relative, chunks)

    monkeypatch.setattr(Source, "describe", replace)
    assets = Assets(lib)
    try:
        assert wait(assets, assets.extract(game.project_id, "replaced"))["state"] == "failed"
    finally:
        assets.close()


def test_preview_start_does_not_recreate_removed_project(tmp_path, monkeypatch):
    import shutil

    from vn_stream.assets.sources import Source

    lib, game, p = environment(tmp_path)
    (p / "game/voice.mp3").write_bytes(b"ID3fixture")
    assets = Assets(lib)
    try:
        assert (
            wait(assets, assets.extract(game.project_id, "before-remove"))["state"] == "completed"
        )
        uid = assets.list(game.project_id, category="audio")["entries"][0]["id"]
        validate = Source.validate
        calls = 0

        def remove(source):
            nonlocal calls
            result = validate(source)
            calls += 1
            if calls == 2:
                shutil.rmtree(p)
            return result

        monkeypatch.setattr(Source, "validate", remove)
        with pytest.raises((ValueError, OSError, KeyError)):
            assets.preview(game.project_id, uid)
        assert not p.exists()
    finally:
        assets.close()


def test_image_thumbnail_rejects_changed_source_before_publication(tmp_path, monkeypatch):
    from PIL import Image

    lib, game, p = environment(tmp_path)
    image = p / "game/picture.png"
    Image.new("RGB", (32, 32), "blue").save(image)
    assets = Assets(lib)
    try:
        assert wait(assets, assets.extract(game.project_id, "image"))["state"] == "completed"
        uid = assets.list(game.project_id, category="image")["entries"][0]["id"]
        save = Image.Image.save

        def changed(self, fp, *args, **kwargs):
            save(self, fp, *args, **kwargs)
            image.write_bytes(b"changed")

        monkeypatch.setattr(Image.Image, "save", changed)
        with pytest.raises(ValueError):
            assets.thumbnail(game.project_id, uid)
    finally:
        assets.close()
