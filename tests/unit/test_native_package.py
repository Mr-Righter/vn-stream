"""Native runtime publication includes the files required for graphical startup."""

import hashlib
import json
import runpy
from pathlib import Path

import pytest

from vn_stream.engines.registry import ADAPTERS
from vn_stream.runtimes.catalog import available_runtimes


def installed_krkr2(root):
    resources = {
        "Resources/locale/en_us.xml": "<locale/>",
        "Resources/vn-stream-alsoft.conf": "[pulse]\nmmap=false\n",
    }
    for name, content in resources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    runner = root / "runner"
    runner.write_text("#!/bin/sh\nexit 0\n")
    runner.chmod(0o755)
    record = dict(
        schema=1,
        id="krkr:krkr2:test",
        engine_id="krkr",
        flavor="krkr2",
        version="test-build",
        executable="runner",
        validation="validated",
        binary_sha256=hashlib.sha256(runner.read_bytes()).hexdigest(),
        resource_files={
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in resources
        },
    )
    (root / "runtime.json").write_text(json.dumps(record))
    return dict(record, path=str(root))


def test_installed_krkr2_rejects_modified_resources(tmp_path):
    installed = tmp_path / "kirikiri/krkr2"
    installed_krkr2(installed)
    assert len(available_runtimes(tmp_path, ADAPTERS)) == 1
    locale = installed / "Resources/locale/en_us.xml"
    locale.write_text("modified")
    assert available_runtimes(tmp_path, ADAPTERS) == []


def test_yuri_publication_uses_clean_upstream_identity(tmp_path):
    module = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/build-native-runtime.py")
    )
    source = tmp_path / "source"
    binary = source / "build/onsyuri"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    (source / "LICENSE").write_text("Test-only license")
    installed = module["publish"](source, tmp_path / "installed", "yuri")
    record = json.loads((installed / "runtime.json").read_text())
    assert "build_patch_sha256" not in record
    assert record["version"] == "0.7.7"
    assert record["display_name"] == "ONScripterYuri 0.7.7"


def test_krkr_cannot_omit_required_resources_at_discovery_or_launch(tmp_path):
    from vn_stream.engines.registry import inspect_project, prepare_launch
    from vn_stream.runtimes.catalog import verify_resources

    output = tmp_path / "runtimes"
    installed = output / "kirikiri/krkr2"
    record = installed_krkr2(installed)
    project = tmp_path / "project"
    project.mkdir()
    (project / "startup.tjs").write_text("System.exit();")
    (installed / "Resources/locale/en_us.xml").unlink()
    with pytest.raises(ValueError):
        prepare_launch(project, inspect_project(project), record)
    record.pop("resource_files")
    assert not verify_resources(installed, record, required=ADAPTERS["krkr"].required_resources)
    (installed / "runtime.json").write_text(json.dumps(record))
    assert available_runtimes(output, ADAPTERS) == []


def test_reused_native_checkout_rejects_untracked_build_inputs(tmp_path):
    import subprocess

    module = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/build-native-runtime.py")
    )
    source = tmp_path / "source"
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "assets").mkdir()
    (source / "assets/locale.xml").write_text("original")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    (source / "assets/extra.xml").write_text("unexpected")
    with pytest.raises(ValueError, match="untracked"):
        module["checkout"](source, "unused", commit)


def test_krkr_launch_uses_hashed_audio_profile_without_changing_project(tmp_path):
    from vn_stream.engines.registry import inspect_project, prepare_launch

    installed = tmp_path / "runtimes/kirikiri/krkr2"
    record = installed_krkr2(installed)
    project = tmp_path / "project"
    project.mkdir()
    (project / "startup.tjs").write_text("System.exit();")
    launch = prepare_launch(project, inspect_project(project), record)
    audio = installed / "Resources/vn-stream-alsoft.conf"
    assert launch.env.get("ALSOFT_CONF") == str(audio)
    assert "mmap=false" in audio.read_text()
    assert (
        record["resource_files"]["Resources/vn-stream-alsoft.conf"]
        == hashlib.sha256(audio.read_bytes()).hexdigest()
    )
    assert not (project / "alsoft.conf").exists()


