import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
from runpy import run_path

import pytest

BUNDLE = Path(__file__).resolve().parents[2] / "scripts/prepare-runtime-bundle.py"


@pytest.fixture
def bundle():
    return run_path(str(BUNDLE))


def test_renpy_trim_keeps_all_license_text(bundle, tmp_path):
    sdk = tmp_path / "sdk"
    kept = (
        "renpy.sh",
        "LICENSE.txt",
        "lib/py3-linux-x86_64/renpy",
        "lib/python3.9/encodings/__init__.py",
        "renpy/common/00audio.rpy",
        "launcher/game/launcher.rpy",
    )
    removed = (
        "renpy.exe",
        "renpy-32.exe",
        "renpy.app/info.plist",
        "lib/py3-linux-aarch64/renpy",
        "lib/py3-linux-i686/renpy",
        "lib/py3-mac-universal/renpy",
        "lib/py3-windows-x86_64/renpy.exe",
        "the_question/game/script.rpy",
        "tutorial/game/script.rpy",
        "doc/index.html",
        "doc/license.html",
    )
    for name in kept + removed:
        path = sdk / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)

    notices = tmp_path / "notices"
    bundle["trim_renpy_sdk"](sdk, notices)

    assert {path.relative_to(sdk).as_posix() for path in sdk.rglob("*") if path.is_file()} == set(
        kept
    )
    assert (notices / "LICENSE.txt").read_text() == "LICENSE.txt"
    assert (notices / "license.html").read_text() == "doc/license.html"


def test_deduplication_preserves_contents_and_mode(bundle, tmp_path):
    entries = {
        "a": ("same", 0o644),
        "b": ("same", 0o644),
        "c": ("same", 0o755),
        "d": ("different", 0o644),
    }
    for name, (data, mode) in entries.items():
        path = tmp_path / name
        path.write_text(data)
        path.chmod(mode)

    assert bundle["deduplicate"](tmp_path) == 4
    assert os.path.samefile(tmp_path / "a", tmp_path / "b")
    assert not os.path.samefile(tmp_path / "a", tmp_path / "c")
    assert not os.path.samefile(tmp_path / "a", tmp_path / "d")
    for name, (data, mode) in entries.items():
        assert (tmp_path / name).read_text() == data
        assert (tmp_path / name).stat().st_mode & 0o777 == mode


@pytest.mark.parametrize("change", ["traversal", "link", "duplicate", "digest", "missing", "extra"])
def test_native_tar_rejects_unverified_members(bundle, tmp_path, change):
    archive = tmp_path / "runner.tar.gz"
    digest = hashlib.sha256(b"OK").hexdigest()
    with tarfile.open(archive, "w:gz") as tar:
        entries = {
            "traversal": ["../runner"],
            "link": ["runner"],
            "duplicate": ["runner", "runner"],
            "digest": ["runner"],
            "missing": [],
            "extra": ["runner", "another"],
        }
        for name in entries[change]:
            member = tarfile.TarInfo(name)
            if change == "link":
                member.type = tarfile.SYMTYPE
                member.linkname = "../runner"
                tar.addfile(member)
            else:
                member.size = 2
                tar.addfile(member, io.BytesIO(b"NO" if change == "digest" else b"OK"))

    with pytest.raises(ValueError):
        bundle["extract_native_archive"](archive, tmp_path / "out", {"runner": (digest, 0o755)})
    assert not (tmp_path / "runner").exists()


def test_native_tar_uses_locked_mode(bundle, tmp_path):
    archive = tmp_path / "runner.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("dir/runner")
        member.size = 2
        member.mode = 0o4777
        tar.addfile(member, io.BytesIO(b"OK"))

    files = {"dir/runner": (hashlib.sha256(b"OK").hexdigest(), 0o755)}
    bundle["extract_native_archive"](archive, tmp_path / "out", files)
    assert (tmp_path / "out/dir/runner").read_bytes() == b"OK"
    assert (tmp_path / "out/dir/runner").stat().st_mode & 0o7777 == 0o755


