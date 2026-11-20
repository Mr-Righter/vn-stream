"""Family directories are shared by project publication and runtime installation."""

import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from vn_stream.engines.registry import ADAPTERS
from vn_stream.imports.publication import import_project
from vn_stream.projects.catalog import ProjectCatalog
from vn_stream.runtimes.catalog import available_runtimes


def test_adapters_declare_full_directory_names():
    assert {key: getattr(adapter, "directory_name", None) for key, adapter in ADAPTERS.items()} == {
        "renpy": "renpy",
        "ons": "onscripter",
        "krkr": "kirikiri",
    }


def test_grouped_runtimes_keep_ids_and_reject_wrong_family(tmp_path):
    sdk = tmp_path / "renpy/8.5.3"
    (sdk / "renpy").mkdir(parents=True)
    (sdk / "renpy/__init__.py").write_text("version_tuple = (8,5,3,0)")
    (sdk / "renpy.sh").write_text("#!/bin/sh")
    native = tmp_path / "onscripter/branch"
    native.mkdir(parents=True)
    (native / "runner").write_text("#!/bin/sh")
    (native / "runner").chmod(0o755)
    record = dict(schema=1, id="ons:test:1", engine_id="ons", executable="runner", version="1")
    (native / "runtime.json").write_text(json.dumps(record))
    wrong = tmp_path / "kirikiri/misplaced"
    wrong.mkdir(parents=True)
    (wrong / "runner").write_text("#!/bin/sh")
    (wrong / "runner").chmod(0o755)
    (wrong / "runtime.json").write_text(json.dumps(record))
    found = available_runtimes(tmp_path, ADAPTERS)
    assert {r["id"] for r in found} == {"renpy:8.5.3", "ons:test:1"}
    assert next(r["path"] for r in found if r["id"] == "ons:test:1") == str(native)


def test_archive_publication_uses_detected_engine_directory(tmp_path):
    import zipfile

    archive = tmp_path / "game.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("Project/0.txt", "*define\ngame\n*start\nend\n")
    root = tmp_path / "games"
    catalog = ProjectCatalog(tmp_path / "data", [tmp_path], library_root=root)
    item = import_project(catalog, archive)
    assert Path(item.path).parent == root / "onscripter"
    assert item.engine_id == "ons"


def test_discovery_warns_without_registering_wrong_family(tmp_path):
    root = tmp_path / "games"
    project = root / "kirikiri/ActuallyONS"
    project.mkdir(parents=True)
    (project / "0.txt").write_text("*define\ngame\n*start\nend\n")
    catalog = ProjectCatalog(tmp_path / "data", [root], library_root=root)
    result = catalog.discover()
    assert result["warnings"] == 1
    assert result["added"] == 0
    assert project.is_dir()


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40))
def test_directory_components_cannot_escape_root(component):
    from types import SimpleNamespace

    from vn_stream.engines.directories import engine_directory

    engines = {"test": SimpleNamespace(directory_name=component)}
    try:
        result = engine_directory(Path("/games"), "test", engines)
    except ValueError:
        return
    assert result.parent == Path("/games")
    assert not result.name.startswith(".")
    assert "/" not in result.name and "\\" not in result.name
