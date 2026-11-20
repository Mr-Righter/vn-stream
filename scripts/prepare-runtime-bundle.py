#!/usr/bin/env python3
"""Prepare a verified Linux amd64 runtime image build context."""

import argparse
import ctypes
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

from vn_stream.engines.directories import engine_directory
from vn_stream.engines.registry import ADAPTERS
from vn_stream.runtimes.install import install_runtime
from vn_stream.runtimes.releases import RELEASES

ROOT = Path(__file__).resolve().parents[1]
NATIVE_LOCK = ROOT / "containers/runtime-bundle.lock.json"
SDK_SOURCE_LOCK = ROOT / "containers/renpy-source.lock.json"
CHUNK_BYTES = 1024**2
NATIVE_LIMIT_BYTES = 512 * CHUNK_BYTES
NATIVE_ARCHIVE_NAME = "native-runtimes-linux-amd64.tar.gz"
SOURCE_ARCHIVE_NAME = "runtime-sources-linux-amd64.tar.gz"
SOURCE_LIMIT_BYTES = 2000 * CHUNK_BYTES
BUILD_INPUT_LIMIT_BYTES = 1024**2
SDK_LIBRARIES = {"linux-x86_64", "py2-linux-x86_64", "py3-linux-x86_64"}


def checksum(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def publish(stage, output):
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(output), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), output)


def trim_renpy_sdk(sdk, notices):
    notices.mkdir(parents=True)
    shutil.copyfile(sdk / "LICENSE.txt", notices / "LICENSE.txt")
    shutil.copyfile(sdk / "doc/license.html", notices / "license.html")
    for name in ("renpy.exe", "renpy-32.exe"):
        (sdk / name).unlink(missing_ok=True)
    for name in ("renpy.app", "tutorial", "the_question", "doc"):
        path = sdk / name
        if path.exists():
            shutil.rmtree(path)
    for path in (sdk / "lib").iterdir():
        if path.name in SDK_LIBRARIES or re.fullmatch(r"python[23]\.\d+", path.name):
            continue
        if path.is_dir() and re.fullmatch(r"(?:py[23]-)?(?:linux|mac|windows)-[\w-]+", path.name):
            shutil.rmtree(path)
        else:
            raise ValueError(f"Unexpected Ren'Py library entry: {path.name}")


def deduplicate(root):
    seen = {}
    saved = 0
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"Unexpected runtime file type: {path.relative_to(root)}")
        key = (stat.S_IMODE(mode), checksum(path))
        previous = seen.setdefault(key, path)
        if previous == path:
            continue
        saved += path.stat().st_size
        path.unlink()
        os.link(previous, path)
    return saved


def extract_native_archive(archive, output, files):
    pending = dict(files)
    total = 0
    with tarfile.open(archive, mode="r|gz") as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if (
                member.name not in pending
                or not member.isfile()
                or member.issparse()
                or path.is_absolute()
                or ".." in path.parts
                or path.as_posix() != member.name
            ):
                raise ValueError(f"Unexpected native archive member: {member.name}")
            total += member.size
            if member.size < 0 or total > NATIVE_LIMIT_BYTES:
                raise ValueError("Native archive exceeds its extraction limit")
            expected, mode = pending.pop(member.name)
            target = output / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=CHUNK_BYTES)
            if checksum(target) != expected:
                raise ValueError(f"Native member failed SHA-256 verification: {member.name}")
            target.chmod(mode)
    if pending:
        raise ValueError("Native archive is missing locked members: " + ", ".join(sorted(pending)))


def check_amd64_elf(path):
    with path.open("rb") as file:
        header = file.read(20)
    if header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00":
        raise ValueError(f"Expected a Linux amd64 ELF: {path}")


def download_cached(url, expected, cache, limit):
    filename = url.rsplit("/", 1)[-1]
    archive = cache / filename
    if archive.exists():
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > limit:
            raise ValueError(f"Invalid cached archive: {archive}")
        if checksum(archive) != expected:
            raise ValueError(f"Cached archive failed SHA-256 verification: {archive}")
        return archive
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix=".download-") as temporary:
        pending = Path(temporary) / filename
        total = 0
        digest = hashlib.sha256()
        deadline = time.monotonic() + 900
        with urlopen(url, timeout=30) as source, pending.open("xb") as destination:
            while chunk := source.read(CHUNK_BYTES):
                total += len(chunk)
                if total > limit or time.monotonic() > deadline:
                    raise ValueError(f"Archive exceeds its download size or time limit: {filename}")
                digest.update(chunk)
                destination.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"Archive failed SHA-256 verification: {filename}")
        os.replace(pending, archive)
    return archive


