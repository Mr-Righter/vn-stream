import stat
import zipfile
from pathlib import Path

import pytest

from vn_stream.engines.renpy.version_detection import detect_engine
from vn_stream.imports.archive import Limits, extract_archive
from vn_stream.imports.publication import import_project
from vn_stream.projects.catalog import ProjectCatalog


def archive(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, content in entries.items():
            z.writestr(name, content)
    return path


def test_zip_preserves_complete_project_and_existing_progress(tmp_path):
    package = archive(
        tmp_path / "sample.zip",
        {
            "Sample/game/script.rpyc": b"compiled",
            "Sample/game/saves/1-1.save": b"progress",
            "Sample/renpy/__init__.py": "version_tuple = (8, 3, 7, 0)",
            "Sample/Sample.exe": b"launcher",
        },
    )
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    game = import_project(library, package)
    root = Path(game.path)
    assert (root / "game/saves/1-1.save").read_bytes() == b"progress"
    assert (root / "Sample.exe").is_file()
    assert game.engine_version == "8.3.7"
    assert library.list() == [game]
    assert ProjectCatalog(tmp_path / "data", [tmp_path]).get(game.project_id) == game


def test_apk_removes_one_prefix_per_path_segment(tmp_path):
    package = archive(
        tmp_path / "sample.apk",
        {
            "assets/x-game/x-script.rpyc": b"compiled",
            "assets/x-game/x-images/x-x-name.webp": b"image",
            "assets/x-renpy/x-common/x-example.rpyc": b"common",
            "classes.dex": b"platform",
        },
    )
    game = import_project(ProjectCatalog(tmp_path / "data", [tmp_path]), package)
    root = Path(game.path)
    assert (root / "game/images/x-name.webp").read_bytes() == b"image"
    assert (root / "renpy/common/example.rpyc").is_file()
    assert game.engine_version is None


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "C:/escape", "a/../../escape", "a\\escape"]
)
def test_archive_rejects_unsafe_paths(tmp_path, name):
    package = archive(tmp_path / "bad.zip", {"game/script.rpyc": b"ok", name: b"bad"})
    with pytest.raises(ValueError):
        extract_archive(package, tmp_path / "output")


def test_rejects_symlink_archive(tmp_path):
    package = tmp_path / "bad.zip"
    entry = zipfile.ZipInfo("game/link")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(package, "w") as z:
        z.writestr(entry, "../../outside")
    with pytest.raises(ValueError):
        extract_archive(package, tmp_path / "output")


def test_expansion_limit_leaves_library_unchanged(tmp_path):
    package = archive(tmp_path / "bad.zip", {"game/script.rpyc": b"x" * 20})
    library = ProjectCatalog(tmp_path / "data", [tmp_path], limits=Limits(max_bytes=10))
    with pytest.raises(ValueError):
        import_project(library, package)
    assert library.list() == []
    assert list((tmp_path / "projects").iterdir()) == []


def test_directory_registration_does_not_copy_or_overwrite_saves(tmp_path):
    project = tmp_path / "Original"
    (project / "game/saves").mkdir(parents=True)
    (project / "game/script.rpyc").write_bytes(b"compiled")
    (project / "game/saves/persistent").write_bytes(b"progress")
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    first = import_project(library, project)
    second = import_project(library, project)
    assert first == second
    assert Path(first.path) == project
    assert (project / "game/saves/persistent").read_bytes() == b"progress"
    assert list((tmp_path / "projects").iterdir()) == []


def test_failed_import_does_not_leave_partial_game(tmp_path):
    package = archive(tmp_path / "bad.apk", {"classes.dex": b"no resources"})
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    with pytest.raises(ValueError):
        import_project(library, package)
    assert library.list() == []
    assert list((tmp_path / "projects").iterdir()) == []


def test_reserved_metadata_never_overwritten(tmp_path):
    project = tmp_path / "Original"
    (project / "game").mkdir(parents=True)
    (project / ".vn-stream").mkdir()
    (project / "game/script.rpyc").write_bytes(b"compiled")
    (project / ".vn-stream/project.json").write_text("user data")
    with pytest.raises(ValueError):
        import_project(ProjectCatalog(tmp_path / "data", [tmp_path]), project)
    assert (project / ".vn-stream/project.json").read_text() == "user data"


def test_source_must_be_in_allowed_roots(tmp_path):
    library = ProjectCatalog(tmp_path / "data", [tmp_path / "allowed"])
    with pytest.raises(ValueError):
        import_project(library, tmp_path / "elsewhere")


def test_version_detection_does_not_execute_python(tmp_path):
    (tmp_path / "renpy").mkdir()
    sentinel = tmp_path / "executed"
    (tmp_path / "renpy/__init__.py").write_text(
        f'open({str(sentinel)!r}, "w").write("bad")\nversion_tuple = (7, 8, 7, 0)\n'
    )
    assert detect_engine(tmp_path).version == "7.8.7"
    assert not sentinel.exists()


