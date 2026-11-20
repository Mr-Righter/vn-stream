#!/usr/bin/env python3
"""Build pinned native runtimes in Docker; publish experimental installs atomically."""

import argparse
import ctypes
import errno
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from vn_stream.engines.directories import engine_directory
from vn_stream.engines.registry import ADAPTERS

SPECS = {
    "new": dict(
        engine_id="ons",
        repo="https://github.com/timftw21/ONScripter-NEW.git",
        commit="8b11e69172e09ea1818e0cda37dd68a003b56644",
        version="8b11e69172e0",
        display_name="ONScripter-NEW",
        executable="DerivedData/Posix-x86_64/onscripter-new",
        license="LICENSE",
    ),
    "yuri": dict(
        engine_id="ons",
        repo="https://github.com/YuriSizuku/OnscripterYuri.git",
        commit="46c07d2ece7350b0543428962cc9df58eb5496d5",
        version="0.7.7",
        display_name="ONScripterYuri 0.7.7",
        executable="build/onsyuri",
        license="LICENSE",
    ),
    "jh": dict(
        engine_id="ons",
        repo="https://github.com/chf2000/ONScripter-Jh.git",
        commit="45429f2fac27083b538ae1078f80a22943923e04",
        version="0.7.3",
        display_name="ONScripter-Jh 0.7.3",
        executable="onscripter",
        license="COPYING",
    ),
    "zero": dict(
        engine_id="ons",
        repo="https://github.com/zeromake/ONScripter-zero.git",
        commit="66cd50004fa79a7cca679e3a445543f5ae7c0fd1",
        version="0.9.10-development+maskfix",
        display_name="ONScripter-zero 0.9.10-dev",
        executable="build/linux/onscripter",
        license="LICENSE",
    ),
}
COMMANDS = {
    "new": "./configure --std=gnu++23 --no-lto && make -j3",
    "yuri": "cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j3",
    "jh": 'make -f Makefile.Linux onscripter CC=g++ "LD=g++ -o" '
    '"INCS=$(sdl2-config --cflags) -I/usr/include/lua5.3" '
    '"LIBS=$(sdl2-config --libs) -lSDL2_ttf -lSDL2_image -lSDL2_mixer -ljpeg -lbz2 -lm '
    '-lfontconfig -logg -lvorbis -lvorbisfile -llua5.3" -j3',
    "zero": "cmake -S /native-build/zero -B build/linux -DCMAKE_BUILD_TYPE=Release "
    "-DONS_SOURCE=/build/zero && cmake --build build/linux -j3",
}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def checkout(directory, repo, commit, *, patch=None):
    if not directory.exists():
        run("git", "init", str(directory))
        run("git", "-C", str(directory), "remote", "add", "origin", repo)
        run("git", "-C", str(directory), "fetch", "--depth=1", "origin", commit)
        run("git", "-C", str(directory), "checkout", "--detach", "FETCH_HEAD")
    actual = subprocess.check_output(
        ["git", "-C", str(directory), "rev-parse", "HEAD"], text=True
    ).strip()
    diff = subprocess.check_output(["git", "-C", str(directory), "diff", "HEAD"])
    allowed = patch.read_bytes() if patch else b""
    if actual != commit or (diff and diff != allowed):
        raise ValueError("Build checkout differs from the pinned source; use a new work directory")
    untracked = subprocess.check_output(
        ["git", "-C", str(directory), "ls-files", "--others", "--exclude-standard", "-z"]
    )
    if untracked:
        raise ValueError(
            "Build checkout contains unexpected untracked inputs; use a clean directory"
        )
    if patch and not diff:
        run("git", "-C", str(directory), "apply", str(patch.resolve()))


