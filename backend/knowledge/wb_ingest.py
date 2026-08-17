"""
wb_ingest.py — load structured WB offer rules from official excerpt JSON.

Source of truth for this stage:
  backend/knowledge/data/wb_offer_ru_excerpt.json
  (from https://static-basket-02.wb.ru/vol20/suppliers-portal-root/0.0.2/offer-ru.pdf)

Актуальная редакция всегда сверяется с кабинетом WB Partners.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parent / "data" / "wb_offer_ru_excerpt.json"

OFFICIAL_PDF_URL = (
    "https://static-basket-02.wb.ru/vol20/suppliers-portal-root/0.0.2/offer-ru.pdf"
)
CABINET_HELP_URL = (
    "https://seller.wildberries.ru/instructions/ru/ru/material/how-to-use-offer"
)


def load_offer_document(path: Path | None = None) -> dict[str, Any]:
    p = path or DATA_PATH
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("wb offer ingest: root must be object")
    return raw


def rules_from_document(doc: dict[str, Any]) -> list[dict[str, Any]]:
    rules = doc.get("rules") or []
    meta = doc.get("document") or {}
    out = []
    for r in rules:
        row = dict(r)
        row.setdefault("official_source", meta.get("official_source"))
        row.setdefault("source_url", meta.get("source_url") or OFFICIAL_PDF_URL)
        row.setdefault("offer_version", meta.get("offer_version"))
        row.setdefault(
            "source_quality",
            row.get("source_quality") or meta.get("source_quality") or "official_pdf_excerpt",
        )
        out.append(row)
    return out


def load_ingested_rules(path: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    doc = load_offer_document(path)
    return doc, rules_from_document(doc)
