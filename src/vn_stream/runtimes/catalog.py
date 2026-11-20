"""Static metadata detection: importing game Python would execute arbitrary code."""

import hashlib
import json
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from vn_stream.engines.directories import directories
from vn_stream.engines.renpy.version_detection import detect_engine


@lru_cache(maxsize=256)
def _binary_digest(path, identity):
    digest = hashlib.sha256()
    with Path(path).open("rb") as binary:
        while chunk := binary.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_binary(path, expected):
    stat = path.stat()
    return (
        _binary_digest(
            str(path), (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        )
        == expected
    )


def verify_resources(directory, record, *, required=()):
    """Check declared runtime assets without following links outside the install."""
    if isinstance(required, Mapping):
        if record.get("flavor") not in required:
            return False
        required = required[record["flavor"]]
    required = set(required)
    files = record.get("resource_files")
    if files is None:
        return not required
    if not isinstance(files, dict) or not 1 <= len(files) <= 256 or not required.issubset(files):
        return False
    for relative, digest in files.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            return False
        current = directory
        for part in path.parts:
            current /= part
            if current.is_symlink():
                return False
        if not current.is_file() or not verify_binary(current, digest):
            return False
    return True


def available_runtimes(root: Path, engines) -> list[dict]:
    """Merge verified local installs with the read-only image runtimes by ID."""
    local = _discover_runtimes(root, engines)
    bundled_root = os.environ.get("VN_STREAM_BUNDLED_RUNTIMES")
    if not bundled_root:
        return local
    bundled = _discover_runtimes(Path(bundled_root), engines)
    records = {item["id"]: {**item, "bundled": True} for item in bundled}
    records.update((item["id"], item) for item in local)
    return list(records.values())


def _discover_runtimes(root: Path, engines) -> list[dict]:
    if not root.exists():
        return []
    result = []
    groups = directories(root, engines)
    renpy = groups.get("renpy")
    for sdk in sorted(renpy.iterdir()) if renpy and renpy.is_dir() else []:
        if (
            not sdk.name.startswith(".")
            and not sdk.is_symlink()
            and sdk.is_dir()
            and (sdk / "renpy.sh").is_file()
        ):
            version = detect_engine(sdk, allow_logs=False).version
            if version:
                result.append(
                    {
                        "id": "renpy:" + version,
                        "engine_id": "renpy",
                        "flavor": "official",
                        "validation": "validated",
                        "version": version,
                        "path": str(sdk.resolve()),
                    }
                )
    installs = [
        (family, child)
        for family, group in groups.items()
        if group.is_dir()
        for child in sorted(group.iterdir())
    ]
    for family, directory in installs:
        manifest = directory / "runtime.json"
        if directory.name.startswith(".") or directory.is_symlink() or not manifest.is_file():
            continue
        try:
            with manifest.open() as file:
                value = json.loads(file.read(16385))
            executable = Path(value["executable"])
            if (
                value.get("schema") != 1
                or value.get("engine_id") != family
                or not isinstance(value.get("id"), str)
                or not value["id"].startswith(value["engine_id"] + ":")
                or not isinstance(value.get("version"), str)
                or executable.is_absolute()
                or ".." in executable.parts
                or not executable.parts
            ):
                continue
            binary = directory / executable
            if (
                any(p.is_symlink() for p in [binary, *binary.parents])
                or not os.access(binary, os.X_OK)
                or not binary.is_file()
            ):
                continue
            if value.get("binary_sha256") and not verify_binary(binary, value["binary_sha256"]):
                continue
            if not verify_resources(
                directory,
                value,
                required=getattr(engines[value["engine_id"]], "required_resources", ()),
            ):
                continue
            result.append(
                {
                    **value,
                    "display_name": value.get("display_name")
                    or value.get("flavor")
                    or value["version"],
                    "path": str(directory.resolve()),
                }
            )
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return list({item["id"]: item for item in result}.values())
