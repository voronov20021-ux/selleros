"""Telegram notification worker — same asyncio loop as ActionVerificationScheduler.

EVENT / ACTION_CHECK / RE_ENGAGEMENT. No new process, no new database.
Clocks come from TimeService only. Dedup lives in MiniAppStore.notify_state.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from backend.foundation.time_service import TimeService, get_time_service

log = logging.getLogger("selleros.foundation.notification_worker")

NotifySellerFn = Callable[[int, str, dict[str, Any] | None], Awaitable[None]]

RE_ENGAGE_HOURS = (24, 48, 72)
RE_ENGAGE_GAP_HOURS = 23.5
EVENT_MAX_AGE_DAYS = 7.0
SENT_EVENTS_CAP = 200

EVENT_COPY = "ARGUS: новое важное изменение по товару {title}."
ACTION_CHECK_COPY = "Пора проверить действие: {name}."
RE_ENGAGE_COPY = {
    1: "ARGUS: вы не заходили сутки. Откройте разбор — могло появиться важное.",
    2: "ARGUS: прошло двое суток. Коротко проверю карточку, если вернётесь в Seller OS.",
    3: "ARGUS: последнее напоминание. Дальше молчу, пока вы сами не напишете.",
}


@dataclass
class NotificationEvent:
    event_id: str
    seller_id: int
    title: str
    source: str = "injected"


@dataclass
class NotificationTickResult:
    sent: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def texts_for(self, seller_id: int) -> list[str]:
        return [row["text"] for row in self.sent if int(row["seller_id"]) == int(seller_id)]


def _as_seller_id(raw: Any) -> int | None:
    try:
        sid = int(raw)
    except (TypeError, ValueError):
        return None
    if sid <= 0:
        return None
    return sid


def _toggle(schedule: dict[str, Any] | None, *keys: str, default: bool = False) -> bool:
    if not isinstance(schedule, dict):
        return default
    for key in keys:
        if key in schedule:
            return bool(schedule[key])
    return default


class NotificationWorker:
    """Periodic Telegram pushes honoring Mini App notification toggles."""

    def __init__(
        self,
        *,
        time_service: TimeService | None = None,
        prefs=None,
        memory_store=None,
        action_service=None,
        intelligence_store=None,
        notify: NotifySellerFn | None = None,
        interval_sec: float = 300.0,
    ) -> None:
        self._time = time_service or get_time_service()
        self._prefs = prefs
        self._memory = memory_store
        self._actions = action_service
        self._intel = intelligence_store
        self._notify = notify
        self._interval = float(interval_sec)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._pending: list[NotificationEvent] = []

    def set_intelligence_store(self, store) -> None:
        self._intel = store

    def queue_event(
        self,
        seller_id: int,
        event_id: str,
        title: str,
        *,
        source: str = "injected",
    ) -> NotificationEvent:
        ev = NotificationEvent(
            event_id=str(event_id),
            seller_id=int(seller_id),
            title=str(title or "товар"),
            source=source,
        )
        self._pending.append(ev)
        return ev

    def start_background(self) -> asyncio.Task:
        if self._task and not self._task.done():
            return self._task
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="notification_worker")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:
                log.exception("notification tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def mark_returned(self, seller_id: int) -> None:
        """Test/helper: bump last_seen via MemoryStore using TimeService clock."""
        if self._memory is None:
            return
        if hasattr(self._memory, "mark_seen"):
            await self._memory.mark_seen(int(seller_id), self._time.timestamp())
            return
        if hasattr(self._memory, "touch_user"):
            await self._memory.touch_user(int(seller_id))

    async def tick(self, seller_id: int | None = None) -> NotificationTickResult:
        result = NotificationTickResult()
        sellers = await self._iter_seller_ids()
        if seller_id is not None:
            only = _as_seller_id(seller_id)
            sellers = [sid for sid in sellers if sid == only]
        for sid in sellers:
            try:
                await self._tick_seller(sid, result)
            except Exception as exc:
                log.debug("notify seller %s skip: %s", sid, exc)
                result.skipped.append(f"error:{sid}")
        return result

    async def _iter_seller_ids(self) -> list[int]:
        found: set[int] = set()
        if self._prefs is not None and hasattr(self._prefs, "list_seller_ids"):
            for raw in self._prefs.list_seller_ids() or []:
                sid = _as_seller_id(raw)
                if sid is not None:
                    found.add(sid)
        if self._memory is not None and hasattr(self._memory, "list_user_ids"):
            try:
                for raw in await self._memory.list_user_ids():
                    sid = _as_seller_id(raw)
                    if sid is not None:
                        found.add(sid)
            except Exception as exc:
                log.debug("list_user_ids skip: %s", exc)
        if self._actions is not None:
            try:
                due = await self._actions.list_due_checks(None)
                for action in due:
                    sid = _as_seller_id(action.seller_id)
                    if sid is not None:
                        found.add(sid)
            except Exception as exc:
                log.debug("due sellers skip: %s", exc)
            for action in getattr(self._actions, "_mem", {}).values():
                sid = _as_seller_id(getattr(action, "seller_id", None))
                if sid is not None:
                    found.add(sid)
        for ev in self._pending:
            found.add(int(ev.seller_id))
        return sorted(found)

    def _prefs_row(self, seller_id: int) -> dict[str, Any]:
        if self._prefs is None:
            return {
                "schedule": {
                    "notify_event": True,
                    "notify_action_check": False,
                    "notify_reengagement": False,
                },
                "notify_state": {},
                "category": None,
            }
        return self._prefs.get(str(seller_id))

    def _save_state(self, seller_id: int, state: dict[str, Any]) -> None:
        if self._prefs is None or not hasattr(self._prefs, "upsert"):
            return
        self._prefs.upsert(str(seller_id), notify_state=state)

    async def _last_seen(self, seller_id: int) -> float | None:
        if self._memory is None or not hasattr(self._memory, "get_last_seen"):
            return None
        try:
            val = await self._memory.get_last_seen(int(seller_id))
        except Exception as exc:
            log.debug("get_last_seen skip: %s", exc)
            return None
        return float(val) if val is not None else None

    async def _tick_seller(self, seller_id: int, result: NotificationTickResult) -> None:
        row = self._prefs_row(seller_id)
        schedule = row.get("schedule") or {}
        state = dict(row.get("notify_state") or {})
        last_seen = await self._last_seen(seller_id)

        if _toggle(schedule, "notify_event", "critical_enabled", default=True):
            await self._send_events(seller_id, state, result)
        else:
            result.skipped.append(f"event_off:{seller_id}")

        if _toggle(schedule, "notify_action_check", "action_check_enabled", default=False):
            await self._send_action_checks(seller_id, state, last_seen, result)
        else:
            result.skipped.append(f"action_off:{seller_id}")

        if _toggle(schedule, "notify_reengagement", default=False):
            await self._send_reengagement(seller_id, state, last_seen, result)
        else:
            result.skipped.append(f"reeng_off:{seller_id}")

        self._save_state(seller_id, state)

    async def _collect_events(self, seller_id: int) -> list[NotificationEvent]:
        out: list[NotificationEvent] = []
        keep: list[NotificationEvent] = []
        for ev in self._pending:
            if int(ev.seller_id) == int(seller_id):
                out.append(ev)
            else:
                keep.append(ev)
        self._pending = keep

        if self._memory is not None and hasattr(self._memory, "list_products"):
            try:
                products = await self._memory.list_products(int(seller_id))
            except Exception as exc:
                log.debug("list_products skip: %s", exc)
                products = []
            for product in products or []:
                article = getattr(product, "article", None)
                if article is None or not hasattr(self._memory, "changes_for"):
                    continue
                title = getattr(product, "title", None) or str(article)
                try:
                    changes = await self._memory.changes_for(
                        int(seller_id), int(article), limit=8,
                    )
                except Exception:
                    continue
                for change in changes or []:
                    changed_at = getattr(change, "changed_at", None)
                    if changed_at is not None and self._time.elapsed_days(changed_at) > EVENT_MAX_AGE_DAYS:
                        continue
                    cid = getattr(change, "id", None)
                    eid = f"chg:{seller_id}:{cid or getattr(change, 'changed_at', '')}:{getattr(change, 'field', '')}"
                    out.append(NotificationEvent(eid, seller_id, str(title), "product_change"))

        intel = self._intel
        category = (self._prefs_row(seller_id).get("category") or "").strip()
        if intel is not None and category and hasattr(intel, "list_market_events"):
            try:
                events = await intel.list_market_events(category=category, limit=8)
            except Exception as exc:
                log.debug("market_events skip: %s", exc)
                events = []
            for ev in events or []:
                when = getattr(ev, "event_date", None) or getattr(ev, "created_at", None)
                if when is not None and self._time.elapsed_days(when) > EVENT_MAX_AGE_DAYS:
                    continue
                if float(getattr(ev, "confidence", 1.0) or 0) < 0.5:
                    continue
                eid = f"mkt:{getattr(ev, 'id', '')}"
                title = getattr(ev, "title", None) or "рынок"
                out.append(NotificationEvent(str(eid), seller_id, str(title), "market_event"))
        return out

    async def _send_events(
        self,
        seller_id: int,
        state: dict[str, Any],
        result: NotificationTickResult,
    ) -> None:
        sent_ids = [str(x) for x in (state.get("sent_events") or [])]
        sent_set = set(sent_ids)
        for ev in await self._collect_events(seller_id):
            if ev.event_id in sent_set:
                result.skipped.append(f"event_dup:{ev.event_id}")
                continue
            text = EVENT_COPY.format(title=ev.title)
            if not await self._emit(seller_id, "event", text, {"event_id": ev.event_id}, result):
                continue
            sent_ids.append(ev.event_id)
            sent_set.add(ev.event_id)
        state["sent_events"] = sent_ids[-SENT_EVENTS_CAP:]

    async def _send_action_checks(
        self,
        seller_id: int,
        state: dict[str, Any],
        last_seen: float | None,
        result: NotificationTickResult,
    ) -> None:
        if self._actions is None:
            return
        sent_map = dict(state.get("sent_action_checks") or {})
        try:
            due = await self._actions.list_due_checks(int(seller_id))
        except Exception as exc:
            log.debug("list_due_checks skip: %s", exc)
            return
        now = self._time.timestamp()
        for action in due:
            if int(action.seller_id) != int(seller_id):
                continue
            aid = str(action.action_id)
            if aid in sent_map:
                result.skipped.append(f"action_dup:{aid}")
                continue
            due_ts = action.next_verification_at or action.check_after
            if not self._time.is_due(due_ts):
                result.skipped.append(f"action_not_due:{aid}")
                continue
            if last_seen is not None and float(last_seen) >= float(due_ts):
                result.skipped.append(f"action_returned:{aid}")
                sent_map[aid] = now
                continue
            name = (action.recommendation or action.action_type.value or "действие").strip()
            text = ACTION_CHECK_COPY.format(name=name[:80])
            if not await self._emit(seller_id, "action_check", text, {"action_id": aid}, result):
                continue
            sent_map[aid] = now
        state["sent_action_checks"] = sent_map

    async def _send_reengagement(
        self,
        seller_id: int,
        state: dict[str, Any],
        last_seen: float | None,
        result: NotificationTickResult,
    ) -> None:
        if last_seen is None:
            result.skipped.append(f"reeng_no_seen:{seller_id}")
            return
        idle_hours = self._time.elapsed_seconds(last_seen) / 3600.0
        step = int(state.get("reengage_step") or 0)
        last_sent = float(state.get("reengage_sent_at") or 0)

        if idle_hours < 24:
            if step or last_sent:
                state["reengage_step"] = 0
                state["reengage_sent_at"] = 0
            result.skipped.append(f"reeng_active:{seller_id}")
            return
        if step >= 3:
            result.skipped.append(f"reeng_done:{seller_id}")
            return
        if last_sent and (self._time.timestamp() - last_sent) < RE_ENGAGE_GAP_HOURS * 3600:
            result.skipped.append(f"reeng_gap:{seller_id}")
            return

        next_step = 0
        if idle_hours >= 72 and step < 3:
            next_step = 3
        elif idle_hours >= 48 and step < 2:
            next_step = 2
        elif idle_hours >= 24 and step < 1:
            next_step = 1
        if not next_step:
            result.skipped.append(f"reeng_wait:{seller_id}")
            return
        text = RE_ENGAGE_COPY[next_step]
        if not await self._emit(seller_id, f"reengage_{next_step}", text, {"step": next_step}, result):
            return
        state["reengage_step"] = next_step
        state["reengage_sent_at"] = self._time.timestamp()

    async def _emit(
        self,
        seller_id: int,
        category: str,
        text: str,
        extras: dict[str, Any] | None,
        result: NotificationTickResult,
    ) -> bool:
        if self._notify is not None:
            try:
                await self._notify(int(seller_id), text, extras)
            except Exception as exc:
                log.debug("notify emit skip %s: %s", seller_id, exc)
                result.skipped.append(f"emit_fail:{seller_id}:{category}")
                return False
        result.sent.append(
            {
                "seller_id": int(seller_id),
                "category": category,
                "text": text,
                "extras": extras or {},
            }
        )
        return True
