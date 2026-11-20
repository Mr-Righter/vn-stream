"""Opaque login token persistence and credential revocation policy."""

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from vn_stream.i18n import text


class AuthStore:
    def __init__(self, state: Path, username: str, password: str):
        state.mkdir(parents=True, exist_ok=True)
        keyfile = state / "auth.key"
        try:
            with keyfile.open("xb") as f:
                keyfile.chmod(0o600)
                f.write(secrets.token_bytes(32))
        except FileExistsError:
            pass
        key = keyfile.read_bytes()
        if len(key) != 32:
            raise ValueError(text("errors.theLoginSessionKeyIsDamaged"))
        keyfile.chmod(0o600)
        self.path = state / "auth.sqlite3"
        self.username, self.password = username, password
        self.marker = hmac.new(
            key, (username + "\0" + password).encode(), hashlib.sha256
        ).hexdigest()
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS auth_sessions (token_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL, expires_at REAL NOT NULL, credential_marker TEXT NOT NULL, remembered INTEGER NOT NULL)"
            )
            db.execute(
                "DELETE FROM auth_sessions WHERE credential_marker != ? OR expires_at <= ?",
                (self.marker, time.time()),
            )
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def valid_credentials(self, username, password):
        return secrets.compare_digest(
            username.encode(), self.username.encode()
        ) & secrets.compare_digest(password.encode(), self.password.encode())

    def lookup(self, token):
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM auth_sessions WHERE token_hash = ? AND credential_marker = ? AND expires_at > ?",
                (self.digest(token), self.marker, time.time()),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def revoke(self, token):
        if token:
            with self.connect() as db:
                db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (self.digest(token),))

    def issue(self, remember):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expiry = time.time() + (30 * 86400 if remember else 12 * 3600)
        with self.connect() as db:
            db.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (time.time(),))
            db.execute(
                "INSERT INTO auth_sessions VALUES (?, ?, ?, ?, ?)",
                (self.digest(token), csrf, expiry, self.marker, int(remember)),
            )
        return token, {"csrf_token": csrf, "expires_at": expiry, "remembered": remember}
