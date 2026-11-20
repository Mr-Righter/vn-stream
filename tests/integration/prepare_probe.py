"""Prepare isolated, self-authored media fixtures; never overwrite an existing game."""

import argparse
import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path


def prepare(root: Path, runtimes: Path):
    root = root.resolve()
    runtimes = runtimes.resolve()
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "inbox").mkdir(exist_ok=True)
    probe = root / "inbox/Stream-Probe"
    if not probe.exists():
        if not shutil.which("ffmpeg"):
            raise ValueError("准备媒体样本需要 ffmpeg")
        sdk = runtimes / "8.5.3/renpy/vc_version.py"
        if not sdk.is_file():
            raise ValueError("请先安装 8.5.3 SDK")
        shutil.copytree(Path(__file__).parents[1] / "fixtures/runtime-smoke", probe)
        (probe / "renpy").mkdir()
        shutil.copyfile(sdk, probe / "renpy/vc_version.py")
        game = probe / "game"
        with wave.open(str(game / "tone.wav"), "wb") as audio:
            audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            frames = bytearray()
            for index in range(48000):
                sample = int(9000 * math.sin(2 * math.pi * 440 * index / 48000))
                frames.extend(struct.pack("<hh", sample, sample))
            audio.writeframes(frames)
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=640x360:rate=24",
                "-t",
                "4",
                "-c:v",
                "libvpx-vp9",
                "-deadline",
                "realtime",
                "-cpu-used",
                "6",
                "-crf",
                "35",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(game / "probe.webm"),
            ],
            check=True,
        )
        script = (game / "script.rpy").read_text()
        script = script.replace(
            '"version": renpy.version_only,',
            '"version": renpy.version_only,\n            "uid": os.getuid(),',
        )
        script = script.replace("label start:\n", 'label start:\n    play music "tone.wav" loop\n')
        script = script.replace(
            '    on "show" action Function(smoke_event, "ready")',
            '    key "K_v" action Show("media_video")\n    on "show" action Function(smoke_event, "ready")',
        )
        (game / "script.rpy").write_text(script)
        (game / "media_probe.rpy").write_text("""screen media_video():
    modal True
    add Solid("#000000")
    add Movie(play="probe.webm", loop=True) size (640, 360)
    text "Video probe - Escape closes" xpos 15 ypos 10 color "#ffffff"
    key "K_ESCAPE" action Hide("media_video")
""")
    quit_probe = root / "inbox/Stream-Quit-Probe"
    if not quit_probe.exists():
        shutil.copytree(
            probe,
            quit_probe,
            ignore=shutil.ignore_patterns(
                ".vn-stream", "saves", "cache", "*.rpyc", "*.log", "events.jsonl"
            ),
        )
        script = quit_probe / "game/script.rpy"
        text = script.read_text().replace(
            "screen main_menu():",
            'screen main_menu():\n    textbutton "Quit (Q)" xpos 320 ypos 100 action Quit(confirm=False)\n    key "K_q" action Quit(confirm=False)',
        )
        script.write_text(text)
    override = {
        "services": {
            "vn-stream": {
                "volumes": [{"type": "bind", "source": str(runtimes), "target": "/data/runtimes"}]
            }
        }
    }
    (root / "compose.override.json").write_text(json.dumps(override, indent=2))
    return probe


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="准备独立容器验收样本")
    parser.add_argument("--root", type=Path, default=Path("var/container-verify"))
    parser.add_argument("--runtimes", type=Path, default=Path("var/runtimes"))
    args = parser.parse_args()
    print(prepare(args.root, args.runtimes))
