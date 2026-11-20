"""Verify legacy copies before publishing references and reclaiming duplicate bytes."""

import logging
import time

from vn_stream.assets.sources import Source, revision
from vn_stream.projects.locations import project
from vn_stream.storage.files import atomic_json, safe

LOG = logging.getLogger(__name__)


def migrate(assets, uid, project_id, game):
    root, previous = assets._snapshot(project_id)
    sources, warnings, _ = assets._sources(game)
    entries, reclaim = [], []
    assets._update(
        uid,
        phase="indexing",
        total_files=len(previous),
        total_bytes=sum(e["size_bytes"] for e in previous),
    )
    done = 0
    for old in previous:
        assets._check_task(uid)
        entry = dict(old)
        copy = safe(root, "files/" + entry["path"])
        source_record = sources.get(entry["path"])
        if copy.is_file() and source_record:
            try:
                source = Source(game, source_record)
                if source.size != copy.stat().st_size:
                    raise ValueError("Different size")
                copy_revision = revision(copy.stat())
                with (
                    source.open() as incoming,
                    Source(root, Source.describe(copy, "files/" + entry["path"])).open() as cached,
                ):
                    while piece := incoming.read(1024**2):
                        assets._check_task(uid)
                        if piece != cached.read(len(piece)):
                            raise ValueError("Different resource")
                source.validate()
                if copy_revision != revision(copy.stat()):
                    raise ValueError("Legacy copy changed")
                entry.update(source=source_record, retire=copy_revision)
            except (OSError, ValueError):
                entry.pop("source", None)
                entry.pop("retire", None)
        elif entry.get("source"):
            # A previous publication may have completed before cleanup or service shutdown.
            try:
                Source(game, entry["source"]).validate()
            except (OSError, ValueError):
                if copy.is_file():
                    entry.pop("source", None)
                    entry.pop("retire", None)
                else:
                    raise
        if entry.get("source") and entry.get("retire"):
            reclaim.append(entry)
        entries.append(entry)
        done += entry["size_bytes"]
        assets._update(uid, completed_bytes=done, completed_files=len(entries))
    retained = [e for e in entries if not e.get("source") or e["source"].get("retained")]
    with assets.library.lock:
        project(assets.library, project_id)
        assets._check_task(uid)
        current, _ = assets._snapshot(project_id)
        if current != root:
            raise ValueError("Retired snapshot")
        # Publish locators durably before any old copy is removed.
        atomic_json(root / "index.json", entries)
        info = assets.summary(project_id)
        info.pop("job", None)
        info.pop("ready", None)
        info.update(
            storage="reference-v1",
            retained_files=len(retained),
            retained_bytes=sum(e["size_bytes"] for e in retained),
            migrated_at=time.time(),
        )
        atomic_json(root.parent / "current.json", info)
        released = 0
        for entry in reclaim:
            copy = safe(root, "files/" + entry["path"])
            try:
                Source(game, entry["source"]).validate()
                if copy.is_file() and revision(copy.stat()) == entry["retire"]:
                    copy.unlink()
                    released += entry["size_bytes"]
            except (OSError, ValueError):
                LOG.warning(
                    "Legacy duplicate cleanup deferred: game=%s asset=%s", project_id, entry["id"]
                )
        assets._update(
            uid,
            state="completed",
            phase="completed",
            warnings=len(warnings) + len(retained),
            released_bytes=released,
            retained_files=len(retained),
            retained_bytes=info["retained_bytes"],
        )
