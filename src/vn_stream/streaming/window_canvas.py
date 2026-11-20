"""Observe only windows owned by the supervised game group; no engine injection."""

import json
import os
import re
import subprocess
import time

from vn_stream.i18n import text
from vn_stream.streaming.native_canvas import NativeCanvas, canvas_geometry


def window_geometry(pid):
    """Ignore desktop, launcher and unrelated processes even if they have focus."""

    def run(*args):
        return subprocess.run(args, capture_output=True, text=True, timeout=0.2, check=True).stdout

    windows = run("xprop", "-root", "_NET_CLIENT_LIST")
    deadline = time.monotonic() + 0.6
    for window in re.findall(r"0x[0-9a-fA-F]+", windows)[:64]:
        if time.monotonic() >= deadline:
            break
        try:
            value = run("xprop", "-id", window, "_NET_WM_PID")
        except subprocess.SubprocessError:
            continue
        match = re.search(r"=\s*(\d+)", value)
        if not match:
            continue
        try:
            if os.getpgid(int(match[1])) != pid:
                continue
        except ProcessLookupError:
            continue
        info = run("xwininfo", "-id", window)
        if "IsViewable" not in info:
            continue
        width, height = (
            int(re.search(r"\b" + field + r":\s*(\d+)", info)[1]) for field in ("Width", "Height")
        )
        if width >= 64 and height >= 64:
            return window, width, height
    return None


class WindowCanvas(NativeCanvas):
    def __init__(self, root, session_id, canvas=None, **kwargs):
        super().__init__(root, session_id, **kwargs)
        self.pid = None
        self.window = None
        self.next_probe = 0
        self.observed = False
        self.canvas = canvas
        self.status["ready"] = False

    def environment(self):
        # A configured script canvas is a display hint, not a claim of a ready frame.
        if self.canvas:
            try:
                width, height = canvas_geometry(*self.canvas)
                self.apply((width + 7) // 8 * 8, (height + 1) // 2 * 2)
            except (ValueError, OSError, subprocess.SubprocessError):
                self.status["diagnostic"] = "initial_display_failed"
        return {}

    def _fallback(self, diagnostic):
        super()._fallback(diagnostic)
        if diagnostic == "report_timeout":
            self.status["notice"] = text("engine.windowWaiting")
            self._acknowledge()

    def poll(self, now=None):
        now = time.monotonic() if now is None else now
        if self.pid and now >= self.next_probe:
            self.next_probe = now + 1.0
            try:
                found = window_geometry(self.pid)
                if found:
                    window, width, height = found
                    if (
                        self.observed
                        and self.window == window
                        and [width, height] == self.status.get("content")
                        and self.status["ready"]
                        and self.status["state"] == "native"
                    ):
                        return super().poll(now)
                    source = self.canvas or (width, height)
                    self.consumed = False
                    self.report.write_text(
                        json.dumps(
                            dict(session_id=self.session_id, width=source[0], height=source[1])
                        )
                    )
                    changed = super().poll(now)
                    if self.status["state"] != "native":
                        self.status["ready"] = False
                        return changed
                    content = self.status.get("content")
                    if content:
                        subprocess.run(
                            [
                                "xdotool",
                                "windowmove",
                                window,
                                "0",
                                "0",
                                "windowsize",
                                window,
                                *map(str, content),
                            ],
                            capture_output=True,
                            timeout=0.8,
                            check=True,
                        )
                        measured = window_geometry(self.pid)
                        if measured != (window, *content):
                            self.status.update(ready=False, diagnostic="window_resize_pending")
                            return True
                        self.status["render"] = dict(
                            physical=content, viewport=[0, 0, *content], position=[0, 0]
                        )
                        self.status["ready"] = True
                        self.status["measurement"] = "window_observed"
                        self.window, self.observed = window, True
                    return changed
                if self.observed and self.status["ready"]:
                    self.status.update(
                        ready=False,
                        diagnostic="window_unavailable",
                        notice=text("engine.windowWaiting"),
                    )
                    return True
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                previous = self.status.copy()
                self.status.update(
                    ready=False,
                    diagnostic="window_probe_failed",
                    notice=text("engine.windowWaiting"),
                )
                # Preserve the initial fallback deadline and publish readiness loss.
                changed = super().poll(now)
                return changed or self.status != previous
        return super().poll(now)
