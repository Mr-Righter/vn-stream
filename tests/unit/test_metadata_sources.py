"""Self-authored data only; metadata scanning never executes compiled programs."""

import gzip
import io
import json
import marshal
import pickle
import struct
import tarfile
import zlib

import pytest

from tests.auth_helpers import TestClient, api_data, sign_in
from vn_stream.api.app import create_app
from vn_stream.engines.renpy.version_detection import detect_engine
from vn_stream.projects.metadata import game_info

WEBP = b"RIFF" + struct.pack("<I", 12) + b"WEBPVP8 " + b"\0" * 4


def test_webp_under_png_name_is_detected_and_served_with_its_real_mime(tmp_path):
    project = tmp_path / "games/story"
    (project / "game/gui").mkdir(parents=True)
    (project / "game/script.rpy").write_text("label start:\n    return")
    (project / "game/gui/window_icon.png").write_bytes(WEBP)
    with TestClient(create_app(tmp_path / "data", [tmp_path / "games"], "secret")) as c:
        sign_in(c, "secret")
        game = api_data(c.post("/api/v2/projects", json={"path": str(project)}))
        assert game["icon_url"].startswith("/api/v2/projects/")
        icon = c.get(game["icon_url"])
        assert icon.status_code == 200
        assert icon.headers["content-type"] == "image/webp"
        assert icon.content == WEBP
        c.cookies.clear()
        assert c.get(game["icon_url"]).status_code == 401


def pyc_version(version):
    # This fixture produces bytes; the reader must not unmarshal or import it.
    return (
        b"\xcb\r\r\n"
        + b"\0" * 12
        + marshal.dumps(compile(f"version = {version!r}", "renpy/vc_version.py", "exec"))
    )


def test_compiled_engine_version_is_static_and_not_taken_from_runtime_logs(tmp_path):
    (tmp_path / "renpy").mkdir()
    (tmp_path / "renpy/vc_version.pyc").write_bytes(pyc_version("8.5.3.26051504"))
    (tmp_path / "log.txt").write_text("Ren'Py 8.4.1.9999")
    result = detect_engine(tmp_path, allow_logs=False)
    assert result.version == "8.5.3"
    assert "renpy/vc_version.pyc" in result.sources


def test_apk_private_compiled_version_is_detected(tmp_path):
    (tmp_path / "assets").mkdir()
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        data = pyc_version("8.3.7.25031702")
        entry = tarfile.TarInfo("renpy/vc_version.pyc")
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    (tmp_path / "assets/private.mp3").write_bytes(gzip.compress(payload.getvalue()))
    result = detect_engine(tmp_path, allow_logs=False)
    assert result.version == "8.3.7"
    assert result.sources == ("assets/private.mp3:renpy/vc_version.pyc",)


def test_android_build_metadata_is_a_fallback_not_a_replacement_for_release_version(tmp_path):
    (tmp_path / "game/cache").mkdir(parents=True)
    (tmp_path / "android.json").write_text(json.dumps({"name": "Android Name", "version": "1.0"}))
    info = game_info(tmp_path, tmp_path / "cache.json")
    assert info["display_name"] == "Android Name"
    assert info["release_version"] == "1.0"
    (tmp_path / "game/cache/build_info.json").write_text(
        json.dumps({"name": "Real Story", "version": "0.4"})
    )
    info = game_info(tmp_path, tmp_path / "cache.json", refresh=True)
    assert info["display_name"] == "Real Story"
    assert info["release_version"] == "0.4"


def test_nested_static_options_are_parsed_without_choosing_translation_scripts(tmp_path):
    (tmp_path / "game/rpy").mkdir(parents=True)
    (tmp_path / "game/rpy/options.rpy").write_text(
        'define config.name = _("Nested Story")\ndefine config.version = "2.3"'
    )
    (tmp_path / "game/tl/chinese").mkdir(parents=True)
    (tmp_path / "game/tl/chinese/options.rpy").write_text(
        'define config.name = "Wrong Translation"'
    )
    info = game_info(tmp_path, tmp_path / "cache.json")
    assert info["display_name"] == "Nested Story"
    assert info["release_version"] == "2.3"


