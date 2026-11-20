import gzip
import io
import tarfile

import pytest

from vn_stream.engines.renpy.version_detection import detect_engine


def write(project, name, text):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def private_archive(project, entries):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name, content in entries.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    (project / "private.mp3").write_bytes(gzip.compress(data.getvalue()))


@pytest.mark.parametrize(
    "source",
    [
        "version_tuple = (8, 3, 7, vc_version)",
        "version_tuple = VersionTuple(8, 3, 7, vc_version)",
        'print "legacy python"\nversion_tuple = (8, 3, 7, vc_version)',
    ],
)
def test_detects_literal_engine_metadata_without_executing_source(tmp_path, source):
    write(tmp_path, "renpy/__init__.py", source + '\nraise RuntimeError("never execute")')
    result = detect_engine(tmp_path)
    assert result.version == "8.3.7"
    assert result.sources == ("renpy/__init__.py",)


def test_generated_version_module_supports_new_engine_layout(tmp_path):
    write(tmp_path, "renpy/__init__.py", 'version_tuple = VersionTuple(*version_dict["semver"])')
    write(tmp_path, "renpy/vc_version.py", 'version = "8.6.0.26110900"')
    assert detect_engine(tmp_path).version == "8.6.0"


def test_private_archive_is_inspected_without_extracting_paths(tmp_path):
    private_archive(
        tmp_path,
        {
            "renpy/__init__.py": b"version_tuple = (7, 8, 7, 0)",
            "../../escape": b"untrusted",
        },
    )
    result = detect_engine(tmp_path)
    assert result.version == "7.8.7"
    assert result.sources == ("private.mp3:renpy/__init__.py",)
    assert not (tmp_path.parent / "escape").exists()


def test_source_metadata_has_priority_over_old_player_logs(tmp_path):
    write(tmp_path, "renpy/__init__.py", "version_tuple = (7, 8, 7, 0)")
    write(tmp_path, "log.txt", "Ren'Py 8.5.3.26051501")
    assert detect_engine(tmp_path).version == "7.8.7"


def test_conflicting_engine_sources_do_not_silently_choose(tmp_path):
    write(tmp_path, "renpy/__init__.py", "version_tuple = (8, 3, 7, 0)")
    write(tmp_path, "renpy/vc_version.py", 'version = "8.5.3"')
    result = detect_engine(tmp_path)
    assert result.version is None
    assert result.status == "conflict"


def test_release_version_and_strings_are_not_engine_versions(tmp_path):
    write(tmp_path, "game/options.rpy", 'define config.version = "8.3.7"')
    write(tmp_path, "renpy/__init__.py", 'description = "version_tuple = (8, 3, 7, 0)"')
    assert detect_engine(tmp_path).version is None


def test_explicit_log_fallback_has_distinct_evidence_status(tmp_path):
    write(tmp_path, "traceback.txt", "Game v2.3.4\nRen'Py 8.3.7.25031702\n")
    result = detect_engine(tmp_path)
    assert result.version == "8.3.7"
    assert result.status == "log"


def test_private_archive_expansion_is_bounded(tmp_path):
    (tmp_path / "private.mp3").write_bytes(gzip.compress(b"x" * (2 * 1024 * 1024)))
    assert detect_engine(tmp_path, private_limit=1024).version is None


def test_corrupt_private_archive_does_not_crash_version_detection(tmp_path):
    (tmp_path / "private.mp3").write_bytes(
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff" + b"\xff" * 50
    )
    assert detect_engine(tmp_path).status == "unknown"


@pytest.mark.parametrize(
    "family,expected", [("py2-windows-x86_64", "7.5.3"), ("py3-linux-x86_64", "8.0.3")]
)
def test_shared_7_5_and_8_0_sources_follow_packaged_python_family(tmp_path, family, expected):
    # Those official releases share source: the version tuple is inside `if PY2`.
    write(
        tmp_path,
        "renpy/__init__.py",
        "if PY2:\n    version_tuple = (7, 5, 3, vc_version)\nelse:\n    version_tuple = (8, 0, 3, vc_version)\nraise RuntimeError('never execute')",
    )
    write(tmp_path, "renpy/vc_version.py", "vc_version = 22090809")
    (tmp_path / "lib" / family).mkdir(parents=True)
    detected = detect_engine(tmp_path)
    assert detected.version == expected
    assert detected.status == "metadata"


def test_shared_sources_without_python_family_are_ambiguous(tmp_path):
    write(
        tmp_path,
        "renpy/__init__.py",
        "if PY2:\n    version_tuple = (7, 5, 3, 0)\nelse:\n    version_tuple = (8, 0, 3, 0)",
    )
    result = detect_engine(tmp_path)
    assert result.version is None
    assert result.status == "conflict"


@pytest.mark.parametrize(
    "family,expected", [("py2-android-armv7l", "7.5.3"), ("py3-android-arm64", "8.0.3")]
)
def test_private_archive_uses_bundled_python_family_for_shared_sources(tmp_path, family, expected):
    private_archive(
        tmp_path,
        {
            "renpy/__init__.py": b"if PY2:\n    version_tuple = (7,5,3,vc_version)\nelse:\n    version_tuple = (8,0,3,vc_version)\n",
            "renpy/vc_version.py": b"vc_version = 22090809",
            f"lib/{family}/runtime-marker": b"metadata only; never execute",
        },
    )
    result = detect_engine(tmp_path)
    assert result.version == expected
    assert result.status == "metadata"
