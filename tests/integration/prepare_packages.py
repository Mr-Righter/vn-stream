"""Compile only the authored probe and publish fresh package fixtures, never user games."""

import argparse
import hashlib
import json
import os
import pickle
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
import zlib
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from prepare_probe import prepare


def rpa(files: dict[str, bytes]) -> bytes:
    # Serialize our own index only; this generator never unpickles game data.
    offset = 34
    index = {}
    for name, content in files.items():
        index[name] = [(offset, len(content))]
        offset += len(content)
    header = f"RPA-3.0 {offset:016x} 00000000\n".encode("ascii")
    return header + b"".join(files.values()) + zlib.compress(pickle.dumps(index, protocol=2))


def prepare_packages(root: Path, runtimes: Path):
    root, runtimes = root.resolve(), runtimes.resolve()
    source = prepare(root, runtimes)
    bucket = "Stream-Packages-" + uuid4().hex[:12]
    cases = []
    # Staging is private; publish only complete packages, under a fresh directory.
    with tempfile.TemporaryDirectory(dir=root / "inbox", prefix=".packages-") as temporary:
        staging = Path(temporary)
        output = staging / "output"
        output.mkdir()
        for version in ("7.8.2", "8.5.3"):
            sdk = runtimes / version
            if not (sdk / "renpy.sh").is_file():
                raise ValueError(f"请先安装 {version} SDK")
            project = staging / version
            game = project / "game"
            game.mkdir(parents=True)
            for name in ("script.rpy", "media_probe.rpy", "tone.wav", "probe.webm"):
                shutil.copyfile(source / "game" / name, game / name)
            home = project / "compiler-home"
            home.mkdir()
            compiled = subprocess.run(
                [
                    "/bin/sh",
                    str(sdk / "renpy.sh"),
                    "--savedir",
                    str(project / "saves"),
                    str(project),
                    "compile",
                ],
                env={**os.environ, "HOME": str(home), "DISPLAY": "", "SDL_AUDIODRIVER": "dummy"},
                capture_output=True,
                text=True,
                timeout=30,
            )
            if compiled.returncode:
                raise ValueError(f"自制样本编译失败 {version}: {compiled.stderr[-2000:]}")
            files = {
                name: (game / name).read_bytes()
                for name in ("script.rpyc", "media_probe.rpyc", "tone.wav", "probe.webm")
            }
            metadata = {
                name: (sdk / name).read_bytes()
                for name in ("renpy/__init__.py", "renpy/vc_version.py")
            }
            family = "py2" if version.startswith("7.") else "py3"
            for kind in ("zip", "apk"):
                filename = f"Stream-{version}.{kind}"
                package = output / filename
                with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    if kind == "zip":
                        archive.writestr("Stream/game/archive.rpa", rpa(files))
                        archive.writestr(f"Stream/lib/{family}-windows-x86_64/", b"")
                        for name, content in metadata.items():
                            archive.writestr("Stream/" + name, content)
                    else:
                        for name, content in files.items():
                            archive.writestr("assets/x-game/x-" + name, content)
                        private = BytesIO()
                        with tarfile.open(fileobj=private, mode="w:gz") as bundle:
                            for name, content in {
                                **metadata,
                                f"lib/{family}-android-arm64/runtime-marker": b"fixture metadata only",
                            }.items():
                                member = tarfile.TarInfo(name)
                                member.size = len(content)
                                bundle.addfile(member, BytesIO(content))
                        archive.writestr("assets/private.mp3", private.getvalue())
                cases.append(
                    {
                        "version": version,
                        "kind": kind,
                        "path": f"{bucket}/{filename}",
                        "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                    }
                )
        output.rename(root / "inbox" / bucket)
    manifest = root / "packages.json"
    pending = manifest.with_suffix(".pending")
    pending.write_text(json.dumps(cases, indent=2) + "\n")
    pending.replace(manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="编译并准备四组自制归档媒体样本")
    parser.add_argument("--root", type=Path, default=Path("var/container-verify"))
    parser.add_argument("--runtimes", type=Path, default=Path("var/runtimes"))
    args = parser.parse_args()
    print(prepare_packages(args.root, args.runtimes))