def native_archive_files(root, lock):
    for entry in lock["runtimes"]:
        runtime = entry["manifest"]
        family = engine_directory(root / "runtimes", runtime["engine_id"], ADAPTERS)
        flavor = runtime["flavor"]
        for name, digest in sorted(entry["files"].items()):
            path = (family / entry["directory"] / name).relative_to(root).as_posix()
            mode = 0o755 if name == runtime["executable"] else 0o644
            yield flavor, name, path, digest, mode


def assemble_native_archive(output, lock, inputs):
    expected = {entry["manifest"]["flavor"] for entry in lock["runtimes"]}
    if inputs.keys() != expected:
        raise ValueError(
            "Native archive requires package directories for: " + ", ".join(sorted(expected))
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".native-archive-") as temporary:
        pending = Path(temporary) / NATIVE_ARCHIVE_NAME
        with (
            pending.open("xb") as target,
            gzip.GzipFile(fileobj=target, filename="", mode="wb", mtime=0) as compressed,
            tarfile.open(fileobj=compressed, format=tarfile.USTAR_FORMAT, mode="w|") as archive,
        ):
            for flavor, name, relative, expected_digest, mode in native_archive_files(
                Path(temporary), lock
            ):
                source = inputs[flavor] / name
                if source.is_symlink() or not source.is_file():
                    raise ValueError(f"Missing regular native package file: {source}")
                with source.open("rb") as file:
                    if hashlib.file_digest(file, "sha256").hexdigest() != expected_digest:
                        raise ValueError(f"Native package file differs from lock: {source}")
                    file.seek(0)
                    member = tarfile.TarInfo(relative)
                    member.size = os.fstat(file.fileno()).st_size
                    member.mode = mode
                    archive.addfile(member, file)
        if checksum(pending) != lock["archive_sha256"]:
            raise ValueError("Recreated native archive differs from the locked archive bytes")
        publish(pending, output)


def read_source_build_inputs(archive, runtimes):
    # Released binaries refer to the recipes used to build them, which can
    # differ from the tools in the current checkout.
    pending = {
        f"build-inputs/{relative}": digest
        for entry in runtimes
        for relative, digest in entry["build_inputs"].items()
    }
    inputs = {}
    with tarfile.open(archive, mode="r|gz") as tar:
        for member in tar:
            expected = pending.pop(member.name, None)
            if expected is None:
                continue
            if (
                not member.isfile()
                or member.issparse()
                or not 0 <= member.size <= BUILD_INPUT_LIMIT_BYTES
            ):
                raise ValueError(f"Invalid archived native build input: {member.name}")
            with tar.extractfile(member) as source:
                contents = source.read()
            if hashlib.sha256(contents).hexdigest() != expected:
                raise ValueError(f"Native build input differs from lock: {member.name}")
            inputs[member.name.removeprefix("build-inputs/")] = contents
            if not pending:
                break
    if pending:
        raise ValueError("Missing archived native build inputs: " + ", ".join(sorted(pending)))
    return inputs


def stage_native(archive, stage, lock, source_archive=None):
    if (
        archive.is_symlink()
        or not archive.is_file()
        or archive.stat().st_size > NATIVE_LIMIT_BYTES
        or checksum(archive) != lock["archive_sha256"]
    ):
        raise ValueError(f"Supply the pinned {NATIVE_ARCHIVE_NAME} in {archive.parent}")
    runtimes = lock["runtimes"]
    build_inputs = read_source_build_inputs(source_archive, runtimes) if source_archive else {}
    files = {path: (digest, mode) for _, _, path, digest, mode in native_archive_files(stage, lock)}
    extract_native_archive(archive, stage, files)
    for entry in runtimes:
        runtime = entry["manifest"]
        family = engine_directory(stage / "runtimes", runtime["engine_id"], ADAPTERS)
        directory = family / entry["directory"]
        check_amd64_elf(directory / runtime["executable"])
        if runtime["flavor"] == "krkr-next":
            check_amd64_elf(directory / "lib/libengine_api.so")
        if runtime["binary_sha256"] != entry["files"][runtime["executable"]]:
            raise ValueError(f"Manifest runner digest differs from archive lock: {runtime['id']}")
        if any(entry["files"].get(k) != v for k, v in runtime.get("resource_files", {}).items()):
            raise ValueError(f"Manifest resource digest differs from archive lock: {runtime['id']}")
        write_json(directory / "runtime.json", runtime)
        notices = stage / "licenses/native" / runtime["flavor"]
        notices.mkdir(parents=True)
        for name in entry["files"]:
            if name in (runtime["executable"], "lib/libengine_api.so"):
                continue
            destination = notices / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.link(directory / name, destination)
        if source_archive is not None:
            for relative in entry["build_inputs"]:
                destination = notices / "build-inputs" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(build_inputs[relative])
        write_json(notices / "sources.json", entry["sources"])
    return [{"id": entry["manifest"]["id"], "files": entry["files"]} for entry in runtimes]