def test_project_move_reindexes_identity_and_progress(tmp_path):
    source = tmp_path / "Original"
    (source / "game/saves").mkdir(parents=True)
    (source / "game/script.rpyc").write_bytes(b"compiled")
    (source / "game/saves/1.save").write_bytes(b"progress")
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    game = import_project(library, source)
    moved = tmp_path / "Moved"
    source.rename(moved)
    restored = import_project(library, moved)
    assert restored.project_id == game.project_id
    assert Path(library.get(game.project_id).path) == moved
    assert (moved / "game/saves/1.save").read_bytes() == b"progress"


def test_apk_rejects_partial_prefix_layout(tmp_path):
    package = archive(
        tmp_path / "bad.apk",
        {
            "assets/x-game/x-script.rpyc": b"compiled",
            "assets/x-game/images/x-bg.png": b"wrong layout",
        },
    )
    with pytest.raises(ValueError):
        import_project(ProjectCatalog(tmp_path / "data", [tmp_path]), package)


def test_ambiguous_archive_does_not_choose_arbitrary_game(tmp_path):
    package = archive(
        tmp_path / "two.zip",
        {
            "A/game/script.rpyc": b"a",
            "B/game/script.rpyc": b"b",
        },
    )
    with pytest.raises(ValueError):
        import_project(ProjectCatalog(tmp_path / "data", [tmp_path]), package)


def test_import_does_not_mutate_source_archive(tmp_path):
    package = archive(tmp_path / "game.zip", {"game/script.rpyc": b"compiled"})
    original = package.read_bytes()
    import_project(ProjectCatalog(tmp_path / "data", [tmp_path]), package)
    assert package.read_bytes() == original


@pytest.mark.parametrize("segment", ["x-..", "x-."])
def test_apk_normalization_rejects_dot_segments_without_external_writes(tmp_path, segment):
    victim = tmp_path / "victim"
    victim.write_bytes(b"original")
    package = archive(
        tmp_path / "escape.apk",
        {
            "assets/x-game/x-script.rpyc": b"compiled",
            f"assets/{segment}/{segment}/{segment}/{segment}/{segment}/x-victim": b"corrupt",
        },
    )
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    with pytest.raises(ValueError):
        import_project(library, package)
    assert victim.read_bytes() == b"original"
    assert library.list() == []


def test_archive_publication_does_not_retain_source_copy(tmp_path, monkeypatch):
    import shutil

    package = archive(tmp_path / "game.zip", {"game/script.rpyc": b"compiled"})
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    original = shutil.copyfile

    def failing_retention(source, destination, *args, **kwargs):
        if Path(source) == package:
            assert library.list() == []
            raise OSError("disk full")
        return original(source, destination, *args, **kwargs)

    monkeypatch.setattr(shutil, "copyfile", failing_retention)
    game = import_project(library, package)
    assert library.list() == [game]
    assert not (Path(game.path) / ".vn-stream/source").exists()
    assert package.exists()


def test_runtime_choice_survives_reindex_without_copying_project(tmp_path, monkeypatch):
    project = tmp_path / "Original"
    (project / "game/saves").mkdir(parents=True)
    (project / "game/script.rpyc").write_bytes(b"compiled")
    (project / "game/saves/1.save").write_bytes(b"progress")
    (project / "game/movie.webm").write_bytes(b"original video")
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    game = import_project(library, project)
    (project / ".vn-stream/home").mkdir()
    (project / ".vn-stream/home/keep").write_bytes(b"home progress")

    def fail_copy(*args, **kwargs):
        raise AssertionError("Runtime selection must not copy project files")

    monkeypatch.setattr("shutil.copytree", fail_copy)
    for version in ("8.3.7", "8.3.7", "8.5.3"):
        library.select_runtime(game.project_id, version, override=version)
        assert library.runtime_version(game.project_id) == version
        assert library.runtime_override(game.project_id) == version
    assert not (tmp_path / "data/backups").exists()
    assert ProjectCatalog(tmp_path / "data", [tmp_path]).runtime_version(game.project_id) == "8.5.3"
    assert (project / "game/saves/1.save").read_bytes() == b"progress"
    assert (project / "game/movie.webm").read_bytes() == b"original video"
    assert (project / ".vn-stream/home/keep").read_bytes() == b"home progress"


def test_failed_manifest_write_does_not_change_runtime_choice(tmp_path, monkeypatch):
    project = tmp_path / "Original"
    (project / "game/saves").mkdir(parents=True)
    (project / "game/script.rpyc").write_bytes(b"compiled")
    (project / "game/saves/persistent").write_bytes(b"progress")
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    game = import_project(library, project)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("vn_stream.projects.catalog.os.replace", fail)
    with pytest.raises(OSError):
        library.select_runtime(game.project_id, "8.3.7")
    assert library.runtime_version(game.project_id) is None
    assert (project / "game/saves/persistent").read_bytes() == b"progress"
    assert not (project / ".vn-stream/manifest.pending").exists()
