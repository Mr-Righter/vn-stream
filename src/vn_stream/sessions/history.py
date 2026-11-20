"""Best-effort durable accounting; statistics must never own the game lifetime."""

import sqlite3
import sys
from pathlib import Path


def database(state: Path):
    db = sqlite3.connect(state / "play-history.sqlite3", timeout=0.2)
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS plays (session_id TEXT PRIMARY KEY, project_id TEXT, started REAL, ended REAL, seconds REAL)"
        )
    except sqlite3.Error:
        db.close()
        raise
    return db


def record_session(
    state: Path,
    session_id: str,
    project_id: str,
    started: float,
    seconds: float,
    ended: float | None = None,
):
    db = None
    try:
        db = database(state)
        with db:
            db.execute(
                "INSERT INTO plays VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET seconds=excluded.seconds, ended=excluded.ended",
                (session_id, project_id, started, ended, seconds),
            )
    except (sqlite3.Error, OSError) as error:
        # stdout is the supervisor handshake. Diagnostics belong in the game log.
        print(f"Play statistics unavailable: {error}", file=sys.stderr)
    finally:
        if db is not None:
            db.close()


def summary(state: Path, project_id: str) -> dict:
    db = None
    try:
        db = database(state)
        last, seconds = db.execute(
            "SELECT MAX(started), COALESCE(SUM(seconds),0) FROM plays WHERE project_id=?",
            (project_id,),
        ).fetchone()
        return {"last_played_at": last, "play_seconds": seconds}
    except (sqlite3.Error, OSError):
        return {"last_played_at": None, "play_seconds": None}
    finally:
        if db is not None:
            db.close()
