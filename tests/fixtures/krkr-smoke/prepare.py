"""Prepare an independent native probe with a self-generated audio tone."""

import argparse
import math
import shutil
import struct
import wave
from pathlib import Path


def prepare(target):
    target.mkdir(parents=True, exist_ok=False)
    (target / "savedata").mkdir()
    shutil.copy2(Path(__file__).with_name("startup.tjs"), target / "startup.tjs")
    with wave.open(str(target / "tone.wav"), "wb") as output:
        output.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
        output.writeframes(
            b"".join(
                struct.pack("<h", int(3000 * math.sin(i * 2 * math.pi * 440 / 22050)))
                for i in range(22050)
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    prepare(parser.parse_args().target)
