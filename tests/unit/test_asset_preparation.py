"""Preparation persists state, reuses originals and never publishes stale output."""

import time

import pytest

from tests.native_asset_fixtures import png, xp3
from tests.unit.test_asset_reuse import wait
from vn_stream.assets.operations import Assets
from vn_stream.projects.catalog import ProjectCatalog


def environment(tmp_path):
    p = tmp_path / "games/native"
    p.mkdir(parents=True)
    (p / "data.xp3").write_bytes(
        xp3([("p.png", [(png(), True)]), ("intro.amv", [(b"AJPMopaque", True)])])
    )
    library = ProjectCatalog(tmp_path / "data", [tmp_path / "games"])
    game = library.register(p, "Native")
    service = Assets(library)
    assert wait(service, service.extract(game.project_id, "index"))["state"] == "completed"
    entry = service.list(game.project_id, search="p.png")["entries"][0]
    return service, game, entry, p


def prepared(service, game, entry, representation="original"):
    result = service.prepare(game.project_id, entry["id"], representation)
    for _ in range(1000):
        result = service.prepare_status(game.project_id, entry["id"], representation)
        if result["state"] in ("completed", "failed"):
            return result
        time.sleep(0.005)
    pytest.fail("preparation timeout")


def test_compressed_preparation_is_deduplicated_and_survives_service_restart(tmp_path):
    service, game, entry, p = environment(tmp_path)
    try:
        assert prepared(service, game, entry)["state"] == "completed"
        one = service.prepare(game.project_id, entry["id"], "original")
        two = service.prepare(game.project_id, entry["id"], "original")
        assert one["task_id"] == two["task_id"]
        assert service.prepared_file(game.project_id, entry["id"], "original").read_bytes() == png()
        assert not (
            p / ".vn-stream/assets" / service.summary(game.project_id)["generation"] / "files"
        ).exists()
        library = service.library
        service.close()
        service = Assets(library)
        assert (
            service.prepare_status(game.project_id, entry["id"], "original")["state"] == "completed"
        )
        assert service.prepared_file(game.project_id, entry["id"], "original").read_bytes() == png()
    finally:
        service.close()


def test_changed_source_is_rejected_even_with_prepared_cache(tmp_path):
    service, game, entry, p = environment(tmp_path)
    try:
        assert prepared(service, game, entry)["state"] == "completed"
        (p / "data.xp3").write_bytes(b"replacement")
        with pytest.raises(ValueError):
            service.prepared_file(game.project_id, entry["id"], "original")
    finally:
        service.close()


def test_unsupported_video_download_remains_available(tmp_path):
    service, game, _, _ = environment(tmp_path)
    try:
        entry = service.list(game.project_id, search="intro.amv")["entries"][0]
        assert not entry["preview_supported"]
        result = prepared(service, game, entry, "preview")
        assert result["state"] == "failed"
        assert prepared(service, game, entry)["state"] == "completed"
        assert (
            service.prepared_file(game.project_id, entry["id"], "original").read_bytes()
            == b"AJPMopaque"
        )
    finally:
        service.close()


def test_cache_eviction_protects_open_readers(tmp_path):
    from vn_stream.assets.decode_cache import DecodeCache

    cache = DecodeCache(tmp_path / "cache", limit=100)
    first = cache.folder / "one"
    first.write_bytes(b"a" * 80)
    cache.pin("one")
    second = cache.folder / "two"
    second.write_bytes(b"b" * 80)
    cache.prune()
    assert first.exists()
    assert not second.exists()
    cache.unpin("one")
    cache.limit = 0
    cache.prune()
    assert not first.exists()


def test_authenticated_prepared_range_and_unsupported_capabilities(tmp_path):
    from tests.auth_helpers import TestClient, sign_in
    from vn_stream.api.app import create_app

    root = tmp_path / "games/native"
    root.mkdir(parents=True)
    (root / "data.xp3").write_bytes(xp3([("p.png", [(png(), True)])]))
    app = create_app(tmp_path / "data", [tmp_path / "games"], password="secret")
    game = app.state.services.catalog.register(root, "Native")
    with TestClient(app) as client:
        sign_in(client, "secret")
        service = app.state.assets
        assert wait(service, service.extract(game.project_id, "index"))["state"] == "completed"
        entry = service.list(game.project_id, search="p.png")["entries"][0]
        prefix = f"/api/v2/projects/{game.project_id}/assets/{entry['id']}"
        response = client.post(prefix + "/prepare", json={"representation": "original"})
        assert response.status_code == 202
        result = prepared(service, game, entry)
        response = client.get(result["url"], headers={"Range": "bytes=8-23"})
        assert response.status_code == 206
        assert response.content == png()[8:24]
        assert client.get(f"/api/v2/projects/{game.project_id}/saves").status_code == 409
        client.cookies.clear()
        assert client.get(result["url"]).status_code == 401


