"""Opt-in real standalone decoder verification using generated TLG images."""

import io
import os
import struct
import subprocess

import pytest
from PIL import Image

from tests.native_asset_fixtures import tlg5, tlg6, xp3
from tests.unit.test_asset_preparation import prepared
from tests.unit.test_asset_reuse import wait
from vn_stream.assets.operations import Assets
from vn_stream.projects.catalog import ProjectCatalog

TOOL = os.environ.get("VN_STREAM_TLG_TOOL")
pytestmark = pytest.mark.skipif(not TOOL, reason="set VN_STREAM_TLG_TOOL to run native decoder")


@pytest.mark.parametrize("version,wrapped", [(5, False), (5, True), (6, False), (6, True)])
def test_real_tlg_pixels_and_wrapped_data(tmp_path, version, wrapped):
    raw = tlg5() if version == 5 else tlg6()
    if wrapped:
        raw = b"TLG0.0\0sds\x1a" + struct.pack("<I", len(raw)) + raw
    source = tmp_path / "image.tlg"
    target = tmp_path / "image.png"
    source.write_bytes(raw)
    result = subprocess.run([TOOL, str(source), str(target)], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode()
    with Image.open(target) as image:
        assert image.convert("RGBA").getpixel((0, 0)) == (
            (60, 40, 20, 128) if version == 5 else (0, 0, 0, 255)
        )


def test_tlg_rejects_geometry_before_allocating(tmp_path):
    raw = bytearray(tlg5())
    struct.pack_into("<II", raw, 12, 100000, 100000)
    source = tmp_path / "image.tlg"
    source.write_bytes(raw)
    output = tmp_path / "output.png"
    result = subprocess.run([TOOL, str(source), str(output)], capture_output=True, timeout=15)
    assert result.returncode != 0
    assert not output.exists()


def test_tlg_snapshot_preview_and_original_remain_separate(tmp_path):
    root = tmp_path / "games/tlg"
    root.mkdir(parents=True)
    raw = tlg5()
    (root / "data.xp3").write_bytes(xp3([("face.tlg", [(raw, True)])]))
    lib = ProjectCatalog(tmp_path / "data", [tmp_path / "games"])
    game = lib.register(root, "TLG")
    service = Assets(lib)
    try:
        assert wait(service, service.extract(game.project_id, "index"))["state"] == "completed"
        entry = service.list(game.project_id, search="face")["entries"][0]
        assert prepared(service, game, entry, "preview")["state"] == "completed"
        with Image.open(
            io.BytesIO(service.prepared_file(game.project_id, entry["id"], "preview").read_bytes())
        ) as image:
            assert image.getpixel((0, 0)) == (60, 40, 20, 128)
        assert prepared(service, game, entry)["state"] == "completed"
        assert service.prepared_file(game.project_id, entry["id"], "original").read_bytes() == raw
    finally:
        service.close()
