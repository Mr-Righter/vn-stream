"""Detail queries never execute game state or limit navigation to one page."""

import hashlib
import threading
import time

import pytest

from tests.unit.test_content_library import Sessions, sample, settle, setup  # noqa: F401
from vn_stream.assets.operations import Assets
from vn_stream.saves.operations import Saves
from vn_stream.storage.files import atomic_json


def test_asset_details_filter_and_cross_page_neighbors(setup):  # noqa: F811
    library, game, _ = setup
    service = Assets(library)
    root = service._root(game.project_id)
    entries = [
        dict(
            id=f"{i:032x}",
            name=f"{i:03}.png",
            path=f"gui/{i:03}.png",
            kind="image",
            mime="image/png",
            size_bytes=12,
            directory=False,
        )
        for i in range(105)
    ]
    entries.append(dict(entries[0], id="f" * 32, name="else.png", path="else.png"))
    atomic_json(root / "snapshot-fixture/index.json", entries)
    atomic_json(
        root / "current.json",
        dict(generation="snapshot-fixture", counts={}, size_bytes=0, updated_at=0),
    )
    view = service.detail(game.project_id, entries[99]["id"], folder="gui", category="image")
    assert (view["position"], view["total"]) == (100, 105)
    assert view["previous_id"] == entries[98]["id"]
    assert view["next_id"] == entries[100]["id"]
    assert service.detail(game.project_id, entries[0]["id"], folder="gui")["previous_id"] is None
    with pytest.raises(KeyError):
        service.detail(game.project_id, entries[0]["id"], folder="gui", search="no-match")
    service.close()


def test_save_summary_does_not_parse_variables_and_keeps_deleted_backups(setup, monkeypatch):  # noqa: F811
    library, game, project = setup
    target = project / "game/saves/1-1-LT1.save"
    target.write_bytes(sample())
    service = Saves(library, Sessions())
    item = service.list(game.project_id)["saves"][0]
    revision = service.variables(game.project_id, item["id"])["revision"]
    monkeypatch.setattr(
        service, "variables", lambda *_: pytest.fail("Summary must not parse pickle")
    )
    assert service.detail(game.project_id, item["id"])["name"] == target.name
    service.delete(game.project_id, item["id"], revision)
    assert service.detail(game.project_id, item["id"])["archived"] is True


def test_video_thumbnail_deduplicates_and_does_not_recreate_deleted_project(setup, monkeypatch):  # noqa: F811
    library, game, project = setup
    (project / "game/clip.webm").write_bytes(b"fixture")
    service = Assets(library)
    settle(service, service.extract(game.project_id, "thumb-fixture")["id"])
    uid = hashlib.sha256(b"clip.webm").hexdigest()[:32]
    started, release = threading.Event(), threading.Event()
    calls = []

    def render(source, output):
        calls.append(source)
        started.set()
        assert release.wait(5)
        output.write_bytes(b"jpeg-fixture")

    monkeypatch.setattr(service.thumbnails, "render", render)
    assert service.thumbnail(game.project_id, uid)["state"] in ("queued", "running")
    assert started.wait(2)
    service.thumbnail(game.project_id, uid)
    assert len(calls) == 1
    import shutil

    shutil.rmtree(project)
    release.set()
    time.sleep(0.1)
    service.close()
    assert not project.exists()


@pytest.mark.parametrize("duration,expected", [("0.4", "0.2"), ("N/A", "0"), ("nan", "0")])
def test_video_poster_seek_is_bounded_even_without_duration(
    setup,  # noqa: F811
    monkeypatch,
    tmp_path,
    duration,
    expected,
):
    import json
    from types import SimpleNamespace

    from vn_stream.assets import thumbnails as thumbnails

    library, _, _ = setup
    service = Assets(library)
    monkeypatch.setattr(
        thumbnails.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps({"format": {"duration": duration}})
        ),
    )
    commands = []

    class Process:
        def __init__(self, command, **kwargs):
            commands.append(command)
            from pathlib import Path

            Path(command[-1]).write_bytes(b"poster")

        def wait(self, timeout=None):
            assert timeout <= 15
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(thumbnails.subprocess, "Popen", Process)
    service.thumbnails.render(tmp_path / "input.webm", tmp_path / "output.jpg")
    assert commands[0][commands[0].index("-ss") + 1] == expected
    service.close()
