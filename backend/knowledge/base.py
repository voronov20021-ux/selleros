"""KnowledgeBase — lookup by term/alias, version-aware."""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable

from backend.knowledge.models import KnowledgeCategory, KnowledgeEntry
from backend.knowledge.seed_v2 import build_seed_entries_v2

_NORM = re.compile(r"[^a-zA-Zа-яА-ЯёЁ0-9]+", re.UNICODE)


def _norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е").strip()
    s = _NORM.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


class KnowledgeBase:
    def __init__(self, entries: Iterable[KnowledgeEntry] | None = None) -> None:
        self._entries: list[KnowledgeEntry] = list(entries or [])
        self._by_key: dict[str, KnowledgeEntry] = {}
        for e in self._entries:
            self._index(e)

    def _index(self, e: KnowledgeEntry) -> None:
        keys = [e.term, *e.aliases]
        for k in keys:
            nk = _norm(k)
            if nk:
                self._by_key[nk] = e

    def add(self, entry: KnowledgeEntry) -> None:
        self._entries.append(entry)
        self._index(entry)

    def get(self, term: str, *, on: date | None = None) -> KnowledgeEntry | None:
        e = self._by_key.get(_norm(term))
        if e is None:
            return None
        if on is not None and not self._is_valid(e, on):
            return None
        return e

    def _is_valid(self, e: KnowledgeEntry, on: date) -> bool:
        try:
            start = date.fromisoformat(e.valid_from)
        except ValueError:
            start = date.min
        if on < start:
            return False
        if e.valid_to:
            try:
                end = date.fromisoformat(e.valid_to)
            except ValueError:
                end = date.max
            if on > end:
                return False
        return True

    def search(self, query: str, *, limit: int = 5, on: date | None = None) -> list[KnowledgeEntry]:
        q = _norm(query)
        if not q:
            return []
        hit = self.get(q, on=on)
        if hit:
            return [hit]
        scored: list[tuple[int, KnowledgeEntry]] = []
        seen: set[str] = set()
        for e in self._entries:
            if on is not None and not self._is_valid(e, on):
                continue
            keys = [_norm(e.term), *[_norm(a) for a in e.aliases]]
            score = 0
            for k in keys:
                if not k:
                    continue
                if k == q:
                    score = 100
                elif k in q or q in k:
                    score = max(score, 80)
                elif any(tok and tok in k for tok in q.split()):
                    score = max(score, 40)
            if score and e.term not in seen:
                seen.add(e.term)
                scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]

    def by_category(self, category: KnowledgeCategory | str) -> list[KnowledgeEntry]:
        c = category if isinstance(category, KnowledgeCategory) else KnowledgeCategory(str(category))
        # marketplace alias
        aliases = {c}
        if c in (KnowledgeCategory.MARKETPLACE, KnowledgeCategory.MARKETPLACES):
            aliases = {KnowledgeCategory.MARKETPLACE, KnowledgeCategory.MARKETPLACES, KnowledgeCategory.WILDBERRIES}
        return [e for e in self._entries if e.category in aliases]

    def all_terms(self) -> list[str]:
        return sorted({e.term for e in self._entries})

    def __len__(self) -> int:
        return len(self._entries)


_DEFAULT: KnowledgeBase | None = None


def get_default_knowledge_base() -> KnowledgeBase:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = KnowledgeBase(build_seed_entries_v2())
    return _DEFAULT


def reset_default_knowledge_base() -> KnowledgeBase:
    global _DEFAULT
    _DEFAULT = KnowledgeBase(build_seed_entries_v2())
    return _DEFAULT
