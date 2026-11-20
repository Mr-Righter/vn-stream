"""Opt-in compilation against the fixed upstream Next C ABI, without stubs."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("VN_STREAM_NEXT_HOST_TEST") != "1",
    reason="requires the pinned Next checkout and native builder image",
)


def test_linux_host_compiles_against_real_engine_api():
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/bash",
            "-v",
            str(root) + ":/workspace:ro",
            "vn-stream:zero-builder",
            "-lc",
            "g++ -std=c++17 -Wall -Wextra -Werror -fsyntax-only "
            "$(pkg-config --cflags sdl2 gtk+-3.0) "
            "-I/workspace/var/runtime-candidates/sources/krkr-next/bridge/engine_api/include "
            "/workspace/containers/native-build/krkr-next/main.cpp",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
