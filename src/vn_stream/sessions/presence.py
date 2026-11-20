"""Supervisor-owned lease: only application heartbeats on the real media owner count."""

import json
from pathlib import Path


def media_owner(path: Path, session_id: str, now: float):
    try:
        value = json.loads(path.read_text())
        if (
            value.get("session_id") == session_id
            and value.get("connected") is True
            and 0 <= now - value["at"] <= 3
        ):
            return value
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


class Presence:
    def __init__(self, session_id, media, *, now, timeout=60):
        self.session_id, self.media = session_id, media
        self.timeout = timeout
        self.deadline = now + timeout

    def heartbeat(self, connection, epoch, now):
        owner = media_owner(self.media, self.session_id, now)
        if not owner or owner.get("connection_id") != connection or owner.get("epoch") != epoch:
            return False
        self.deadline = now + self.timeout
        return True

    def expired(self, now):
        return now >= self.deadline