def test_archive_recreation_rejects_changed_native_input(bundle, tmp_path):
    source = tmp_path / "isolated-package"
    source.mkdir()
    (source / "runner").write_bytes(b"changed")
    lock = {
        "archive_sha256": "locked archive",
        "runtimes": [
            {
                "directory": "fixture",
                "manifest": {"engine_id": "ons", "flavor": "test", "executable": "runner"},
                "files": {"runner": hashlib.sha256(b"original").hexdigest()},
            }
        ],
    }
    output = tmp_path / "release.tar.gz"
    with pytest.raises(ValueError, match="package file differs"):
        bundle["assemble_native_archive"](output, lock, {"test": source})
    assert not output.exists()


def test_bundle_publication_never_overwrites_a_directory(bundle, tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "provenance.json").touch()
    existing = tmp_path / "existing"
    existing.mkdir()

    with pytest.raises(FileExistsError):
        bundle["publish"](pending, existing)
    assert list(existing.iterdir()) == []


def test_release_requires_the_matching_source_archive(bundle, tmp_path):
    source = tmp_path / "runtime-sources-linux-amd64.tar.gz"
    locked = hashlib.sha256(b"matching source").hexdigest()

    with pytest.raises(ValueError, match="source archive SHA-256 is not locked"):
        bundle["verify_source_archive"](tmp_path, {})
    with pytest.raises(ValueError, match="matching source archive"):
        bundle["verify_source_archive"](tmp_path, {"source_archive_sha256": locked})
    source.write_bytes(b"wrong source")
    with pytest.raises(ValueError, match="matching source archive"):
        bundle["verify_source_archive"](tmp_path, {"source_archive_sha256": locked})

    source.write_bytes(b"matching source")
    assert bundle["verify_source_archive"](tmp_path, {"source_archive_sha256": locked}) == {
        "name": source.name,
        "sha256": locked,
    }


@pytest.mark.parametrize("source_matches", [True, False])
def test_release_stages_original_build_inputs_from_source_archive(
    bundle, tmp_path, monkeypatch, source_matches
):
    relative = "scripts/build-native-runtime.py"
    original = b"original build recipe"
    runner = b"\x7fELF\x02\x01" + bytes(12) + b"\x3e\x00"
    runner_sha256 = hashlib.sha256(runner).hexdigest()
    runtime = {
        "id": "ons:test",
        "engine_id": "ons",
        "flavor": "fixture",
        "executable": "runner",
        "binary_sha256": runner_sha256,
    }
    archives = tmp_path / "archives"
    archives.mkdir()
    native = archives / bundle["NATIVE_ARCHIVE_NAME"]
    sources = archives / bundle["SOURCE_ARCHIVE_NAME"]
    for archive, name, data in (
        (native, "runtimes/onscripter/fixture/runner", runner),
        (sources, f"build-inputs/{relative}", original if source_matches else b"wrong recipe"),
    ):
        with tarfile.open(archive, "w:gz") as tar:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "archive_sha256": bundle["checksum"](native),
                "source_archive_sha256": bundle["checksum"](sources),
                "runtimes": [
                    {
                        "manifest": runtime,
                        "directory": "fixture",
                        "files": {"runner": runner_sha256},
                        "build_inputs": {relative: hashlib.sha256(original).hexdigest()},
                        "sources": {},
                    }
                ],
            }
        )
    )
    globals_ = bundle["prepare"].__globals__
    monkeypatch.setitem(globals_, "NATIVE_LOCK", lock)
    monkeypatch.setitem(globals_, "stage_renpy", lambda *_: [])
    output = tmp_path / "bundle"
    if not source_matches:
        with pytest.raises(ValueError, match="Native build input differs from lock"):
            bundle["prepare"](output, tmp_path / "cache", archives)
        assert not output.exists()
        return

    bundle["prepare"](output, tmp_path / "cache", archives)
    assert (output / "licenses/native/fixture/build-inputs" / relative).read_bytes() == original
