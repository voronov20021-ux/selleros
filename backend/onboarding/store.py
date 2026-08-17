"""Persistent seller profiles + encrypted WB credentials (same SQLite as auth)."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.onboarding.models import OnboardingStatus, parse_status
from backend.onboarding.secrets import decrypt_secret, encrypt_secret

log = logging.getLogger("selleros.onboarding.store")

ONBOARDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS seller_profiles (
    seller_id TEXT PRIMARY KEY,
    telegram_user_id TEXT NOT NULL,
    display_name TEXT,
    onboarding_status TEXT NOT NULL DEFAULT 'NEW',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seller_profiles_tg
    ON seller_profiles(telegram_user_id);

CREATE TABLE IF NOT EXISTS seller_wb_credentials (
    seller_id TEXT PRIMARY KEY,
    api_key_encrypted TEXT NOT NULL,
    connected_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (seller_id) REFERENCES seller_profiles(seller_id)
);
"""


@dataclass
class SellerProfile:
    seller_id: str
    telegram_user_id: str
    display_name: str
    onboarding_status: OnboardingStatus
    created_at: float
    updated_at: float

    @property
    def status(self) -> OnboardingStatus:
        return self.onboarding_status


class OnboardingStore:
    """SQLite store for onboarding — shares MEMORY_DB_PATH with auth_sessions."""

    def __init__(self, *, db_path: Optional[str] = None):
        if db_path is None:
            from backend import config

            db_path = config.MEMORY_DB_PATH
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(ONBOARDING_SCHEMA)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------ profile

    def get_profile(self, seller_id: str) -> Optional[SellerProfile]:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT * FROM seller_profiles WHERE seller_id = ?",
                    (str(seller_id),),
                )
                row = cur.fetchone()
                return self._row_to_profile(row) if row else None
            finally:
                conn.close()

    def ensure_profile(
        self,
        *,
        seller_id: str,
        telegram_user_id: str,
        display_name: str,
    ) -> SellerProfile:
        """Idempotent: create NEW profile once; never overwrite seller_id."""
        seller_id = str(seller_id)
        telegram_user_id = str(telegram_user_id)
        existing = self.get_profile(seller_id)
        if existing is not None:
            # Refresh display_name if empty / changed lightly
            if display_name and display_name != existing.display_name:
                self._update_display_name(seller_id, display_name)
                existing.display_name = display_name
            return existing

        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                # Race-safe: UNIQUE seller_id
                conn.execute(
                    """
                    INSERT OR IGNORE INTO seller_profiles (
                        seller_id, telegram_user_id, display_name,
                        onboarding_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        seller_id,
                        telegram_user_id,
                        display_name or f"seller_{seller_id}",
                        OnboardingStatus.NEW.value,
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        profile = self.get_profile(seller_id)
        assert profile is not None
        return profile

    def set_status(self, seller_id: str, status: OnboardingStatus) -> SellerProfile:
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE seller_profiles
                    SET onboarding_status = ?, updated_at = ?
                    WHERE seller_id = ?
                    """,
                    (status.value, now, str(seller_id)),
                )
                conn.commit()
            finally:
                conn.close()
        profile = self.get_profile(seller_id)
        if profile is None:
            raise KeyError(f"seller profile missing: {seller_id}")
        return profile

    def _update_display_name(self, seller_id: str, display_name: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE seller_profiles
                    SET display_name = ?, updated_at = ?
                    WHERE seller_id = ?
                    """,
                    (display_name, time.time(), str(seller_id)),
                )
                conn.commit()
            finally:
                conn.close()

    # -------------------------------------------------------------- credentials

    def save_wb_credentials(self, seller_id: str, api_key: str) -> None:
        """Encrypt and upsert WB API key. Never stores plaintext."""
        enc = encrypt_secret(api_key)
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO seller_wb_credentials (
                        seller_id, api_key_encrypted, connected_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(seller_id) DO UPDATE SET
                        api_key_encrypted = excluded.api_key_encrypted,
                        updated_at = excluded.updated_at
                    """,
                    (str(seller_id), enc, now, now),
                )
                conn.commit()
            finally:
                conn.close()

    def has_wb_credentials(self, seller_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT 1 FROM seller_wb_credentials WHERE seller_id = ?",
                    (str(seller_id),),
                )
                return cur.fetchone() is not None
            finally:
                conn.close()

    def get_wb_api_key(self, seller_id: str) -> Optional[str]:
        """Decrypt stored key for server-side WB check only. Never expose to clients."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT api_key_encrypted FROM seller_wb_credentials WHERE seller_id = ?",
                    (str(seller_id),),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return decrypt_secret(row["api_key_encrypted"])
            finally:
                conn.close()

    def delete_wb_credentials(self, seller_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM seller_wb_credentials WHERE seller_id = ?",
                    (str(seller_id),),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def encrypted_blob_for_tests(self, seller_id: str) -> Optional[str]:
        """Return ciphertext only (tests assert plaintext absent)."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT api_key_encrypted FROM seller_wb_credentials WHERE seller_id = ?",
                    (str(seller_id),),
                )
                row = cur.fetchone()
                return row["api_key_encrypted"] if row else None
            finally:
                conn.close()

    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> SellerProfile:
        return SellerProfile(
            seller_id=str(row["seller_id"]),
            telegram_user_id=str(row["telegram_user_id"]),
            display_name=row["display_name"] or f"seller_{row['seller_id']}",
            onboarding_status=parse_status(row["onboarding_status"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )
