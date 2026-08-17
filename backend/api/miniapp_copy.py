"""Seller-facing copy helpers. Does not change Advisor/Funnel math."""

from __future__ import annotations

from typing import Any

FUNNEL_RU = {
    "CONSISTENT": "Данные воронки согласованы",
    "INCONSISTENT": "Данные противоречат друг другу",
    "INVALID": "Данные воронки некорректны",
    "MISSING": "Данных недостаточно",
}

SOURCE_RU = {
    "PUBLIC_BROWSER": "Источник: карточка WB",
    "public_browser": "Источник: карточка WB",
    "browser": "Источник: карточка WB",
    "cdn": "Источник: карточка WB",
}


def human_funnel_status(code: Any) -> str:
    if code is None or code == "":
        return "Данных недостаточно"
    key = str(code).upper()
    return FUNNEL_RU.get(key, "Данных недостаточно")


def human_source(code: Any) -> str:
    if not code:
        return "Источник: карточка WB"
    return SOURCE_RU.get(str(code), SOURCE_RU.get(str(code).upper(), "Источник: карточка WB"))


def format_short_reply(*, verdict: str = "", why: str = "", action: str = "") -> str:
    v = (verdict or "Данных недостаточно").strip()
    w = (why or "Пока мало подтверждённых фактов.").strip()
    a = (action or "Пока нет подтверждённого шага.").strip()
    return f"Вывод: {v}\nПочему: {w}\nДействие: {a}"