def rpyc_defines(fields, *, store="store.config"):
    def string(value):
        data = value.encode()
        return b"X" + struct.pack("<I", len(data)) + data

    payload = b"\x80\x02]("
    for field, expression in fields.items():
        payload += b"crenpy.ast\nDefine\n)\x81}("
        payload += string("varname") + string(field) + string("store") + string(store)
        payload += string("code") + b"crenpy.ast\nPyCode\n)\x81("
        payload += (
            b"K\x01crenpy.ast\nPyExpr\n("
            + string(expression)
            + string("game/options.rpy")
            + b"K\x01K\x03t\x81tbub"
        )
    payload += b"e."
    compressed = zlib.compress(payload)
    return b"RENPY RPC2" + struct.pack("<III", 1, 34, len(compressed)) + b"\0" * 12 + compressed


def rpa(path, members, *, version=3, protocol=4):
    header_size = 34 if version == 3 else 25
    payload = bytearray(b"\0" * header_size)
    index = {}
    key = 0x42424242 if version == 3 else 0
    for name, data in members.items():
        index[name] = [(len(payload) ^ key, len(data) ^ key, b"" if protocol >= 3 else "")]
        payload.extend(data)
    offset = len(payload)
    payload.extend(zlib.compress(pickle.dumps(index, protocol=protocol)))
    header = (
        f"RPA-3.0 {offset:016x} {key:08x}\n" if version == 3 else f"RPA-2.0 {offset:016x}\n"
    ).encode()
    assert len(header) == header_size
    payload[:header_size] = header
    path.write_bytes(payload)


@pytest.mark.parametrize("version,protocol", [(2, 2), (3, 4)])
def test_archived_compiled_configuration_and_icon_are_read_without_objects(
    tmp_path, version, protocol
):
    (tmp_path / "game").mkdir()
    compiled = rpyc_defines(
        {"name": '_("Fates Test")', "version": '"0.4"', "window_icon": '"gui/custom.webp"'}
    )
    rpa(
        tmp_path / "game/scripts.rpa",
        {"options.rpyc": compiled},
        version=version,
        protocol=protocol,
    )
    rpa(
        tmp_path / "game/archive.rpa", {"gui/custom.webp": WEBP}, version=version, protocol=protocol
    )
    cache = tmp_path / "metadata/info.json"
    info = game_info(tmp_path, cache)
    assert info["display_name"] == "Fates Test"
    assert info["release_version"] == "0.4"
    assert info["icon"] is not None
    assert cache.with_suffix(".icon").read_bytes() == WEBP


def test_compiled_define_only_reads_config_namespace_and_literal_expressions(tmp_path):
    (tmp_path / "game").mkdir()
    (tmp_path / "game/options.rpyc").write_bytes(
        rpyc_defines({"name": '"Build Name"', "version": '"9.9"'}, store="store.build")
    )
    (tmp_path / "game/gui.rpyc").write_bytes(
        rpyc_defines({"name": '_("Actual Name")', "version": '__import__("os").getcwd()'})
    )
    info = game_info(tmp_path, tmp_path / "cache.json")
    assert info["display_name"] == "Actual Name"
    assert info["release_version"] is None


@pytest.mark.parametrize("member", ["../outside.png", "/absolute.png", "gui/../../outside.png"])
def test_rpa_rejects_unsafe_member_paths(tmp_path, member):
    from vn_stream.assets.archive_index import Resources

    (tmp_path / "game").mkdir()
    rpa(tmp_path / "game/archive.rpa", {member: WEBP, "gui/window_icon.png": WEBP})
    resources = Resources(tmp_path)
    assert resources.names() == set()
    assert resources.read("gui/window_icon.png") is None


def test_rpa_does_not_execute_pickle_callables(tmp_path):
    from vn_stream.assets.archive_index import Resources

    (tmp_path / "game").mkdir()
    marker = tmp_path / "executed"
    # A pickle reducer would invoke os.system if loaded. Our index parser rejects GLOBAL.
    code = f"touch {marker}".encode()
    payload = b"cos\nsystem\n(S" + repr(code.decode()).encode() + b"\ntR."
    compressed = zlib.compress(payload)
    (tmp_path / "game/archive.rpa").write_bytes(b"RPA-3.0 0000000000000022 00000000\n" + compressed)
    assert Resources(tmp_path).names() == set()
    assert not marker.exists()


