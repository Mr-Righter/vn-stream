"""Create disposable media/save fixtures for opt-in container verification."""

import argparse
import io
import json
import pickle
import subprocess
import zipfile
from pathlib import Path

from PIL import Image


def save(path, money=5):
    image = io.BytesIO()
    Image.new("RGB", (320, 180), "#2563eb").save(image, "PNG")
    with zipfile.ZipFile(path, "w") as output:
        output.writestr("log", pickle.dumps(({"store.flag": False, "store.money": money}, {}), 2))
        output.writestr("json", json.dumps({"_save_name": "Fixture"}))
        output.writestr("screenshot.png", image.getvalue())


def prepare(inbox, state):
    for title in [
        "Save-UI-Refactor",
        "Save-Polish-Refactor",
        "Content-Refactor",
        "Migration-UI-Source",
        "Migration-UI-Target",
    ]:
        game = inbox / title / "game"
        game.mkdir(parents=True, exist_ok=True)
        (game / "script.rpy").write_text("label start:\n    return\n")
        (game / "saves").mkdir(exist_ok=True)
        save(
            game / "saves" / ("1-1.save" if title.startswith("Save-UI") else "slot.save"),
            42 if title.endswith("Source") else 5,
        )
    for folder in ["save-ui-work", "save-polish-work", "save-migration-work"]:
        (state / folder).mkdir(parents=True, exist_ok=True)
    (state / "save-ui-work/fixture.json").write_text(json.dumps({"folder": "Save-UI-Refactor"}))
    (state / "save-polish-work/content-fixture-name").write_text("Save-Polish-Refactor")
    (state / "save-migration-work/ui-fixtures.json").write_text(
        json.dumps([{"folder": "Migration-UI-Source"}, {"folder": "Migration-UI-Target"}])
    )
    game = inbox / "Content-Refactor/game"
    (game / "large").mkdir(exist_ok=True)
    (game / "large/blob.bin").write_bytes(b"x" * (6 * 1024**2))
    Image.new("RGB", (64, 64), "#2563eb").save(game / "image.jpg")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=15",
            "-t",
            "1",
            "-c:v",
            "libvpx-vp9",
            "-threads",
            "1",
            str(game / "clip.webm"),
        ],
        check=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inbox", type=Path)
    parser.add_argument("state", type=Path)
    args = parser.parse_args()
    prepare(args.inbox, args.state)
