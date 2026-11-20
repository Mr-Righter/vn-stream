"""Session-owned display handshake; never imports game Python in the service."""

import json
import math
import shutil
import subprocess
import time
from importlib.resources import files
from pathlib import Path

from vn_stream.i18n import text


def canvas_geometry(width: int, height: int) -> tuple[int, int]:
    if any(type(n) is not int or not 1 <= n <= 65536 for n in (width, height)):
        raise ValueError("invalid canvas dimensions")
    scale = min(1, 3840 / max(width, height), 2160 / min(width, height))
    return max(1, math.floor(width * scale)), max(1, math.floor(height * scale))


def apply_display(width: int, height: int) -> tuple[int, int]:
    result = subprocess.run(
        [
            "/lsiopy/bin/python3",
            str(Path(__file__).with_name("selkies_display.py")),
            str(width),
            str(height),
        ],
        capture_output=True,
        text=True,
        timeout=2.5,
        check=True,
    )
    size = json.loads(result.stdout)
    if size != [width, height]:
        raise ValueError("display did not realize requested geometry")
    return tuple(size)


class NativeCanvas:
    def __init__(self, root: Path, session_id: str, *, apply=apply_display, now=None):
        self.directory = root / "native-canvas" / session_id
        self.directory.mkdir(parents=True, mode=0o700)
        self.report = self.directory / "report.json"
        self.ack = self.directory / "ack.json"
        self.session_id = session_id
        self.apply = apply
        self.deadline = (time.monotonic() if now is None else now) + 5
        self.consumed = False
        self.status = {
            "state": "pending",
            "revision": 0,
            "source": None,
            "content": None,
            "capture": None,
            "diagnostic": None,
            "notice": None,
        }

    def environment(self) -> dict:
        search = self.directory / "scripts"
        search.mkdir()
        shutil.copyfile(
            files("vn_stream.engines.renpy.hooks").joinpath("native_canvas.rpy"),
            search / "_stream_canvas.rpy",
        )
        return {
            "RENPY_SEARCHPATH": str(search),
            "VN_STREAM_CANVAS_REPORT": str(self.report),
            "VN_STREAM_CANVAS_ACK": str(self.ack),
            "VN_STREAM_SESSION_ID": self.session_id,
        }

    def _acknowledge(self):
        temporary = self.ack.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps({"session_id": self.session_id, **self.status}))
            temporary.replace(self.ack)
        except OSError:
            # The game hook has its own bounded wait; losing this optional
            # handshake must never tear down a healthy game process.
            self.status["diagnostic"] = "ack_write_failed"
            self.status["notice"] = text(
                "errors.displayHandshakeCouldNotBeCompletedTheGameContinues"
            )

    def _fallback(self, diagnostic):
        self.status.update(
            state="fallback",
            diagnostic=diagnostic,
            notice=text("errors.nativeCanvasUnavailableTemporarilyUsing19201080ItMay"),
        )
        try:
            self.status["capture"] = list(self.apply(1920, 1080))
        except (OSError, ValueError, subprocess.SubprocessError):
            self.status.update(
                capture=None,
                diagnostic=diagnostic + ":fallback_apply_failed",
                notice=text("errors.displayConfigurationCouldNotBeCompletedTheGameContinues"),
            )
        self.status["revision"] += 1
        self._acknowledge()

    def poll(self, now=None) -> bool:
        now = time.monotonic() if now is None else now
        if not self.consumed and self.report.exists():
            self.consumed = True
            try:
                with self.report.open() as file:
                    report = json.loads(file.read(2049))
                if report.get("session_id") != self.session_id:
                    self.consumed = False
                    if self.status["state"] == "pending" and now >= self.deadline:
                        self._fallback("report_timeout")
                        return True
                    return False
                width, height = canvas_geometry(report["width"], report["height"])
                self.status["source"] = [report["width"], report["height"]]
                self.status["content"] = [width, height]
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RecursionError,
                OverflowError,
            ):
                self._fallback("invalid_report")
                return True
            # RandR CVT aligns widths to eight; YUV codecs require even heights.
            # Only the display is padded, never the game coordinate system.
            target = ((width + 7) // 8 * 8, (height + 1) // 2 * 2)
            try:
                actual = self.apply(*target)
                self.status.update(
                    state="native", capture=list(actual), diagnostic=None, notice=None
                )
                self.status["revision"] += 1
                self._acknowledge()
            except (OSError, ValueError, subprocess.SubprocessError):
                self._fallback("apply_failed")
            return True
        if self.status["state"] == "pending" and now >= self.deadline:
            self._fallback("report_timeout")
            return True
        try:
            with (self.directory / "render.json").open() as file:
                rendered = json.loads(file.read(2049))
            if rendered.get("session_id") != self.session_id:
                return False
            physical, viewport = rendered["physical"], rendered["viewport"]
            if len(physical) != 2 or len(viewport) != 4:
                return False
            if any(type(n) is not int or not 1 <= n <= 65536 for n in physical):
                return False
            if any(type(n) not in (int, float) or not math.isfinite(n) for n in viewport):
                return False
            value = {"physical": physical, "viewport": viewport}
            position = rendered.get("position")
            if (
                isinstance(position, list)
                and len(position) == 2
                and all(type(n) is int and abs(n) <= 65536 for n in position)
            ):
                value["position"] = position
            if value != self.status.get("render"):
                self.status["render"] = value
                return True
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RecursionError,
            OverflowError,
        ):
            pass
        return False

    def close(self):
        # Called only after the entire game process group has been reaped.
        shutil.rmtree(self.directory, ignore_errors=True)