def test_archive_prefix_fragments_and_loose_file_precedence(tmp_path):
    from vn_stream.assets.archive_index import Resources

    game = tmp_path / "game"
    game.mkdir()
    prefix = b"RIFF"
    first, second = WEBP[4:12], WEBP[12:]
    payload = first + second
    index = {"icon.webp": [(34, len(first), prefix), (34 + len(first), len(second), b"")]}
    end = 34 + len(payload)
    (game / "archive.rpa").write_bytes(
        f"RPA-3.0 {end:016x} 00000000\n".encode()
        + payload
        + zlib.compress(pickle.dumps(index, protocol=4))
    )
    assert Resources(tmp_path).read("icon.webp")[0] == WEBP
    (game / "icon.webp").write_bytes(b"loose")
    assert Resources(tmp_path).read("icon.webp")[0] == b"loose"


def test_archive_invalid_chunk_ranges_and_corrupt_compression_are_optional(tmp_path):
    from vn_stream.assets.archive_index import Resources

    (tmp_path / "game").mkdir()
    path = tmp_path / "game/archive.rpa"
    path.write_bytes(
        b"RPA-3.0 0000000000000022 00000000\n"
        + zlib.compress(pickle.dumps({"icon.webp": [(1, 1000000, b"")]}, protocol=4))
    )
    assert Resources(tmp_path).read("icon.webp") is None
    path.write_bytes(b"RPA-3.0 0000000000000022 00000000\ninvalid zlib")
    assert Resources(tmp_path).names() == set()


def test_compiled_metadata_never_evaluates_game_expressions(tmp_path):
    from vn_stream.engines.renpy.static_config import compiled_config

    marker = tmp_path / "executed"
    expression = f'__import__("pathlib").Path({str(marker)!r}).touch()'
    assert compiled_config(rpyc_defines({"version": expression})) == []
    assert not marker.exists()
    assert compiled_config(b"RENPY RPC2\x01") == []
    assert compiled_config(b"not compressed") == []


def test_inflation_limits_reject_bombs():
    from vn_stream.assets.archive_index import inflate
    from vn_stream.engines.renpy.static_config import compiled_config

    with pytest.raises(ValueError):
        inflate(zlib.compress(b"x" * 1025), 1024)
    assert compiled_config(zlib.compress(b"x" * (8 * 1024**2 + 1))) == []


def test_conflicting_config_literals_stay_unknown(tmp_path):
    (tmp_path / "game").mkdir()
    (tmp_path / "game/options.rpy").write_text('define config.version = "1.0"')
    (tmp_path / "game/version.rpy").write_text('define config.version = "2.0"')
    (tmp_path / "game/build_info.json").write_text('{"version":"3.0"}')
    assert game_info(tmp_path, tmp_path / "cache.json")["release_version"] is None


def test_source_engine_declaration_outranks_stale_compiled_cache(tmp_path):
    (tmp_path / "renpy").mkdir()
    (tmp_path / "renpy/vc_version.py").write_text('version = "8.3.2.100"')
    (tmp_path / "renpy/vc_version.pyc").write_bytes(pyc_version("8.5.3.100"))
    assert detect_engine(tmp_path, allow_logs=False).version == "8.3.2"
    (tmp_path / "renpy/vc_version.py").unlink()
    (tmp_path / "renpy/vc_version.pyo").write_bytes(pyc_version("8.4.1.100"))
    assert detect_engine(tmp_path, allow_logs=False).status == "conflict"


def test_compiled_version_does_not_guess_from_python_or_arbitrary_bytes(tmp_path):
    (tmp_path / "renpy").mkdir()
    path = tmp_path / "renpy/vc_version.pyc"
    path.write_bytes(b"8.5.3 plain text is not compiled metadata")
    assert detect_engine(tmp_path, allow_logs=False).version is None
    path.write_bytes(pyc_version("3.12"))
    assert detect_engine(tmp_path, allow_logs=False).version is None


def test_cached_size_survives_parser_upgrade_and_warm_cache_avoids_rescan(tmp_path, monkeypatch):
    import vn_stream.projects.metadata as module

    (tmp_path / "game").mkdir()
    (tmp_path / "game/options.rpy").write_text('define config.version = "1.0"')
    cache = tmp_path / "metadata/info.json"
    cache.parent.mkdir()
    cache.write_text('{"schema":2,"size_bytes":12345}')
    original = module.os.walk
    walks = []

    def walk(path, **kwargs):
        walks.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(module.os, "walk", walk)
    first = game_info(tmp_path, cache)
    assert first["size_bytes"] == 12345 and first["release_version"] == "1.0"
    assert tmp_path not in walks
    walks.clear()
    assert game_info(tmp_path, cache) == first
    assert walks == []
    (tmp_path / "game/options.rpy").write_text('define config.version = "2.0"')
    assert game_info(tmp_path, cache, refresh=True)["release_version"] == "2.0"
    assert tmp_path in walks


def test_metadata_will_not_read_symlinked_assets(tmp_path):
    from vn_stream.assets.archive_index import Resources

    project = tmp_path / "story"
    (project / "game/gui").mkdir(parents=True)
    outside = tmp_path / "private.webp"
    outside.write_bytes(WEBP)
    (project / "game/gui/window_icon.png").symlink_to(outside)
    assert Resources(project).read("gui/window_icon.png") is None
    assert game_info(project, tmp_path / "cache.json")["icon"] is None


def test_engine_metadata_rejects_symlinked_parent(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "vc_version.pyc").write_bytes(pyc_version("8.5.3.100"))
    project = tmp_path / "story"
    project.mkdir()
    (project / "renpy").symlink_to(outside, target_is_directory=True)
    assert detect_engine(project, allow_logs=False).version is None


def test_rpa_nesting_and_memo_limits_are_bounded():
    from vn_stream.assets.archive_index import primitive_index

    for data in (b"(" * 33 + b"d.", b"\x80\x04}r" + struct.pack("<I", 100000) + b"."):
        with pytest.raises(ValueError):
            primitive_index(data)


def test_new_icon_revision_updates_after_explicit_refresh(tmp_path):
    project = tmp_path / "games/story"
    (project / "game/gui").mkdir(parents=True)
    (project / "game/script.rpy").write_text("label start:\n    return")
    icon = project / "game/gui/window_icon.png"
    icon.write_bytes(WEBP)
    with TestClient(create_app(tmp_path / "data", [tmp_path / "games"], "secret")) as client:
        sign_in(client, "secret")
        game = api_data(client.post("/api/v2/projects", json={"path": str(project)}))
        icon.write_bytes(WEBP + b"updated")
        games = api_data(client.post("/api/v2/projects/refresh"))
        updated = next(g for g in games if g["project_id"] == game["project_id"])
        assert updated["icon_url"] != game["icon_url"]
        assert client.get(updated["icon_url"]).content == WEBP + b"updated"


def test_memo_shared_chunk_lists_cannot_amplify_archive_memory(tmp_path):
    from vn_stream.assets.archive_index import Resources

    (tmp_path / "game").mkdir()
    chunks = [(34, 1, b"")] * 1024
    index = {f"file{number}.png": chunks for number in range(100)}
    (tmp_path / "game/archive.rpa").write_bytes(
        b"RPA-3.0 0000000000000023 00000000\nx" + zlib.compress(pickle.dumps(index, protocol=4))
    )
    assert Resources(tmp_path).names() == set()


def test_same_tier_build_conflicts_are_not_resolved_by_filename_order(tmp_path):
    (tmp_path / "game/cache").mkdir(parents=True)
    (tmp_path / "game/build_info.json").write_text('{"version":"1.0"}')
    (tmp_path / "game/cache/build_info.json").write_text('{"version":"2.0"}')
    cache = tmp_path / "metadata.json"
    assert game_info(tmp_path, cache)["release_version"] is None
    (tmp_path / "game/options.rpy").write_text('define config.version = "3.0"')
    assert game_info(tmp_path, cache, refresh=True)["release_version"] == "3.0"


def test_memo_shared_prefix_strings_have_an_aggregate_decoded_byte_budget(tmp_path):
    from vn_stream.assets.archive_index import Resources

    (tmp_path / "game").mkdir()
    prefix = "x" * 1024**2
    index = {f"file{number}.png": [(34, 1, prefix)] for number in range(17)}
    (tmp_path / "game/archive.rpa").write_bytes(
        b"RPA-3.0 0000000000000023 00000000\nx" + zlib.compress(pickle.dumps(index, protocol=2))
    )
    assert Resources(tmp_path).names() == set()