def rs_runtime(root, **extra):
    root.mkdir(parents=True)
    runner = root / "runner"
    runner.write_text("#!/bin/sh\nexit 0\n")
    runner.chmod(0o755)
    resources = {}
    for name in ("LICENSE", "LICENSE-IJG.txt", "README.txt"):
        (root / name).write_text("Test-only notice")
        resources[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    record = dict(
        schema=1,
        id="krkr:rs:test",
        engine_id="krkr",
        flavor="krkr-rs",
        version="test-build",
        executable="runner",
        validation="validated",
        resource_files=resources,
        binary_sha256=hashlib.sha256(runner.read_bytes()).hexdigest(),
        **extra,
    )
    (root / "runtime.json").write_text(json.dumps(record))
    return record


def test_rs_discovery_and_launch_use_own_profile_without_cocos_resources(tmp_path):
    from vn_stream.engines.registry import inspect_project, prepare_launch

    installed = tmp_path / "runtimes/kirikiri/rs"
    rs_runtime(installed)
    runtimes = available_runtimes(installed.parent.parent, ADAPTERS)
    assert len(runtimes) == 1
    project = tmp_path / "project"
    project.mkdir()
    (project / "startup.tjs").write_text("System.exit();")
    launch = prepare_launch(project, inspect_project(project), runtimes[0])
    assert launch.command == [
        str(installed / "runner"),
        "--base-dir",
        str(project),
        "--backend",
        "vulkan",
    ]
    assert launch.env["HOME"] == str(project / ".vn-stream/home")
    assert "ALSOFT_CONF" not in launch.env
    assert not (project / "game/saves").exists()


def test_krkr_unknown_profile_is_rejected_at_discovery_and_launch(tmp_path):
    from vn_stream.engines.registry import inspect_project, prepare_launch

    installed = tmp_path / "runtimes/kirikiri/rs"
    record = rs_runtime(installed)
    record.update(flavor="unknown", path=str(installed))
    (installed / "runtime.json").write_text(json.dumps(record))
    assert available_runtimes(installed.parent.parent, ADAPTERS) == []
    (tmp_path / "startup.tjs").write_text("System.exit();")
    with pytest.raises(ValueError):
        prepare_launch(tmp_path, inspect_project(tmp_path), record)


@pytest.mark.parametrize("defaults,expected", [(1, "krkr:rs:test"), (0, None), (2, None)])
def test_native_recommendation_uses_one_explicit_default_only(tmp_path, defaults, expected):
    from vn_stream.engines.registry import inspect_project, resolve_runtime

    (tmp_path / "startup.tjs").write_text("System.exit();")
    records = [
        dict(
            id="krkr:legacy:test",
            engine_id="krkr",
            version="legacy",
            validation="validated",
            default=defaults == 2,
        ),
        dict(
            id="krkr:rs:test",
            engine_id="krkr",
            version="rs",
            validation="validated",
            default=defaults > 0,
        ),
        dict(
            id="krkr:unverified:test",
            engine_id="krkr",
            version="unverified",
            validation="experimental",
            default=True,
        ),
    ]
    assert resolve_runtime(inspect_project(tmp_path), records)["runtime_id"] == expected


def test_native_recommendation_exposes_name_without_replacing_build_identity(tmp_path):
    from vn_stream.engines.registry import inspect_project, resolve_runtime

    (tmp_path / "startup.tjs").write_text("System.exit();")
    result = resolve_runtime(
        inspect_project(tmp_path),
        [
            dict(
                id="krkr:rs:test",
                engine_id="krkr",
                flavor="krkr-rs",
                version="build-sha",
                display_name="krkr-rs",
                validation="validated",
                default=True,
            )
        ],
    )
    assert result["version"] == "build-sha"
    assert result["display_name"] == "krkr-rs"


def test_zero_publication_keeps_manual_validation_with_a_distinct_identity(tmp_path):
    module = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/build-native-runtime.py")
    )
    source = tmp_path / "source"
    binary = source / "build/linux/onscripter"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    (source / "LICENSE").write_text("Test-only license")
    installed = module["publish"](source, tmp_path / "installed", "zero")
    record = json.loads((installed / "runtime.json").read_text())
    assert record["id"].startswith("ons:zero:")
    assert record["validation"] == "experimental"
    assert "zero" in record["display_name"]
    assert record.get("default") is not True
    assert set(module["SPECS"]) == {
        "yuri",
        "jh",
        "zero",
        "new",
    }


