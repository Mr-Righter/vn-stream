"""Round-trip only self-authored saves through every installed engine."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.compatibility.test_runtime_smoke import display  # noqa: F401
from vn_stream.projects.catalog import ProjectCatalog
from vn_stream.saves.operations import Saves
from vn_stream.saves.renpy import pickle_codec as save_codec
from vn_stream.sessions.actions import ActionQueue

pytestmark = pytest.mark.skipif(
    os.environ.get("VN_STREAM_RUNTIME_SMOKE") != "1", reason="opt-in real SDK/X11 matrix"
)


@pytest.mark.parametrize(
    "version", ["7.5.3", "7.8.2", "8.0.3", "8.1.3", "8.2.3", "8.3.2", "8.3.7", "8.4.1", "8.5.3"]
)
@pytest.mark.parametrize("foreign", [False, True])
def test_static_edits_load_with_original_engine(tmp_path, display, version, foreign):  # noqa: F811
    sdk = Path(os.environ.get("VN_STREAM_RUNTIME_ROOT", "var/runtimes")).resolve() / version
    p = tmp_path / "project"
    (p / "game").mkdir(parents=True)
    (p / "game/script.rpy").write_text(
        """define config.name = "Save Editor Fixture"
define config.version = "1.0"
define config.save_directory = "save-editor-fixture"
define config.main_menu = False
default money = 5
default flag = False
default player_name = "Original"
default big_number = 36893488147419103232
default ratio = 1.25
default persistent.customflag = False
init python:
    import os, json
label start:
    if os.path.exists(os.path.join(config.basedir, "load")):
        $ renpy.load("slot")
    $ money = 10
    $ renpy.pause(0.02)
    $ money = 20
    $ renpy.save("slot")
    $ renpy.save_persistent()
    $ renpy.quit()
label after_load:
    $ output = dict(money=money, flag=flag, player_name=player_name, global_flag=persistent.customflag, big_number=big_number, ratio=ratio)
    $ open(os.path.join(config.basedir, "result.json"), "w").write(json.dumps(output))
    $ renpy.quit()
""",
        encoding="utf-8",
    )
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    game = library.register(p, "fixture")
    home = p / ".vn-stream/home"
    home.mkdir(parents=True)
    env = {
        **display[1],
        "HOME": str(home),
        "SDL_AUDIODRIVER": "dummy",
        "LIBGL_ALWAYS_SOFTWARE": "1",
        **ActionQueue(tmp_path / "data", "save-editor", tmp_path / "media").environment(),
    }
    command = ["sh", str(sdk / "renpy.sh"), "--savedir", str(p / "game/saves"), str(p), "run"]
    first = subprocess.run(command, env=env, capture_output=True, timeout=45)
    assert first.returncode == 0, first.stderr[-1500:]

    class Idle:
        def status(self):
            return {"state": "idle", "project_id": None}

    saves = Saves(library, Idle())
    items = saves.list(game.project_id)["saves"]
    slot = next(i for i in items if i["kind"] == "manual")
    if foreign:
        # Simulate a second device without executing or importing its state.
        token = home / ".renpy/tokens/security_keys.txt"
        if token.exists():
            token.rename(token.with_name("fixture-foreign-key.txt"))
        for item in items:
            revision = saves.variables(game.project_id, item["id"])["revision"]
            raw = saves.read(game.project_id, item["id"])
            saves.upload(game.project_id, item["name"], raw, revision)
            assert saves._trusted(
                game.project_id,
                saves.read(game.project_id, item["id"]),
                item["name"] == "persistent",
            )
    view = saves.variables(game.project_id, slot["id"])
    assert view["editable"], view
    changes = []
    for f in view["fields"]:
        values = {
            "store.money": "999",
            "store.flag": True,
            "store.player_name": "中文 edited",
            "store.big_number": "123456789012345678901234567890",
            "store.ratio": "2.75",
        }
        if f["name"] in values:
            changes.append(dict(f, value=values[f["name"]]))
    assert len(changes) == 5
    original_log = save_codec.unpack((p / "game/saves/slot-LT1.save").read_bytes())[0]
    original_root = save_codec.parse(original_log)[0]
    history = save_codec.mapping(original_root.items[1].state)["log"]
    history_count = len(history.items)
    saves.update(game.project_id, slot["id"], view["revision"], changes)
    updated = saves.variables(game.project_id, slot["id"])
    money = next(f for f in updated["fields"] if f["name"] == "store.money")
    saves.update(game.project_id, slot["id"], updated["revision"], [dict(money, value="1001")])
    if foreign:
        revision = saves.variables(game.project_id, slot["id"])["revision"]
        backup = saves.backup(game.project_id, slot["id"], revision, "fixture-manual")
        saves.update(game.project_id, slot["id"], revision, [dict(money, value="3")])
        revision = saves.variables(game.project_id, slot["id"])["revision"]
        saves.restore(game.project_id, slot["id"], backup["id"], revision)
    modified_log = save_codec.unpack((p / "game/saves/slot-LT1.save").read_bytes())[0]
    modified_root = save_codec.parse(modified_log)[0]
    state = save_codec.mapping(modified_root.items[1].state)
    assert len(state["log"].items) == history_count
    assert json.loads(state["_vn_stream_edits"])["store.money"] == 1001
    global_view = saves.variables(game.project_id, "persistent")
    custom = next(f for f in global_view["fields"] if f["name"] == "persistent.customflag")
    saves.update(game.project_id, "persistent", global_view["revision"], [dict(custom, value=True)])
    (p / "load").touch()
    loaded = subprocess.run(command, env=env, capture_output=True, timeout=45)
    assert loaded.returncode == 0, loaded.stderr[-1500:]
    assert json.loads((p / "result.json").read_text()) == dict(
        money=1001,
        flag=True,
        player_name="中文 edited",
        global_flag=True,
        big_number=123456789012345678901234567890,
        ratio=2.75,
    ), loaded.stdout.decode(errors="replace")
