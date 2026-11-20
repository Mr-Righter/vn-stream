import os
import subprocess
import sys
from pathlib import Path


def test_cli_refuses_missing_password(tmp_path):
    environment = {key: value for key, value in os.environ.items() if key != "VN_STREAM_PASSWORD"}
    environment["PYTHONPATH"] = str(Path("src").resolve())
    result = subprocess.run(
        [sys.executable, "-m", "vn_stream", "--data", str(tmp_path)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert "VN_STREAM_PASSWORD" in result.stderr