def test_zero_package_records_overlay_and_header_only_library_notices(tmp_path):
    module = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/build-native-runtime.py")
    )
    source = tmp_path / "source"
    binary = source / "build/linux/onscripter"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"test executable")
    (source / "LICENSE").write_text("Test-only license")
    installed = module["publish"](source, tmp_path / "installed", "zero")
    manifest = json.loads((installed / "runtime.json").read_text())
    overlay = Path(__file__).resolve().parents[2] / "containers/native-build/zero/CMakeLists.txt"
    assert (
        manifest["build_inputs"]["containers/native-build/zero/CMakeLists.txt"]
        == hashlib.sha256(overlay.read_bytes()).hexdigest()
    )
    patch = (
        Path(__file__).resolve().parents[2]
        / "containers/native-patches/ons/zero-mask-alignment.patch"
    )
    assert manifest["build_patch_sha256"] == hashlib.sha256(patch.read_bytes()).hexdigest()
    assert (installed / "source.patch").read_bytes() == patch.read_bytes()
    assert manifest["version"].endswith("+maskfix")
    for name in ("fmt-copyright", "stb-copyright"):
        assert (installed / "THIRD_PARTY" / name).is_file()


def test_new_publication_uses_family_directory_and_branch_identity(tmp_path):
    module = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/build-native-runtime.py")
    )
    source = tmp_path / "source"
    executable = source / "DerivedData/Posix-x86_64/onscripter-new"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    for name in ("LICENSE", "LICENSE-BSD", "LICENSE-GPLv2"):
        (source / name).write_text("Fixture license")
    installed = module["publish"](source, tmp_path / "runtimes", "new")
    assert installed.parent == tmp_path / "runtimes/onscripter"
    record = json.loads((installed / "runtime.json").read_text())
    assert record["flavor"] == "new"
    assert record["validation"] == "experimental"
    assert not record.get("default")
    assert record["display_name"] == "ONScripter-NEW"


@pytest.mark.parametrize("entry", ["startup.tjs", "data.xp3"])
def test_next_launch_uses_own_library_and_correct_project_entry(tmp_path, entry):
    from vn_stream.engines.registry import inspect_project, prepare_launch
    from vn_stream.runtimes.native import resolve_native_runtime

    runtime_root = tmp_path / "runtimes"
    installed = runtime_root / "kirikiri/next"
    record = rs_runtime(installed)
    record.update(id="krkr:next:test", flavor="krkr-next", validation="experimental")
    for relative in ("lib/libengine_api.so", "LICENSE-components.txt", "THIRD_PARTY-NOTICES.txt"):
        path = installed / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"test-only artifact")
        record["resource_files"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    (installed / "runtime.json").write_text(json.dumps(record))
    found = available_runtimes(runtime_root, ADAPTERS)
    assert [item["id"] for item in found] == ["krkr:next:test"]
    assert resolve_native_runtime(None, found)["runtime_id"] is None
    project = tmp_path / "project"
    project.mkdir()
    (project / entry).write_bytes(
        b"System.exit();" if entry == "startup.tjs" else b"XP3\r\n \n\x1a\x8bg\x01"
    )
    launch = prepare_launch(project, inspect_project(project), found[0], allow_experimental=True)
    assert launch.command == [
        str(installed / "runner"),
        "--game",
        str(project if entry == "startup.tjs" else project / entry),
    ]
    assert "ALSOFT_CONF" not in launch.env
    assert not (project / "Resources").exists()
    (installed / "lib/libengine_api.so").write_bytes(b"changed")
    assert available_runtimes(runtime_root, ADAPTERS) == []
