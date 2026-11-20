"""Native asset providers share indexing and seekable source contracts."""

import importlib
import struct
from io import BytesIO

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from tests.native_asset_fixtures import ns2, nsa, png, xp3
from vn_stream.assets.sources import Source
from vn_stream.engines.registry import ADAPTERS, inspect_project


def provider(engine):
    value = getattr(ADAPTERS[engine], "asset_provider", None)
    assert value is not None, f"{engine} asset provider is not connected"
    return value


def enumerate_assets(root, engine):
    return provider(engine).enumerate(root, inspect_project(root), lambda: None)


def test_ns2_media_only_namespaces_and_real_signatures(tmp_path):
    (tmp_path / "0.txt").write_text("*define\ngame\n")
    webp = BytesIO()
    Image.new("RGB", (8, 8)).save(webp, format="WEBP")
    (tmp_path / "00.ns2").write_bytes(
        ns2([("图片.png", webp.getvalue()), ("voice.ogg", b"OggSdata"), ("script.txt", b"script")])
    )
    (tmp_path / "01.ns2").write_bytes(ns2([("图片.png", png())]))
    sources, warnings = enumerate_assets(tmp_path, "ons")
    assert not warnings
    assert set(sources) == {"00.ns2/图片.png", "00.ns2/voice.ogg", "01.ns2/图片.png"}
    assert Source(tmp_path, sources["00.ns2/图片.png"]).read_bytes() == webp.getvalue()
    assert sources["00.ns2/图片.png"]["media"]["mime"] == "image/webp"
    assert sources["01.ns2/图片.png"]["media"]["mime"] == "image/png"


@pytest.mark.parametrize("method", [0, 2, 4])
def test_nsa_standard_codecs(tmp_path, method):
    (tmp_path / "0.txt").write_text("*define\ngame\n")
    original = png()
    (tmp_path / "arc.nsa").write_bytes(nsa([("p.png", original, method)]))
    sources, warnings = enumerate_assets(tmp_path, "ons")
    assert not warnings
    source = Source(tmp_path, sources["arc.nsa/p.png"])
    assert source.read_bytes() == original
    with source.open() as stream:
        stream.seek(10)
        assert stream.read(12) == original[10:22]
        stream.seek(0)
        assert stream.read(8) == original[:8]


def test_sar_and_excluded_save_directories(tmp_path):
    (tmp_path / "0.txt").write_text('*define\nsavedir "progress"\ngame\n')
    (tmp_path / "arc.sar").write_bytes(nsa([("p.png", png(), 0)], sar=True))
    (tmp_path / "progress").mkdir()
    (tmp_path / "progress/shot.png").write_bytes(png())
    (tmp_path / ".vn-stream").mkdir()
    (tmp_path / ".vn-stream/secret.png").write_bytes(png())
    sources, _ = enumerate_assets(tmp_path, "ons")
    assert set(sources) == {"arc.sar/p.png"}


@pytest.mark.parametrize("index_compressed", [False, True])
def test_xp3_multisegment_and_protected_flag_is_not_encryption(tmp_path, index_compressed):
    original = png()
    (tmp_path / "data.xp3").write_bytes(
        xp3(
            [
                ("image.png", [(original[:19], True), (original[19:], False)]),
                ("startup.tjs", [(b"ignored", True)]),
            ],
            index_compressed,
        )
    )
    sources, warnings = enumerate_assets(tmp_path, "krkr")
    assert not warnings
    source = Source(tmp_path, sources["data.xp3/image.png"])
    assert source.read_bytes() == original
    with source.open() as stream:
        stream.seek(12)
        assert stream.read(25) == original[12:37]
    (tmp_path / "data.xp3").write_bytes(b"changed")
    with pytest.raises(ValueError):
        source.validate()


@settings(max_examples=40, deadline=None)
@given(
    payload=st.binary(min_size=1, max_size=4096), position=st.integers(min_value=0, max_value=4096)
)
def test_xp3_codec_seek_matches_original(payload, position, tmp_path_factory):
    # A fresh directory per example avoids inode/revision assumptions.
    root = tmp_path_factory.mktemp("xp3-property")
    data = b"OggS" + payload
    (root / "data.xp3").write_bytes(xp3([("sound.ogg", [(data[:13], True), (data[13:], True)])]))
    sources, _ = enumerate_assets(root, "krkr")
    with Source(root, sources["data.xp3/sound.ogg"]).open() as stream:
        stream.seek(position)
        assert stream.read(101) == data[position : position + 101]
        stream.seek(0)
        assert stream.read() == data


