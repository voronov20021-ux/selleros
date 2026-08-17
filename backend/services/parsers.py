"""
parsers.py — разбор пользовательского ввода (цена / рейтинг / отзывы).

Отзывы и целые счётчики принимают тысячные разделители:
    2578, 2 578, 2.578, 2,578 → 2578
"""

from __future__ import annotations

import re


def parse_nonneg_int(text: str) -> int | None:
    """
    Неотрицательное целое из пользовательского ввода.

    Принимает:
        2578 / 2 578 / 2.578 / 2,578 / 2\\u00a0578
    → 2578
    """
    raw = (text or "").strip()
    if not raw:
        return None

    compact = re.sub(r"[\s\u00a0]+", "", raw)
    if not compact:
        return None

    # Группы тысяч: 2.578 / 2,578 / 1.234.567
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", compact):
        digits = re.sub(r"[.,]", "", compact)
        try:
            value = int(digits)
        except ValueError:
            return None
        return value if value >= 0 else None

    if re.fullmatch(r"\d+", compact):
        try:
            value = int(compact)
        except ValueError:
            return None
        return value if value >= 0 else None

    return None


def parse_price(text: str) -> float | None:
    """Положительная цена. Допускает пробелы и запятую как десятичный разделитель."""
    raw = (text or "").strip()
    if not raw:
        return None

    # Целые с тысячными разделителями (2 490 / 2.490 / 2,490).
    as_int = parse_nonneg_int(raw)
    if as_int is not None and as_int > 0 and not re.search(
        r"[.,]\d{1,2}$", re.sub(r"[\s\u00a0]+", "", raw)
    ):
        return float(as_int)

    try:
        value = float(re.sub(r"[\s\u00a0]+", "", raw).replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


def parse_rating(text: str) -> float | None:
    """Рейтинг 0..5. Запятая → точка."""
    try:
        value = float((text or "").strip().replace(",", "."))
    except ValueError:
        return None
    return value if 0 <= value <= 5 else None