def publish(source, output, flavor, *, build_image=None):
    spec = SPECS[flavor]
    binary = source / spec["executable"]
    if not binary.is_file() or binary.is_symlink():
        raise ValueError("Expected runtime binary was not produced")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    identity = digest[:12]
    key = f"{spec['engine_id']}-{flavor}-{spec['commit'][:12]}-{identity}"
    output = engine_directory(output, spec["engine_id"], ADAPTERS)
    target = output / key
    if target.exists():
        raise ValueError("Runtime already exists; an installation is never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output, prefix=".native-build-") as temporary:
        pending = Path(temporary) / key
        pending.mkdir()
        shutil.copy2(binary, pending / "runner")
        (pending / "runner").chmod(0o755)
        shutil.copy2(source / spec["license"], pending / "LICENSE")
        record = dict(
            schema=1,
            id=f"{spec['engine_id']}:{flavor}:{spec['commit'][:12]}:{identity}",
            engine_id=spec["engine_id"],
            flavor=flavor,
            display_name=spec.get("display_name", flavor),
            version=spec["version"],
            executable="runner",
            validation="experimental",
            source=spec["repo"],
            source_commit=spec["commit"],
            binary_sha256=digest,
            build_command=COMMANDS[flavor],
            build_image=build_image,
            platform="linux-x86_64",
        )
        if flavor == "new":
            notices = pending / "THIRD_PARTY"
            notices.mkdir()
            for name in ("LICENSE-BSD", "LICENSE-GPLv2"):
                shutil.copy2(source / name, notices / name)
            deps = source / "DerivedData/Posix-x86_64/Dependencies/src"
            for dependency in sorted(deps.iterdir()) if deps.is_dir() else []:
                for notice in dependency.iterdir() if dependency.is_dir() else []:
                    if notice.is_file() and notice.name.upper().startswith(
                        ("LICENSE", "COPYING", "NOTICE")
                    ):
                        shutil.copy2(notice, notices / (dependency.name + "-" + notice.name))
            digest_notices = hashlib.sha256()
            for notice in sorted(notices.iterdir()):
                digest_notices.update(notice.name.encode())
                digest_notices.update(notice.read_bytes())
            record["license_bundle_sha256"] = digest_notices.hexdigest()
        if flavor == "zero":
            overlay = Path(__file__).resolve().parents[1] / "containers/native-build/zero"
            relative = "containers/native-build/zero/CMakeLists.txt"
            record["build_inputs"] = {
                relative: hashlib.sha256((overlay / "CMakeLists.txt").read_bytes()).hexdigest()
            }
            patch = (
                Path(__file__).resolve().parents[1]
                / "containers/native-patches/ons/zero-mask-alignment.patch"
            )
            record["build_patch_sha256"] = hashlib.sha256(patch.read_bytes()).hexdigest()
            shutil.copy2(patch, pending / "source.patch")
            shutil.copytree(overlay / "licenses", pending / "THIRD_PARTY")
        serialized = json.dumps(record, indent=2) + "\n"
        if len(serialized.encode()) > 16384:
            raise ValueError("Native runtime manifest exceeds the supported size")
        (pending / "runtime.json").write_text(serialized)
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "Atomic runtime publication requires renameat2")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(pending), -100, os.fsencode(target), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("flavor", choices=SPECS)
    parser.add_argument("--work", type=Path, default=Path("var/runtime-builds/native"))
    parser.add_argument("--output", type=Path, default=Path("var/runtimes"))
    parser.add_argument("--builder-image", default="vn-stream:native-builder")
    parser.add_argument("--use-existing-builder", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("Only native Linux x86_64 is currently validated")
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    spec = SPECS[args.flavor]
    root = Path(__file__).resolve().parents[1]
    patch = (
        root / "containers/native-patches/ons/zero-mask-alignment.patch"
        if args.flavor == "zero"
        else None
    )
    checkout(
        work / args.flavor,
        spec["repo"],
        spec["commit"],
        patch=patch,
    )
    if not args.use_existing_builder:
        run(
            "docker",
            "build",
            "-f",
            str(root / "containers/native-build.Dockerfile"),
            "-t",
            args.builder_image,
            str(root / "containers"),
        )
    environment = [
        item
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")
        if key in os.environ
        for item in ("-e", key)
    ]
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "--cpus",
        "3",
        *environment,
        "-v",
        str(work) + ":/build",
        "-v",
        str(root / "containers/native-build") + ":/native-build:ro",
        "-w",
        "/build/" + args.flavor,
        "--entrypoint",
        "/bin/bash",
        args.builder_image,
        "-lc",
        COMMANDS[args.flavor],
    )
    build_image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.builder_image], text=True
    ).strip()
    print(
        publish(
            work / args.flavor,
            args.output.resolve(),
            args.flavor,
            build_image=build_image,
        )
    )


if __name__ == "__main__":
    main()