def test_oversized_preparation_is_temporary_and_later_range_still_works(tmp_path):
    service, game, entry, _ = environment(tmp_path)
    service.preparations.cache.limit = 1
    try:
        result = prepared(service, game, entry)
        assert result["state"] == "completed"
        key = result["task_id"]
        assert not (service.preparations.cache.folder / key).exists()
        source = service.prepared_file(game.project_id, entry["id"])
        with source.open() as stream:
            assert stream.read() == png()
        assert not (service.preparations.temporary.folder / key).exists()
        with service.prepared_file(game.project_id, entry["id"]).open() as stream:
            stream.seek(8)
            assert stream.read(16) == png()[8:24]
    finally:
        service.close()


def test_durable_running_preparation_is_resumed_on_restart(tmp_path):
    service, game, entry, _ = environment(tmp_path)
    key, root, _, _ = service.preparations._context(game.project_id, entry["id"], "original")
    record = dict(
        task_id=key,
        project_id=game.project_id,
        asset_id=entry["id"],
        generation=root.name,
        representation="original",
        state="running",
        progress_percent=12,
    )
    service.preparations._store(record)
    library = service.library
    service.close()
    service = Assets(library)
    try:
        for _ in range(1000):
            state = service.prepare_status(game.project_id, entry["id"])
            if state["state"] == "completed":
                break
            time.sleep(0.005)
        assert state["state"] == "completed"
        assert service.prepared_file(game.project_id, entry["id"]).read_bytes() == png()
    finally:
        service.close()


def test_deleted_project_cannot_receive_late_preparation(tmp_path, monkeypatch):
    import shutil
    import threading

    service, game, entry, root = environment(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = service.preparations._valid

    def paused(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(service.preparations, "_valid", paused)
    try:
        result = service.prepare(game.project_id, entry["id"])
        assert entered.wait(3)
        shutil.rmtree(root)
        release.set()
        for _ in range(1000):
            state = service.preparations._record(result["task_id"])
            if state["state"] == "failed":
                break
            time.sleep(0.005)
        assert state["state"] == "failed"
        assert not root.exists()
        assert not (service.preparations.cache.folder / result["task_id"]).exists()
    finally:
        release.set()
        service.close()


def test_recovery_cleans_stale_outputs_before_resuming_workers(tmp_path, monkeypatch):
    service, game, entry, _ = environment(tmp_path)
    prep = service.preparations
    key, root, _, _ = prep._context(game.project_id, entry["id"], "original")
    prep._store(
        dict(
            task_id=key,
            project_id=game.project_id,
            asset_id=entry["id"],
            generation=root.name,
            representation="original",
            state="running",
        )
    )
    stale = prep.cache.folder / "stale.tmp"
    stale.write_bytes(b"stale")
    observed = []

    def resume(*args):
        observed.append(stale.exists())
        return {"state": "running"}

    monkeypatch.setattr(prep, "request", resume)
    try:
        prep.recover()
        assert observed == [False]
    finally:
        service.close()


def test_cached_lookup_reserves_file_until_reader_is_open(tmp_path):
    from vn_stream.assets.decode_cache import DecodeCache
    from vn_stream.assets.sources import Source

    original = tmp_path / "original"
    original.write_bytes(b"original")
    cache = DecodeCache(tmp_path / "cache", limit=5)
    (cache.folder / "prepared").write_bytes(b"cache")
    source = cache.source("prepared", Source(tmp_path, Source.describe(original, "original")))
    cache.limit = 0
    cache.prune()
    with source.open() as stream:
        assert stream.read() == b"cache"
    source.close()
    assert not (cache.folder / "prepared").exists()


def test_releasing_last_reader_reapplies_cache_budget(tmp_path):
    from vn_stream.assets.decode_cache import DecodeCache

    cache = DecodeCache(tmp_path / "cache", limit=5)
    for key in ("one", "two"):
        (cache.folder / key).write_bytes(b"cache")
        cache.pin(key)
    cache.prune()
    cache.unpin("one")
    cache.unpin("two")
    assert sum(p.stat().st_size for p in cache.folder.iterdir()) <= cache.limit


def test_abandoned_temporary_outputs_are_bounded_and_expire(tmp_path):
    from vn_stream.assets.decode_cache import DecodeCache

    cache = DecodeCache(tmp_path / "cache", limit=100, max_files=1, max_age=60)
    for key in ("one", "two"):
        (cache.folder / key).write_bytes(b"cache")
    cache.prune()
    assert len(list(cache.folder.iterdir())) == 1
    cache.prune(now=time.time() + 61)
    assert not list(cache.folder.iterdir())


def test_thumbnail_remains_valid_after_oversized_input_is_released(tmp_path):
    service, game, entry, _ = environment(tmp_path)
    service.preparations.cache.limit = 1
    try:
        assert prepared(service, game, entry, "preview")["state"] == "completed"
        result = service.thumbnail(game.project_id, entry["id"])
        assert result.is_file()
    finally:
        service.close()
