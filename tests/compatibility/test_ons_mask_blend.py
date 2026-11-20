"""Opt-in native SIMD regression against the pinned engine's actual blend kernel."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    not os.environ.get("VN_STREAM_ZERO_SOURCE"),
    reason="requires pinned zero sources and Docker builder",
)
def test_mask_blend_accepts_ordinary_heap_alignment(tmp_path):
    source = Path(os.environ["VN_STREAM_ZERO_SOURCE"]).resolve()
    root = Path(__file__).resolve().parents[2]
    content = (source / "src/onscripter/ONScripter_image.cpp").read_text()
    start = content.index(
        "#ifdef USE_SIMD\n#ifdef USE_SIMD_X86_AVX2\nstatic void alphaBlend8Core32"
    )
    end = content.index("inline static void alphaBlendConst32", start)
    (tmp_path / "blend-kernel.inc").write_text(content[start:end])
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{source}:/source:ro",
            "-v",
            f"{root / 'tests/fixtures/ons-mask-blend'}:/fixture:ro",
            "-v",
            f"{tmp_path}:/out",
            "--entrypoint",
            "sh",
            os.environ.get("VN_STREAM_ZERO_BUILDER", "vn-stream:zero-builder"),
            "-c",
            "g++ -std=c++17 -O2 -mavx2 -DUSE_SIMD -I/usr/include/SDL2 -I/source/src -I/out "
            "/fixture/mask-blend.cpp -o /out/test-mask && /out/test-mask",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