def stage_renpy(stage, cache):
    records = []
    sources = json.loads(SDK_SOURCE_LOCK.read_text(encoding="utf-8"))
    for release in RELEASES:
        print(f"Verifying Ren'Py {release.version}", flush=True)
        archive = download_cached(release.url, release.sha256, cache, NATIVE_LIMIT_BYTES)
        sdk = install_runtime(stage / "runtimes", release, archive=archive)
        notices = stage / "licenses/renpy" / release.version
        trim_renpy_sdk(sdk, notices)
        record = {
            "version": release.version,
            "archive": {"url": release.url, "sha256": release.sha256},
            "sources": sources[release.version],
        }
        write_json(notices / "sources.json", record)
        records.append(record)
    (stage / "runtimes/.install.lock").unlink()
    return records


def verify_source_archive(directory, lock):
    expected = lock.get("source_archive_sha256")
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("The corresponding source archive SHA-256 is not locked for release")
    archive = directory / SOURCE_ARCHIVE_NAME
    if (
        archive.is_symlink()
        or not archive.is_file()
        or archive.stat().st_size > SOURCE_LIMIT_BYTES
        or checksum(archive) != expected
    ):
        raise ValueError(f"Supply the matching source archive {SOURCE_ARCHIVE_NAME} in {directory}")
    return {"name": SOURCE_ARCHIVE_NAME, "sha256": expected}


def prepare(output, cache, native_archives, *, isolated=False):
    if output.exists() or output.is_symlink():
        raise ValueError(f"Runtime bundle already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = json.loads(NATIVE_LOCK.read_text(encoding="utf-8"))
    sources = None if isolated else verify_source_archive(native_archives, lock)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".runtime-bundle-") as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir(mode=0o755)
        native = stage_native(
            native_archives / NATIVE_ARCHIVE_NAME,
            stage,
            lock,
            source_archive=native_archives / sources["name"] if sources else None,
        )
        renpy = stage_renpy(stage, cache)
        provenance = {
            "schema": 1,
            "platform": "linux/amd64",
            "renpy": renpy,
            "native": native,
            "native_archive": {"name": NATIVE_ARCHIVE_NAME, "sha256": lock["archive_sha256"]},
            "source_archive": sources,
            "deduplicated_bytes": deduplicate(stage / "runtimes"),
        }
        write_json(stage / "provenance.json", provenance)
        publish(stage, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New build context directory")
    parser.add_argument("--cache", type=Path, default=Path("var/runtime-bundle-cache"))
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="Prepare binaries for local testing without the corresponding source archive",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--native-archives", type=Path, help="Pinned archive directory")
    source.add_argument(
        "--assemble-native-from",
        metavar="FLAVOR=PATH",
        action="append",
        help="Recreate the pinned native archive from six verified isolated build packages",
    )
    args = parser.parse_args()
    try:
        if args.assemble_native_from is None:
            prepare(
                args.output.absolute(), args.cache, args.native_archives, isolated=args.isolated
            )
        else:
            inputs = {}
            for item in args.assemble_native_from:
                flavor, separator, value = item.partition("=")
                if not separator or not value or flavor in inputs:
                    parser.error("Provide one --assemble-native-from FLAVOR=PATH for each runner")
                inputs[flavor] = Path(value)
            lock = json.loads(NATIVE_LOCK.read_text(encoding="utf-8"))
            assemble_native_archive(args.output.absolute(), lock, inputs)
    except (OSError, ValueError, EOFError, tarfile.TarError) as error:
        parser.exit(1, f"Runtime bundle failed: {error}\n")


if __name__ == "__main__":
    main()
