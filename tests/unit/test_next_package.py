"""Publish the host and actual ABI as one immutable runtime identity."""

import hashlib
import json
import runpy
from pathlib import Path

import pytest

from vn_stream.engines.registry import ADAPTERS
from vn_stream.runtimes.catalog import available_runtimes


def test_next_package_identity_covers_abi_notices_and_host(tmp_path):
    package = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/package-next-runtime.py")
    )["publish"]
    source = tmp_path / "source"
    source.mkdir()
    files = {}
    for name in (
        "runner",
        "lib/libengine_api.so",
        "LICENSE",
        "LICENSE-components.txt",
        "THIRD_PARTY-NOTICES.txt",
        "README.txt",
        "source.patch",
        "build.json",
    ):
        path = source / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(name.encode())
        files[name] = path
    metadata = dict(
        source_commit="a" * 40,
        source="https://example.invalid/source",
        version="test",
        build_image="sha256:test",
    )
    output = tmp_path / "runtimes"
    first = package(files, output, metadata)
    manifest = json.loads((first / "runtime.json").read_text())
    assert manifest["validation"] == "experimental"
    assert manifest.get("default") is not True
    assert (
        manifest["resource_files"]["lib/libengine_api.so"]
        == hashlib.sha256(files["lib/libengine_api.so"].read_bytes()).hexdigest()
    )
    assert len(available_runtimes(output, ADAPTERS)) == 1
    with pytest.raises(FileExistsError):
        package(files, output, metadata)
    files["lib/libengine_api.so"].write_bytes(b"new ABI")
    second = package(files, output, metadata)
    assert second != first
    assert len(available_runtimes(output, ADAPTERS)) == 2
    assert (first / "lib/libengine_api.so").read_bytes() == b"lib/libengine_api.so"
    files.pop("LICENSE-components.txt")
    with pytest.raises(ValueError, match="missing"):
        package(files, output, metadata)
