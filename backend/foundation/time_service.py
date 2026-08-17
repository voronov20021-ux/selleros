"""
time_service.py — единый источник времени для ARGUS.

Не размазывать datetime.now() / time.time() по новым foundation-слоям:
всё через TimeService (injectable clock для тестов).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

# Default seller TZ (Russia marketplace). Override per seller later.
DEFAULT_SELLER_TZ = "Europe/Moscow"
_MSK = timezone(timedelta(hours=3), name="Europe/Moscow")

_Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_tz(name: str):
    """ZoneInfo when available; else fixed UTC+3 for Europe/Moscow (Windows without tzdata)."""
    key = name or DEFAULT_SELLER_TZ
    if ZoneInfo is not None:
        try:
            return ZoneInfo(key), key
        except Exception:
            pass
    if key in ("Europe/Moscow", "MSK", "Russia/Moscow"):
        return _MSK, "Europe/Moscow"
    # last resort: UTC
    return timezone.utc, "UTC"


@dataclass(frozen=True)
class PeriodWindow:
    """Inclusive calendar period in seller timezone."""

    start: datetime
    end: datetime
    label: str  # day | week | month | custom

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        ref = now or self.end
        return max(0.0, (ref - self.start).total_seconds())


class TimeService:
    """
    Canonical clock for ARGUS foundation layers.

    - current datetime (aware)
    - seller timezone
    - period / week / month helpers
    - elapsed, due dates, check_after, reminders, follow-ups
    """

    def __init__(
        self,
        *,
        seller_timezone: str = DEFAULT_SELLER_TZ,
        clock: _Clock | None = None,
    ) -> None:
        self._tz, self._tz_name = _resolve_tz(seller_timezone or DEFAULT_SELLER_TZ)
        self._clock = clock or _utc_now

    # ── identity ──────────────────────────────────────────────────────────

    @property
    def seller_timezone(self) -> str:
        return self._tz_name

    def now(self) -> datetime:
        """Aware UTC datetime from injectable clock."""
        dt = self._clock()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def now_seller(self) -> datetime:
        return self.now().astimezone(self._tz)

    def today(self) -> date:
        return self.now_seller().date()

    def timestamp(self) -> float:
        """Unix seconds — compatible with existing Memory/Outcome stores."""
        return self.now().timestamp()

    # ── periods ───────────────────────────────────────────────────────────

    def day_window(self, on: date | None = None) -> PeriodWindow:
        d = on or self.today()
        start = datetime(d.year, d.month, d.day, tzinfo=self._tz)
        end = start + timedelta(days=1) - timedelta(microseconds=1)
        return PeriodWindow(start=start, end=end, label="day")

    def week_window(self, on: date | None = None) -> PeriodWindow:
        """ISO week Mon–Sun in seller TZ."""
        d = on or self.today()
        start_date = d - timedelta(days=d.weekday())
        start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=self._tz)
        end = start + timedelta(days=7) - timedelta(microseconds=1)
        return PeriodWindow(start=start, end=end, label="week")

    def month_window(self, on: date | None = None) -> PeriodWindow:
        d = on or self.today()
        start = datetime(d.year, d.month, 1, tzinfo=self._tz)
        if d.month == 12:
            nxt = datetime(d.year + 1, 1, 1, tzinfo=self._tz)
        else:
            nxt = datetime(d.year, d.month + 1, 1, tzinfo=self._tz)
        end = nxt - timedelta(microseconds=1)
        return PeriodWindow(start=start, end=end, label="month")

    def custom_window(self, start: datetime, end: datetime, label: str = "custom") -> PeriodWindow:
        if start.tzinfo is None:
            start = start.replace(tzinfo=self._tz)
        if end.tzinfo is None:
            end = end.replace(tzinfo=self._tz)
        return PeriodWindow(start=start, end=end, label=label)

    # ── elapsed / due ─────────────────────────────────────────────────────

    def elapsed_seconds(self, since: float | datetime) -> float:
        if isinstance(since, (int, float)):
            since_dt = datetime.fromtimestamp(float(since), tz=timezone.utc)
        else:
            since_dt = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        return max(0.0, (self.now() - since_dt.astimezone(timezone.utc)).total_seconds())

    def elapsed_days(self, since: float | datetime) -> float:
        return self.elapsed_seconds(since) / 86400.0

    def due_at(self, *, days: float = 0, hours: float = 0, from_ts: float | None = None) -> float:
        base = from_ts if from_ts is not None else self.timestamp()
        return float(base) + days * 86400.0 + hours * 3600.0

    def check_after(
        self,
        *,
        days: float = 7.0,
        hours: float = 0.0,
        from_ts: float | None = None,
    ) -> float:
        """Canonical follow-up timestamp after an action."""
        return self.due_at(days=days, hours=hours, from_ts=from_ts)

    def reminder_at(
        self,
        check_after_ts: float,
        *,
        remind_hours_before: float = 24.0,
    ) -> float:
        return float(check_after_ts) - remind_hours_before * 3600.0

    def scheduled_followup(
        self,
        *,
        days: float = 7.0,
        from_ts: float | None = None,
    ) -> dict:
        """Structured follow-up schedule for ActionService / OutcomeFoundation."""
        check = self.check_after(days=days, from_ts=from_ts)
        return {
            "check_after": check,
            "reminder_at": self.reminder_at(check),
            "scheduled_followup_at": check,
            "seller_timezone": self._tz_name,
            "days": float(days),
        }

    def is_due(self, due_ts: float | None) -> bool:
        if due_ts is None:
            return False
        return self.timestamp() >= float(due_ts)

    def format_seller(self, ts: float | None) -> str:
        if ts is None:
            return "—"
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(self._tz)
        return dt.strftime("%Y-%m-%d %H:%M %Z")


# Process-wide default (tests may replace via set_time_service).
_DEFAULT = TimeService()


def get_time_service() -> TimeService:
    return _DEFAULT


def set_time_service(svc: TimeService | None) -> TimeService:
    global _DEFAULT
    _DEFAULT = svc or TimeService()
    return _DEFAULT
