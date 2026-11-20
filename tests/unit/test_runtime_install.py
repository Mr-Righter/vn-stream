"""SDK installation must publish only a complete, verified engine."""

import hashlib
import io
import tarfile
from dataclasses import replace

import pytest

from vn_stream.engines.registry import ADAPTERS
from vn_stream.runtimes.catalog import available_runtimes


def sdk_archive(path, version="8.5.3", extra=()):
    prefix = f"renpy-{version}-sdk"
    files = [
        ("renpy/__init__.py", f"version_tuple = ({version.replace('.', ', ')}, 0)".encode()),
        ("renpy.sh", b"#!/bin/sh\necho must-not-execute > executed"),
        ("lib/py3-linux-x86_64/renpy", b"fake interpreter"),
    ]
    with tarfile.open(path, "w:bz2") as archive:
        for name, content in files:
            member = tarfile.TarInfo(f"{prefix}/{name}")
            member.size = len(content)
            member.mode = 0o755 if name.endswith(("renpy", ".sh")) else 0o644
            archive.addfile(member, io.BytesIO(content))
        for member in extra:
            archive.addfile(member, io.BytesIO(b"x" * member.size) if member.isfile() else None)
    return path


def test_release_catalog_contains_requested_versions_and_official_sources(tmp_path):
    from vn_stream.runtimes.releases import RELEASES, describe_releases

    assert [r.version for r in RELEASES] == [
        "7.5.3",
        "7.8.2",
        "8.0.3",
        "8.1.3",
        "8.2.3",
        "8.3.2",
        "8.3.7",
        "8.4.1",
        "8.5.3",
    ]
    for release in RELEASES:
        assert release.url == (
            f"https://www.renpy.org/dl/{release.version}/renpy-{release.version}-sdk.tar.bz2"
        )
        assert len(bytes.fromhex(release.sha256)) == 32
    assert all(r["installed"] is False for r in describe_releases(tmp_path, ADAPTERS))


def test_install_checks_hash_and_metadata_before_atomic_publication(tmp_path):
    from vn_stream.runtimes.install import install_runtime
    from vn_stream.runtimes.releases import RELEASES, describe_releases

    package = sdk_archive(tmp_path / "sdk.tar.bz2")
    release = replace(RELEASES[-1], sha256=hashlib.sha256(package.read_bytes()).hexdigest())
    root = tmp_path / "runtimes"
    installed = install_runtime(root, release, archive=package)
    assert installed == root / "renpy/8.5.3"
    assert available_runtimes(root, ADAPTERS) == [
        {
            "version": "8.5.3",
            "path": str(installed),
            "id": "renpy:8.5.3",
            "engine_id": "renpy",
            "flavor": "official",
            "validation": "validated",
        }
    ]
    assert (installed / "lib/py3-linux-x86_64/renpy").stat().st_mode & 0o111
    assert not (installed / "executed").exists()
    assert describe_releases(root, ADAPTERS)[-1]["installed"] is True
    # Reinstall never overwrites an engine that a running game may be using.
    with pytest.raises(ValueError, match="已存在"):
        install_runtime(root, release, archive=package)


def test_checksum_failure_leaves_no_visible_runtime(tmp_path):
    from vn_stream.runtimes.install import install_runtime
    from vn_stream.runtimes.releases import RELEASES

    package = sdk_archive(tmp_path / "bad.tar.bz2")
    root = tmp_path / "runtimes"
    with pytest.raises(ValueError, match="SHA-256"):
        install_runtime(root, RELEASES[-1], archive=package)
    assert available_runtimes(root, ADAPTERS) == []
    assert not list(root.glob(".install-*/"))


def test_mismatched_engine_is_not_installed(tmp_path):
    from vn_stream.runtimes.install import install_runtime
    from vn_stream.runtimes.releases import RELEASES

    package = sdk_archive(tmp_path / "wrong.tar.bz2", version="8.4.1")
    release = replace(RELEASES[-1], sha256=hashlib.sha256(package.read_bytes()).hexdigest())
    with pytest.raises((ValueError, OSError)):
        install_runtime(tmp_path / "runtimes", release, archive=package)
    assert available_runtimes(tmp_path / "runtimes", ADAPTERS) == []


@pytest.mark.parametrize("kind", ["traversal", "symlink", "hardlink", "device", "duplicate"])
def test_unsafe_sdk_entries_cannot_escape_or_publish(tmp_path, kind):
    from vn_stream.runtimes.install import install_runtime
    from vn_stream.runtimes.releases import RELEASES

    member = tarfile.TarInfo("renpy-8.5.3-sdk/../../escaped")
    if kind == "symlink":
        member.name, member.type, member.linkname = "renpy-8.5.3-sdk/link", tarfile.SYMTYPE, "/etc"
    elif kind == "hardlink":
        member.name, member.type, member.linkname = (
            "renpy-8.5.3-sdk/link",
            tarfile.LNKTYPE,
            "/etc/passwd",
        )
    elif kind == "device":
        member.name, member.type = "renpy-8.5.3-sdk/device", tarfile.CHRTYPE
    elif kind == "duplicate":
        member.name, member.size = "renpy-8.5.3-sdk/renpy.sh", 1
    package = sdk_archive(tmp_path / "unsafe.tar.bz2", extra=[member])
    release = replace(RELEASES[-1], sha256=hashlib.sha256(package.read_bytes()).hexdigest())
    with pytest.raises((ValueError, OSError)):
        install_runtime(tmp_path / "runtimes", release, archive=package)
    assert not (tmp_path / "escaped").exists()
    assert available_runtimes(tmp_path / "runtimes", ADAPTERS) == []


def test_incomplete_staging_is_not_listed_as_installed(tmp_path):
    sdk = tmp_path / ".install-partial"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text("version_tuple = (8, 5, 3, 0)")
    (sdk / "renpy.sh").touch()
    assert available_runtimes(tmp_path, ADAPTERS) == []


@pytest.mark.parametrize("limit", ["compressed_bytes", "expanded_bytes", "entries"])
def test_install_limits_leave_no_partial_engine(tmp_path, limit):
    from vn_stream.runtimes.install import InstallLimits, install_runtime
    from vn_stream.runtimes.releases import RELEASES

    package = sdk_archive(tmp_path / "limited.tar.bz2")
    release = replace(RELEASES[-1], sha256=hashlib.sha256(package.read_bytes()).hexdigest())
    limits = replace(InstallLimits(), **{limit: 1})
    with pytest.raises(ValueError, match="限制"):
        install_runtime(tmp_path / "runtimes", release, archive=package, limits=limits)
    assert available_runtimes(tmp_path / "runtimes", ADAPTERS) == []


def test_install_lease_prevents_concurrent_writers(tmp_path):
    import fcntl

    from vn_stream.runtimes.install import install_runtime
    from vn_stream.runtimes.releases import RELEASES

    root = tmp_path / "runtimes"
    root.mkdir()
    with (root / ".install.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="正在进行"):
            install_runtime(root, RELEASES[-1])
    assert available_runtimes(root, ADAPTERS) == []


def test_installer_refuses_x86_sdk_on_arm(tmp_path, monkeypatch):
    from vn_stream.runtimes.install import install_runtime
    from vn_stream.runtimes.releases import RELEASES

    monkeypatch.setattr("vn_stream.runtimes.install.platform.machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="x86_64"):
        install_runtime(tmp_path / "runtimes", RELEASES[-1])
    assert not (tmp_path / "runtimes").exists()
