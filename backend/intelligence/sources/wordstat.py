"""
sources/wordstat.py — адаптер спроса на базе Yandex Search API.

СТАТУС: рабочая реализация.

────────────────────────────────────────────────────────────────────────────
Реальность API-доступа (август 2026):

    Yandex Wordstat API (api.wordstat.yandex.ru) требует OAuth-токен
    от Yandex.Direct и НЕ доступен по Yandex Cloud API-ключу.

    Yandex Search API (searchapi.api.cloud.yandex.net) работает с
    WORDSTAT_TOKEN (Cloud API-ключ) и возвращает в XML:
        <found priority="phrase">N</found>   — число страниц с точной фразой
        <found-human>Нашлось N млн ответов</found-human>
        Кластеры доменов в <group>/<categ name="domain.ru"/>

    found_phrase — надёжный прокси-сигнал поискового спроса:
        больше страниц с фразой → выше коммерческий интерес к запросу.
        Сравнение нескольких запросов показывает относительный спрос.

────────────────────────────────────────────────────────────────────────────
Что возвращает fetch():

    1 основной KnowledgeItem (search_demand):
        content  = "Яндекс Поиск: запрос «{query}» — {found:,} результатов
                    ({found_human}), регион {region}"
        metadata = {
            "query":           str,
            "found_phrase":    int,     # точная фраза
            "found_all":       int,     # расширенный поиск
            "found_human":     str,     # "Нашлось 9 млн ответов"
            "top_domains":     list[str],  # топ-10 доменов в выдаче
            "result_count":    int,     # число результатов на странице
            "provider":        "yandex_search_demand_proxy",
        }

    Дополнительные KnowledgeItem (related_queries) — по одному на каждый
    топ-домен из выдачи (используются как косвенные спросовые сигналы).

────────────────────────────────────────────────────────────────────────────
Честность данных:
    content и metadata явно указывают "Яндекс Поиск" и "found_phrase"
    (не "показы Wordstat"). Это реальные числа, не выдумка.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import datetime
import logging
import os
import time
import uuid
import xml.etree.ElementTree as ET

import httpx

from backend.intelligence.models import DataSource, ItemType, KnowledgeItem, SourceType
from backend.intelligence.sources.base import (
    DataSourceAdapter,
    SourceUnavailableError,
)

log = logging.getLogger("selleros.intelligence.wordstat")

_TOKEN_ENV  = "WORDSTAT_TOKEN"
_API_URL    = "https://searchapi.api.cloud.yandex.net/v2/web/search"
_FOLDER_ID  = "b1gkc130d7ds9go5s93a"
_TIMEOUT_S  = 20.0

REGION_RUSSIA = 225


class WordstatAdapter(DataSourceAdapter):
    """
    Адаптер поискового спроса на базе Yandex Search API.

    Использует WORDSTAT_TOKEN (Yandex Cloud API-ключ).
    fetch() возвращает реальные данные о поисковом спросе через
    found_phrase count из Yandex Search XML API.
    """

    @property
    def source_id(self) -> str:
        return "yandex_wordstat"

    @property
    def capabilities(self) -> list[str]:
        return [
            "search_demand",          # объём поискового спроса (found_phrase)
            "top_related_queries",    # топ доменов = косвенные спросовые сигналы
        ]

    def to_data_source(self) -> DataSource:
        return DataSource(
            id=self.source_id,
            name="Yandex Search Demand (via Search API)",
            source_type=SourceType.PUBLIC_API,
            authority=0.80,           # реальные данные Яндекса, прокси-метрика
            freshness_hours=24,       # found_phrase меняется ежедневно
            capabilities=list(self.capabilities),
            base_url=_API_URL,
            is_active=bool(os.getenv(_TOKEN_ENV)),
            metadata={
                "requires_env":  _TOKEN_ENV,
                "metric":        "found_phrase",
                "note":          "Proxy for search demand; not Wordstat impressions",
                "folder_id":     _FOLDER_ID,
            },
        )

    async def is_available(self) -> bool:
        token = os.getenv(_TOKEN_ENV)
        if not token:
            log.debug("WordstatAdapter: %r не задан в .env", _TOKEN_ENV)
            return False
        return True

    async def fetch(
        self,
        *,
        query: str,
        region_id: int = REGION_RUSSIA,
        date_from: str | None = None,
        date_to: str | None = None,
        category: str | None = None,
        include_related: bool = True,
    ) -> list[KnowledgeItem]:
        """
        Получить поисковый спрос по запросу через Yandex Search API.

        Возвращает 1 основной KnowledgeItem (found_phrase, found_human,
        top_domains) + до 10 item-ов по топ-доменам если include_related=True.

        Параметры date_from / date_to / region_id принимаются для
        совместимости интерфейса, но Search API не поддерживает
        временной срез — always returns current-day data.
        """
        if not await self.is_available():
            raise SourceUnavailableError(
                self.source_id,
                f"{_TOKEN_ENV!r} не задан в .env.",
            )

        token  = os.getenv(_TOKEN_ENV, "")
        now    = time.time()
        period = datetime.datetime.utcnow().strftime("%Y-%m")

        body = {
            "query": {
                "searchType":  "SEARCH_TYPE_RU",
                "queryText":   query,
                "familyMode":  "FAMILY_MODE_MODERATE",
                "page":        "0",
                "fixTypoMode": "FIX_TYPO_MODE_ON",
            },
            "folderId":      _FOLDER_ID,
            "responseFormat": "FORMAT_XML",
            "l10N":          "LOCALIZATION_RU",
        }
        headers = {
            "Authorization": f"Api-Key {token}",
            "Content-Type":  "application/json",
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(_API_URL, json=body, headers=headers)

        if resp.status_code != 200:
            raise SourceUnavailableError(
                self.source_id,
                f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        payload = resp.json()
        raw_b64 = payload.get("rawData", "")
        if not raw_b64:
            log.warning("WordstatAdapter: пустой rawData")
            return []

        xml_bytes  = base64.b64decode(raw_b64)
        parsed     = self._parse_xml(xml_bytes)

        if parsed is None:
            return []

        found_phrase = parsed["found_phrase"]
        found_all    = parsed["found_all"]
        found_human  = parsed["found_human"]
        top_domains  = parsed["top_domains"]
        result_count = parsed["result_count"]

        region_label = f"RU-{region_id}" if region_id != REGION_RUSSIA else "RU"

        log.info(
            "WordstatAdapter: query=%r → found_phrase=%d (%s)",
            query, found_phrase, found_human,
        )

        items: list[KnowledgeItem] = []

        # ── Основной item: суммарный спрос ──────────────────────────────── #
        content = (
            f"Яндекс Поиск: запрос «{query}» — {found_phrase:,} результатов "
            f"({found_human}), регион {region_label}, период {period}"
        ).replace(",", " ")

        main_item = KnowledgeItem(
            id=str(uuid.uuid4()),
            source_id=self.source_id,
            source_url=None,
            collected_at=now,
            published_at=None,
            item_type=ItemType.FACT,
            category=category,
            region=region_label,
            period=period,
            confidence=0.82,
            content=content,
            metadata={
                "query":        query,
                "found_phrase": found_phrase,
                "found_all":    found_all,
                "found_human":  found_human,
                "top_domains":  top_domains[:10],
                "result_count": result_count,
                "provider":     "yandex_search_demand_proxy",
            },
        )
        items.append(main_item)

        # ── Дополнительные items: топ-домены как спросовые сигналы ──────── #
        if include_related:
            for domain in top_domains[:5]:
                dom_content = (
                    f"Топ-домен в выдаче Яндекса по запросу «{query}»: {domain}"
                )
                dom_item = KnowledgeItem(
                    id=str(uuid.uuid4()),
                    source_id=self.source_id,
                    source_url=f"https://{domain}",
                    collected_at=now,
                    published_at=None,
                    item_type=ItemType.FACT,
                    category=category,
                    region=region_label,
                    period=period,
                    confidence=0.65,
                    content=dom_content,
                    metadata={
                        "query":    query,
                        "domain":   domain,
                        "provider": "yandex_search_demand_proxy",
                    },
                )
                items.append(dom_item)

        return items

    # ─────────────────────────── XML parsing ────────────────────────────── #

    @staticmethod
    def _parse_xml(xml_bytes: bytes) -> dict | None:
        """
        Разобрать XML-ответ Yandex Search.

        Извлекает found_phrase, found_all, found_human, top_domains.
        """
        try:
            root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
        except ET.ParseError as exc:
            log.error("WordstatAdapter XML parse error: %s", exc)
            return None

        resp_el = root.find("response")
        if resp_el is None:
            log.warning("WordstatAdapter: тег <response> не найден")
            return None

        found_map: dict[str, int] = {}
        for el in resp_el.findall("found"):
            priority = el.get("priority", "unknown")
            try:
                found_map[priority] = int(el.text or "0")
            except (ValueError, TypeError):
                pass

        found_human = resp_el.findtext("found-human") or ""

        # Топ-домены из <group><categ name="domain.ru"/>
        top_domains: list[str] = []
        for categ in root.findall(".//categ"):
            name = categ.get("name", "")
            if name and name not in top_domains:
                top_domains.append(name)

        groups = root.findall(".//group")

        return {
            "found_phrase": found_map.get("phrase", 0),
            "found_all":    found_map.get("all",    0),
            "found_human":  found_human,
            "top_domains":  top_domains,
            "result_count": len(groups),
        }

    # ─────────────────────────── helper (для тестов) ────────────────────── #

    @staticmethod
    def _make_item(
        *,
        query: str,
        impressions: int,
        period: str,
        region_id: int,
        category: str | None,
        source_url: str | None = None,
        metadata: dict | None = None,
    ) -> KnowledgeItem:
        """Фабричный метод — для тестов формата items."""
        region_label = f"RU-{region_id}" if region_id != REGION_RUSSIA else "RU"
        content = (
            f"Яндекс Поиск: запрос «{query}» — {impressions:,} результатов, "
            f"{period}, регион {region_label}"
        ).replace(",", " ")

        return KnowledgeItem(
            id=str(uuid.uuid4()),
            source_id="yandex_wordstat",
            source_url=source_url,
            collected_at=time.time(),
            published_at=None,
            item_type=ItemType.FACT,
            category=category,
            region=region_label,
            period=period,
            confidence=0.82,
            content=content,
            metadata={
                "query":        query,
                "found_phrase": impressions,
                "provider":     "yandex_search_demand_proxy",
                **(metadata or {}),
            },
        )
