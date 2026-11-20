"""Bounded video poster queue; cache publication must never resurrect a project."""

import json
import math
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from vn_stream.assets.sources import decoder_input
from vn_stream.storage.files import safe


class VideoThumbnails:
    def __init__(self, assets, formats):
        self.assets, self.formats = assets, formats
        self.queue = queue.Queue(maxsize=32)
        self.lock = threading.RLock()
        self.pending = {}
        self.stopping = threading.Event()
        self.thread = None
        self.process = None

    def request(self, project_id, uid):
        with self.assets.library.lock:
            root, _ = self.assets.entry(project_id, uid)
            source = self.assets.file(project_id, uid)
            cache = safe(root, "thumbnails/" + uid + ".jpg")
            if cache.is_file():
                return cache
        key = (project_id, root.name, uid)
        with self.lock:
            old = self.pending.get(key)
            if old and (old["state"] != "failed" or time.monotonic() - old["at"] < 60):
                return dict(state=old["state"])
            if self.stopping.is_set() or self.queue.full():
                return dict(state="queued")
            self.pending[key] = dict(state="queued", at=time.monotonic())
            self.queue.put_nowait((key, root, source, cache))
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
        return dict(state="queued")

    def _run(self):
        while not self.stopping.is_set():
            try:
                key, root, source, cache = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            temporary = None
            try:
                with self.lock:
                    self.pending[key]["state"] = "running"
                with tempfile.NamedTemporaryFile(
                    suffix=".jpg", dir=self.assets.library.data / "tmp", delete=False
                ) as f:
                    temporary = Path(f.name)
                self.render(source, temporary)
                with self.assets.library.lock:
                    current, _ = self.assets.entry(key[0], key[2])
                    if self.stopping.is_set() or current != root:
                        raise FileNotFoundError("Retired thumbnail generation")
                    source.validate()
                    safe(root, "thumbnails").mkdir(exist_ok=True)
                    # The temporary output and NAS may be on different filesystems.
                    target = safe(root, "thumbnails/" + key[2] + ".pending")
                    try:
                        with temporary.open("rb") as source_file, target.open("wb") as output:
                            output.write(source_file.read(2 * 1024**2))
                        os.replace(target, cache)
                    finally:
                        target.unlink(missing_ok=True)
                with self.lock:
                    self.pending.pop(key, None)
            except Exception:
                with self.lock:
                    self.pending[key] = dict(state="failed", at=time.monotonic())
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
                self.queue.task_done()

    def render(self, source, output):
        with decoder_input(source, self.assets.library.data / "tmp") as input_file:
            self._render(input_file, output)

    def _render(self, source, output):
        started = time.monotonic()
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,subfile",
                "-format_whitelist",
                self.formats,
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                source.url,
            ],
            capture_output=True,
            pass_fds=source.pass_fds,
            check=True,
            timeout=5,
        )
        try:
            duration = float(json.loads(probe.stdout).get("format", {}).get("duration", 0))
        except (ValueError, TypeError):
            duration = 0
        offset = min(1, duration / 2) if math.isfinite(duration) and duration > 0 else 0
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file,subfile",
            "-format_whitelist",
            self.formats,
            "-ss",
            str(offset),
            "-i",
            source.url,
            "-frames:v",
            "1",
            "-an",
            "-vf",
            "scale=320:320:force_original_aspect_ratio=decrease",
            "-threads",
            "1",
            "-q:v",
            "4",
            str(output),
        ]
        with self.lock:
            if self.stopping.is_set():
                raise InterruptedError
            self.process = subprocess.Popen(
                command,
                pass_fds=source.pass_fds,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            process = self.process
        try:
            if process.wait(timeout=max(0.1, 15 - (time.monotonic() - started))) != 0:
                raise ValueError("Poster decoding failed")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            with self.lock:
                self.process = None
        if not 0 < output.stat().st_size <= 2 * 1024**2:
            raise ValueError("Invalid poster output")

    def close(self):
        self.stopping.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
        if self.thread:
            self.thread.join()