@pytest.mark.parametrize("name", ["../outside.png", "/absolute.png", "folder/../../escape.png"])
def test_invalid_archive_paths_are_reported_not_published(tmp_path, name):
    (tmp_path / "0.txt").write_text("*define\ngame\n")
    (tmp_path / "00.ns2").write_bytes(ns2([(name, png())]))
    sources, warnings = enumerate_assets(tmp_path, "ons")
    assert not sources
    assert warnings


def test_malformed_archive_does_not_hide_other_sources(tmp_path):
    (tmp_path / "0.txt").write_text("*define\ngame\n")
    (tmp_path / "00.ns2").write_bytes(struct.pack("<I", 99999999))
    (tmp_path / "loose.png").write_bytes(png())
    sources, warnings = enumerate_assets(tmp_path, "ons")
    assert set(sources) == {"loose/loose.png"}
    assert warnings[0]["code"] == "assets.archiveUnsupported"


def test_spb_constant_image(tmp_path):
    # A 1x1 SPB encodes exactly one byte per B/G/R plane.
    module = importlib.import_module("vn_stream.assets.codecs")
    raw = module.spb_decode(b"\x00\x01\x00\x01\x14\x28\x3c", 58)
    with Image.open(BytesIO(raw)) as image:
        assert image.getpixel((0, 0)) == (60, 40, 20)


def test_native_index_uses_shared_tasks_and_keeps_only_media(tmp_path):
    from tests.unit.test_asset_reuse import wait
    from vn_stream.assets.operations import Assets
    from vn_stream.projects.catalog import ProjectCatalog

    root = tmp_path / "games/project"
    root.mkdir(parents=True)
    (root / "data.xp3").write_bytes(
        xp3([("p.png", [(png(), True)]), ("x.ks", [(b"script", True)])])
    )
    library = ProjectCatalog(tmp_path / "data", [tmp_path / "games"])
    project = library.register(root, "Krkr")
    service = Assets(library)
    try:
        result = wait(service, service.extract(project.project_id, "native"))
        assert result["state"] == "completed", result
        summary = service.summary(project.project_id)
        assert summary["categories"] == ["image", "audio", "video"]
        assert summary["counts"]["image"] == 1
        listing = service.list(project.project_id, search="p.png")
        assert listing["total"] == 1
        entry = listing["entries"][0]
        assert entry["archive"] == "data.xp3"
        assert entry["format"] == "PNG"
        assert service.file(project.project_id, entry["id"]).read_bytes() == png()
    finally:
        service.close()


def test_corrupt_compressed_member_is_warning_and_other_members_survive(tmp_path):
    raw = bytearray(xp3([("bad.png", [(png(), True)]), ("good.png", [(png(), False)])]))
    raw[19:24] = b"XXXXX"
    (tmp_path / "data.xp3").write_bytes(raw)
    sources, warnings = enumerate_assets(tmp_path, "krkr")
    assert set(sources) == {"data.xp3/good.png"}
    assert any(w["code"] == "assets.resourceUnsupported" for w in warnings)


def test_raw_native_member_retains_direct_ffmpeg_descriptor(tmp_path):
    (tmp_path / "0.txt").write_text("*define\ngame\n")
    (tmp_path / "00.ns2").write_bytes(ns2([("p.png", png())]))
    sources, _ = enumerate_assets(tmp_path, "ons")
    with Source(tmp_path, sources["00.ns2/p.png"]).ffmpeg(tmp_path) as input:
        assert input.url.startswith("subfile,")
        assert len(input.pass_fds) == 1


