"""Opaque server-side session tokens mapped to seller identity.

Sessions persist in the Seller OS SQLite DB (MEMORY_DB_PATH). Only a
SHA-256 hash of the client token is stored — never the plaintext token.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("selleros.auth.session")

AUTH_SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    seller_id TEXT NOT NULL,
    telegram_user_id TEXT NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    revoked_at REAL,
    last_seen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_seller
    ON auth_sessions(seller_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires
    ON auth_sessions(expires_at);
"""


def hash_token(token: str) -> str:
    """SHA-256 hex digest of the opaque client token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class SellerSession:
    token: str
    seller_id: str  # = telegram_user_id
    telegram_user_id: str
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    last_seen_at: float = field(default_factory=time.time)
    revoked_at: Optional[float] = None
    is_new_seller: bool = False
    token_hash: str = ""

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return " ".join(parts)
        if self.username:
            return f"@{self.username}"
        return f"seller_{self.seller_id}"


class SessionStore:
    """
    Persistent session store (SQLite).

    seller_id == telegram_user_id (string). First successful auth creates
    the seller identity implicitly (no separate sellers table).

    Client receives a cryptographically random token; only token_hash is
    stored. FastAPI restarts do not invalidate sessions.
    """

    def __init__(
        self,
        *,
        db_path: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ):
        if db_path is None or ttl_seconds is None:
            from backend import config

            if db_path is None:
                db_path = config.MEMORY_DB_PATH
            if ttl_seconds is None:
                ttl_seconds = config.AUTH_SESSION_TTL_SECONDS

        self.db_path = str(db_path)
        self._ttl = max(60, int(ttl_seconds))
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(AUTH_SESSIONS_SCHEMA)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------ API

    def create_session(
        self,
        *,
        telegram_user_id: str,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> SellerSession:
        seller_id = str(telegram_user_id)
        now = time.time()
        expires_at = now + self._ttl
        token = secrets.token_urlsafe(32)
        token_hash = hash_token(token)

        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT 1 FROM auth_sessions WHERE seller_id = ? LIMIT 1",
                    (seller_id,),
                )
                is_new = cur.fetchone() is None

                conn.execute(
                    """
                    INSERT INTO auth_sessions (
                        token_hash, seller_id, telegram_user_id,
                        username, first_name, last_name,
                        created_at, expires_at, revoked_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        token_hash,
                        seller_id,
                        seller_id,
                        username,
                        first_name,
                        last_name,
                        now,
                        expires_at,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        return SellerSession(
            token=token,
            token_hash=token_hash,
            seller_id=seller_id,
            telegram_user_id=seller_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            created_at=now,
            expires_at=expires_at,
            last_seen_at=now,
            is_new_seller=is_new,
        )

    def get_session(self, token: Optional[str]) -> Optional[SellerSession]:
        """Return session if token is known, not expired, and not revoked."""
        if not token:
            return None
        token_hash = hash_token(token)
        now = time.time()

        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT * FROM auth_sessions WHERE token_hash = ?",
                    (token_hash,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                # Constant-time compare of stored hash vs recomputed (defense in depth).
                stored = row["token_hash"] or ""
                if not secrets.compare_digest(stored, token_hash):
                    return None
                if row["revoked_at"] is not None:
                    return None
                if float(row["expires_at"]) <= now:
                    return None
                return self._row_to_session(row, plaintext_token="")
            finally:
                conn.close()

    def touch_session(self, token: Optional[str]) -> Optional[SellerSession]:
        """Update last_seen_at for a valid session; return refreshed session."""
        if not token:
            return None
        session = self.get_session(token)
        if session is None:
            return None
        now = time.time()
        token_hash = hash_token(token)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE auth_sessions
                    SET last_seen_at = ?
                    WHERE token_hash = ?
                      AND revoked_at IS NULL
                      AND expires_at > ?
                    """,
                    (now, token_hash, now),
                )
                conn.commit()
            finally:
                conn.close()

        session.last_seen_at = now
        return session

    def revoke_session(self, token: Optional[str]) -> bool:
        """Mark session revoked. Returns True if a row was updated."""
        if not token:
            return False
        token_hash = hash_token(token)
        now = time.time()

        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = ?
                    WHERE token_hash = ?
                      AND revoked_at IS NULL
                    """,
                    (now, token_hash),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def cleanup_expired_sessions(self) -> int:
        """Delete expired rows (and optionally already-revoked past expiry)."""
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM auth_sessions WHERE expires_at <= ?",
                    (now,),
                )
                conn.commit()
                deleted = cur.rowcount
            finally:
                conn.close()
        if deleted:
            log.info("auth_sessions cleanup removed %s expired row(s)", deleted)
        return int(deleted or 0)

    # ---- backward-compatible aliases (router / deps / older tests) --------

    def create(self, **kwargs) -> SellerSession:
        return self.create_session(**kwargs)

    def get(self, token: Optional[str]) -> Optional[SellerSession]:
        """Validate session and bump last_seen (request path)."""
        return self.touch_session(token)

    def revoke(self, token: str) -> None:
        self.revoke_session(token)

    def clear(self) -> None:
        """Test helper: wipe all sessions in this DB file."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM auth_sessions")
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _row_to_session(row: sqlite3.Row, *, plaintext_token: str) -> SellerSession:
        return SellerSession(
            token=plaintext_token,
            token_hash=row["token_hash"],
            seller_id=str(row["seller_id"]),
            telegram_user_id=str(row["telegram_user_id"]),
            username=row["username"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            last_seen_at=float(row["last_seen_at"]),
            revoked_at=float(row["revoked_at"]) if row["revoked_at"] is not None else None,
        )
