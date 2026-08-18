"""Mini App prefs / missions / push — additive SQLite, separate from onboarding secrets."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional


def _row_get(row: sqlite3.Row, key: str, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_schedule(raw: Any) -> dict[str, Any]:
    return _merge_schedule(DEFAULT_SCHEDULE, _parse_json_dict(raw))


def _is_bool_schedule_key(key: str) -> bool:
    return (
        key.endswith("_enabled")
        or key == "critical_enabled"
        or key.startswith("notify_")
    )


def _merge_schedule(base: Any, incoming: Any) -> dict[str, Any]:
    out = dict(DEFAULT_SCHEDULE)
    if isinstance(base, dict):
        out.update({k: base[k] for k in DEFAULT_SCHEDULE if k in base})
    incoming = incoming if isinstance(incoming, dict) else {}
    for key in DEFAULT_SCHEDULE:
        if key not in incoming:
            continue
        if _is_bool_schedule_key(key):
            out[key] = bool(incoming[key])
        elif key.endswith("_time"):
            val = str(incoming[key] or "").strip()
            if len(val) >= 4:
                out[key] = val[:5]
    # Contract names ↔ existing TimeService / ActionScheduler keys.
    if "notify_event" in incoming:
        out["critical_enabled"] = bool(out["notify_event"])
    elif "critical_enabled" in incoming:
        out["notify_event"] = bool(out["critical_enabled"])
    else:
        out["notify_event"] = bool(out.get("notify_event", out.get("critical_enabled", True)))
        out["critical_enabled"] = bool(out["notify_event"])
    if "notify_action_check" in incoming:
        out["action_check_enabled"] = bool(out["notify_action_check"])
    elif "action_check_enabled" in incoming:
        out["notify_action_check"] = bool(out["action_check_enabled"])
    else:
        out["notify_action_check"] = bool(
            out.get("notify_action_check", out.get("action_check_enabled", False))
        )
        out["action_check_enabled"] = bool(out["notify_action_check"])
    if "notify_reengagement" not in incoming:
        out["notify_reengagement"] = bool(out.get("notify_reengagement", False))
    return out

SCHEMA = """
CREATE TABLE IF NOT EXISTS seller_miniapp_prefs (
    seller_id TEXT PRIMARY KEY,
    entity TEXT,
    marketplaces TEXT,
    category TEXT,
    missions_json TEXT,
    tz TEXT DEFAULT 'Europe/Moscow',
    push_enabled INTEGER NOT NULL DEFAULT 0,
    reminder_hour INTEGER NOT NULL DEFAULT 10,
    sticky_article INTEGER,
    overlay_skipped INTEGER NOT NULL DEFAULT 0,
    display_name TEXT,
    schedule_json TEXT,
    catalog_meta_json TEXT,
    updated_at REAL NOT NULL
);
"""

DEFAULT_SCHEDULE = {
    "morning_enabled": False,
    "morning_time": "09:00",
    "evening_enabled": False,
    "evening_time": "20:00",
    "action_check_enabled": False,
    "action_check_time": "10:00",
    "critical_enabled": True,
    "notify_event": True,
    "notify_action_check": False,
    "notify_reengagement": False,
}

_EXTRA_COLS = (
    ("display_name", "TEXT"),
    ("schedule_json", "TEXT"),
    ("catalog_meta_json", "TEXT"),
    ("notify_state_json", "TEXT"),
)

DEFAULT_MISSIONS = (
    "profile",
    "wb_connect",
    "first_product",
    "ctr_lesson",
    "first_analysis",
    "first_action",
    "dashboard_ready",
)


class MiniAppStore:
    def __init__(self, *, db_path: Optional[str] = None):
        if db_path is None:
            from backend import config

            db_path = config.MEMORY_DB_PATH
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(SCHEMA)
                existing = {
                    row[1] for row in conn.execute("PRAGMA table_info(seller_miniapp_prefs)")
                }
                for name, decl in _EXTRA_COLS:
                    if name not in existing:
                        conn.execute(
                            f"ALTER TABLE seller_miniapp_prefs ADD COLUMN {name} {decl}"
                        )
                conn.commit()
            finally:
                conn.close()

    def _blank(self, seller_id: str) -> dict[str, Any]:
        return {
            "seller_id": str(seller_id),
            "entity": None,
            "marketplaces": [],
            "category": None,
            "missions": {k: False for k in DEFAULT_MISSIONS},
            "tz": "Europe/Moscow",
            "push_enabled": False,
            "reminder_hour": 10,
            "sticky_article": None,
            "overlay_skipped": False,
            "display_name": None,
            "schedule": dict(DEFAULT_SCHEDULE),
            "catalog_meta": {},
            "notify_state": {},
        }

    def get(self, seller_id: str) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT * FROM seller_miniapp_prefs WHERE seller_id = ?",
                    (str(seller_id),),
                )
                row = cur.fetchone()
            finally:
                conn.close()
        if row is None:
            return self._blank(seller_id)
        missions = {k: False for k in DEFAULT_MISSIONS}
        try:
            raw = json.loads(row["missions_json"] or "{}")
            if isinstance(raw, dict):
                missions.update({k: bool(raw.get(k)) for k in DEFAULT_MISSIONS})
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        markets = []
        try:
            parsed = json.loads(row["marketplaces"] or "[]")
            if isinstance(parsed, list):
                markets = [str(x) for x in parsed if x]
            elif isinstance(parsed, str) and parsed:
                markets = [parsed]
        except (TypeError, ValueError, json.JSONDecodeError):
            if row["marketplaces"]:
                markets = [str(row["marketplaces"])]
        return {
            "seller_id": str(row["seller_id"]),
            "entity": row["entity"],
            "marketplaces": markets,
            "category": row["category"],
            "missions": missions,
            "tz": row["tz"] or "Europe/Moscow",
            "push_enabled": bool(row["push_enabled"]),
            "reminder_hour": int(row["reminder_hour"] or 10),
            "sticky_article": int(row["sticky_article"]) if row["sticky_article"] else None,
            "overlay_skipped": bool(row["overlay_skipped"]),
            "display_name": _row_get(row, "display_name"),
            "schedule": _parse_schedule(_row_get(row, "schedule_json")),
            "catalog_meta": _parse_json_dict(_row_get(row, "catalog_meta_json")),
            "notify_state": _parse_json_dict(_row_get(row, "notify_state_json")),
        }

    def upsert(self, seller_id: str, **fields: Any) -> dict[str, Any]:
        current = self.get(seller_id)
        if "entity" in fields:
            current["entity"] = fields["entity"] or None
        if "marketplaces" in fields:
            val = fields["marketplaces"]
            if isinstance(val, str):
                current["marketplaces"] = [v.strip() for v in val.split(",") if v.strip()]
            elif isinstance(val, list):
                current["marketplaces"] = [str(v) for v in val if v]
        if "category" in fields:
            current["category"] = fields["category"] or None
        if "missions" in fields and isinstance(fields["missions"], dict):
            current["missions"].update(
                {k: bool(fields["missions"].get(k)) for k in DEFAULT_MISSIONS if k in fields["missions"]}
            )
        if "tz" in fields and fields["tz"]:
            current["tz"] = str(fields["tz"])
        if "push_enabled" in fields:
            current["push_enabled"] = bool(fields["push_enabled"])
        if "reminder_hour" in fields and fields["reminder_hour"] is not None:
            hour = int(fields["reminder_hour"])
            current["reminder_hour"] = max(0, min(23, hour))
        if "sticky_article" in fields:
            art = fields["sticky_article"]
            current["sticky_article"] = int(art) if art else None
        if "overlay_skipped" in fields:
            current["overlay_skipped"] = bool(fields["overlay_skipped"])
        if "display_name" in fields:
            name = fields["display_name"]
            current["display_name"] = str(name).strip() if name else None
        if "schedule" in fields and isinstance(fields["schedule"], dict):
            current["schedule"] = _merge_schedule(current.get("schedule"), fields["schedule"])
        if "catalog_meta" in fields and isinstance(fields["catalog_meta"], dict):
            current["catalog_meta"] = fields["catalog_meta"]
        if "notify_state" in fields and isinstance(fields["notify_state"], dict):
            current["notify_state"] = fields["notify_state"]

        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO seller_miniapp_prefs (
                        seller_id, entity, marketplaces, category, missions_json,
                        tz, push_enabled, reminder_hour, sticky_article,
                        overlay_skipped, display_name, schedule_json,
                        catalog_meta_json, notify_state_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(seller_id) DO UPDATE SET
                        entity=excluded.entity,
                        marketplaces=excluded.marketplaces,
                        category=excluded.category,
                        missions_json=excluded.missions_json,
                        tz=excluded.tz,
                        push_enabled=excluded.push_enabled,
                        reminder_hour=excluded.reminder_hour,
                        sticky_article=excluded.sticky_article,
                        overlay_skipped=excluded.overlay_skipped,
                        display_name=excluded.display_name,
                        schedule_json=excluded.schedule_json,
                        catalog_meta_json=excluded.catalog_meta_json,
                        notify_state_json=excluded.notify_state_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        str(seller_id),
                        current["entity"],
                        json.dumps(current["marketplaces"], ensure_ascii=False),
                        current["category"],
                        json.dumps(current["missions"], ensure_ascii=False),
                        current["tz"],
                        1 if current["push_enabled"] else 0,
                        current["reminder_hour"],
                        current["sticky_article"],
                        1 if current["overlay_skipped"] else 0,
                        current.get("display_name"),
                        json.dumps(current.get("schedule") or DEFAULT_SCHEDULE, ensure_ascii=False),
                        json.dumps(current.get("catalog_meta") or {}, ensure_ascii=False),
                        json.dumps(current.get("notify_state") or {}, ensure_ascii=False),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return current

    def list_seller_ids(self) -> list[str]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT seller_id FROM seller_miniapp_prefs"
                ).fetchall()
            finally:
                conn.close()
        return [str(row[0]) for row in rows]

    def complete_mission(self, seller_id: str, mission_id: str) -> dict[str, Any]:
        if mission_id not in DEFAULT_MISSIONS:
            raise ValueError("unknown_mission")
        current = self.get(seller_id)
        current["missions"][mission_id] = True
        return self.upsert(seller_id, missions=current["missions"])