def test_lzss_backreference_reproduces_overlapping_history():
    from vn_stream.assets.codecs import lzss_decode

    bits = "1" + f"{65:08b}" + "1" + f"{66:08b}" + "0" + f"{239:08b}" + f"{2:04b}"
    packed = int(bits.ljust((len(bits) + 7) // 8 * 8, "0"), 2).to_bytes((len(bits) + 7) // 8, "big")
    assert b"".join(lzss_decode([packed], 6)) == b"ABABAB"


def test_spb_zigzag_plane_rows():
    from vn_stream.assets.codecs import spb_decode

    # Each plane starts with one sample, then a four-sample literal block.
    bits = ""
    for values in ([20, 21, 22, 23], [40, 41, 42, 43], [60, 61, 62, 63]):
        bits += f"{values[0]:08b}" + "110" + "".join(f"{v:08b}" for v in [*values[1:], 0])
    raw = b"\0\x02\0\x02" + int(bits.ljust((len(bits) + 7) // 8 * 8, "0"), 2).to_bytes(
        (len(bits) + 7) // 8, "big"
    )
    with Image.open(BytesIO(spb_decode(raw, 70))) as image:
        assert list(image.getdata()) == [(60, 40, 20), (61, 41, 21), (63, 43, 23), (62, 42, 22)]


def test_truncated_zlib_cannot_hide_behind_matching_output_length(tmp_path):
    import zlib

    original = png()
    packed = zlib.compress(original)[:-2]
    archive = tmp_path / "x"
    archive.write_bytes(packed)
    descriptor = Source.encoded(
        archive, "x", [dict(offset=0, packed=len(packed), size=len(original), codec="zlib")]
    )
    with pytest.raises(ValueError):
        Source(tmp_path, descriptor).read_bytes()


def test_ogg_video_identification_does_not_get_classified_as_audio():
    from vn_stream.assets.providers import media_type

    assert media_type(b"OggS" + b"\0" * 24 + b"\x80theora", "movie.ogg")["kind"] == "video"
    assert media_type(b"OggS" + b"\0" * 24 + b"OpusHead", "voice.ogv")["kind"] == "audio"


def test_atomic_archive_replacement_cannot_stamp_new_revision_on_old_members(tmp_path):
    from vn_stream.assets.providers import NativeAssets
    from vn_stream.engines.krkr.assets import read_archive

    archive = tmp_path / "data.xp3"
    archive.write_bytes(xp3([("red.png", [(png(), False)])]))

    def replaced(path, inspection):
        for member in read_archive(path, inspection):
            replacement = path.with_suffix(".new")
            replacement.write_bytes(xp3([("blue.png", [(png(), False)])]))
            replacement.replace(path)
            yield member

    sources, warnings = NativeAssets(replaced, {".xp3"}).enumerate(
        tmp_path, inspect_project(tmp_path), lambda: None
    )
    assert "data.xp3/red.png" not in sources
    assert warnings


def test_spb_indexing_reads_only_geometry_not_the_entire_pixel_stream(tmp_path):
    (tmp_path / "0.txt").write_text("*define\ngame\n")
    # Deliberately missing pixels: indexing can identify BMP, preparation rejects it.
    (tmp_path / "arc.nsa").write_bytes(nsa([("large.spb", struct.pack(">HH", 1000, 1000), 0)]))
    sources, warnings = enumerate_assets(tmp_path, "ons")
    assert not warnings
    assert sources["arc.nsa/large.spb"]["media"]["mime"] == "image/bmp"
    with pytest.raises(ValueError):
        Source(tmp_path, sources["arc.nsa/large.spb"]).read_bytes()


@pytest.mark.parametrize(
    "head,kind,mime",
    [
        (b"\x1aE\xdf\xa3" + b"\0webm\0", "video", "video/webm"),
        (b"RIFF" + b"\0" * 4 + b"AVI ", "video", "video/x-msvideo"),
        (b"\0\0\0\x18ftypisom", "video", "video/mp4"),
        (b"\0\0\x01\xba", "video", "video/mpeg"),
        (b"\0\0\0\x18ftypavif", "image", "image/avif"),
    ],
)
def test_container_signatures_override_misleading_file_extensions(head, kind, mime):
    from vn_stream.assets.providers import media_type

    assert media_type(head, "misleading.png")["kind"] == kind
    assert media_type(head, "misleading.png")["mime"] == mime


def test_empty_directories_also_consume_enumeration_budget(tmp_path, monkeypatch):
    from vn_stream.assets import providers

    (tmp_path / "0.txt").write_text("*define\ngame\n")
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(providers, "MAX_ENTRIES", 2)
    with pytest.raises(ValueError):
        enumerate_assets(tmp_path, "ons")
