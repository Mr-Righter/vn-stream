"""Session-isolated, idempotent native actions. The service never imports game Python."""

import json
import re
import shutil
import time
from importlib.resources import files

from vn_stream.sessions.presence import media_owner

ACTIONS = frozenset(("rollback", "skip", "save", "load", "menu", "quit"))


class ActionQueue:
    def __init__(self, state, session_id, media, *, clock=time.monotonic):
        self.directory = state / "game-actions" / session_id
        self.session_id, self.media, self.clock = session_id, media, clock

    def environment(self, search=None):
        search = search or self.directory / "scripts"
        search.mkdir(parents=True, exist_ok=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        for name in ("commands", "requests"):
            (self.directory / name).mkdir(exist_ok=True)
        shutil.copyfile(
            files("vn_stream.engines.renpy.hooks").joinpath("native_actions.rpy"),
            search / "_stream_actions.rpy",
        )
        shutil.copyfile(
            files("vn_stream.engines.renpy.hooks").joinpath("save_edits.rpy"),
            search / "_stream_save_edits.rpy",
        )
        return {
            "RENPY_SEARCHPATH": str(search),
            "VN_STREAM_ACTIONS": str(self.directory),
            "VN_STREAM_MEDIA_OWNER": str(self.media),
            "VN_STREAM_SESSION_ID": self.session_id,
        }

    @staticmethod
    def write(path, value):
        temporary = path.with_suffix(".pending")
        temporary.write_text(json.dumps(value))
        temporary.replace(path)

    def status(self):
        try:
            return json.loads((self.directory / "status.json").read_text())
        except (OSError, ValueError):
            return {"ready": False, "skip": False, "available": []}

    def result(self, request_id):
        if not re.fullmatch("[a-f0-9]{32}", request_id):
            return None
        try:
            return json.loads((self.directory / "requests" / (request_id + ".json")).read_text())
        except (OSError, ValueError):
            return None

    def submit(self, action, request_id, connection, epoch):
        rejected = {"request_id": request_id, "state": "rejected"}
        if action not in ACTIONS or not re.fullmatch("[a-f0-9]{32}", request_id):
            return {**rejected, "reason": "invalid_action"}
        owner = media_owner(self.media, self.session_id, self.clock())
        if not owner or (owner.get("connection_id"), owner.get("epoch")) != (connection, epoch):
            return {**rejected, "reason": "not_controller"}
        requests = self.directory / "requests"
        commands = self.directory / "commands"
        requests.mkdir(parents=True, exist_ok=True)
        commands.mkdir(exist_ok=True)
        record = requests / (request_id + ".json")
        if record.exists():
            return json.loads(record.read_text())
        if len(list(commands.iterdir())) >= 32 or len(list(requests.iterdir())) >= 4096:
            return {**rejected, "reason": "queue_full"}
        value = dict(
            request_id=request_id,
            action=action,
            session_id=self.session_id,
            connection_id=connection,
            epoch=epoch,
            state="accepted",
        )
        self.write(record, value)
        self.write(commands / (str(time.time_ns()) + "-" + request_id + ".json"), value)
        return value
