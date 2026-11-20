#!/usr/bin/env python3
"""Package the verified Next host and ABI together; never replace an install."""

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from vn_stream.engines.directories import engine_directory
from vn_stream.engines.registry import ADAPTERS

REQUIRED = {
    "runner",
    "lib/libengine_api.so",
    "LICENSE",
    "LICENSE-components.txt",
    "THIRD_PARTY-NOTICES.txt",
    "README.txt",
    "source.patch",
    "build.json",
}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024**2):
            value.update(block)
    return value.hexdigest()


def publish(files, output, metadata):
    if set(files) != REQUIRED:
        raise ValueError("Next package has missing or unexpected files")
    if any(path.is_symlink() or not path.is_file() for path in files.values()):
        raise ValueError("Next package requires regular artifact files")
    if sum(path.stat().st_size for path in files.values()) > 512 * 1024**2:
        raise ValueError("Next package exceeds its size limit")
    hashes = {name: digest(path) for name, path in sorted(files.items())}
    identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
    commit = metadata["source_commit"]
    name = f"krkr-next-{commit[:12]}-{identity}"
    output = engine_directory(output, "krkr", ADAPTERS)
    output.mkdir(parents=True, exist_ok=True)
    target = output / name
    with tempfile.TemporaryDirectory(dir=output, prefix=".native-build-") as temporary:
        pending = Path(temporary) / name
        pending.mkdir()
        for relative, path in files.items():
            destination = pending / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            destination.chmod(0o755 if relative == "runner" else 0o644)
            if digest(destination) != hashes[relative]:
                raise ValueError("Next artifact changed during publication")
        record = dict(
            schema=1,
            id=f"krkr:next:{commit[:12]}:{identity}",
            engine_id="krkr",
            flavor="krkr-next",
            display_name="KrKr2-Next",
            version=metadata["version"],
            executable="runner",
            validation="experimental",
            platform="linux-x86_64",
            source=metadata["source"],
            source_commit=commit,
            build_image=metadata["build_image"],
            binary_sha256=hashes["runner"],
            resource_files={key: value for key, value in hashes.items() if key != "runner"},
        )
        (pending / "runtime.json").write_text(json.dumps(record, indent=2) + "\n")
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
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
            raise OSError(code, os.strerror(code), str(target))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--vcpkg-installed", type=Path, required=True)
    parser.add_argument("--components", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "containers/native-candidates.lock.json").read_text())
    spec = lock["sources"]["krkr-next"]
    artifact = lock["artifacts_built"]["krkr-next-linux-host"]
    actual = subprocess.check_output(
        ["git", "-C", str(args.source), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != spec["commit"]:
        raise ValueError("Next source commit differs from the lock")
    for relative, expected in spec["linux_host_inputs"].items():
        if digest(root / relative) != expected:
            raise ValueError("Next build input differs from the lock: " + relative)
    for relative, expected in spec["plugin_source"]["files"].items():
        if digest(args.components / relative) != expected:
            raise ValueError("Next component differs from the lock: " + relative)
    binary = args.build / "vn-krkr-next"
    library = args.build / "upstream/bridge/engine_api/libengine_api.so"
    if digest(binary) != artifact["sha256"] or digest(library) != artifact["engine_api_sha256"]:
        raise ValueError("Next binaries differ from the verified artifacts")
    with tempfile.TemporaryDirectory(prefix="vn-next-notices-") as temporary:
        material = Path(temporary)
        patches = b"".join((root / name).read_bytes() for name in spec["linux_patch_order"])
        (material / "source.patch").write_bytes(patches)
        (material / "build.json").write_text(json.dumps(spec, indent=2) + "\n")
        notices = sorted((args.vcpkg_installed / "x64-linux/share").glob("*/copyright"))
        if len(notices) < 20:
            raise ValueError("Dependency license inventory is incomplete")
        (material / "THIRD_PARTY-NOTICES.txt").write_bytes(
            b"\n\n".join(path.parent.name.encode() + b"\n" + path.read_bytes() for path in notices)
        )
        files = {
            "runner": binary,
            "lib/libengine_api.so": library,
            "LICENSE": args.source / "LICENSE",
            "LICENSE-components.txt": args.components / "LICENSE",
            "README.txt": root / "containers/native-build/krkr-next/README.txt",
            **{
                name: material / name
                for name in ("source.patch", "build.json", "THIRD_PARTY-NOTICES.txt")
            },
        }
        print(
            publish(
                files,
                args.output.resolve(),
                dict(
                    source_commit=spec["commit"],
                    source=spec["repository"],
                    version=spec["commit"][:12] + "+linux-modal",
                    build_image=spec["linux_builder_image"],
                ),
            )
        )


if __name__ == "__main__":
    main()
