"""
sources/yandex_search.py — адаптер Yandex Search API.

Endpoint: POST https://searchapi.api.cloud.yandex.net/v2/web/search
Auth:     Api-Key <WORDSTAT_TOKEN>   (та же переменная, что и для Wordstat)
Format:   API возвращает JSON {"rawData": "<base64-encoded XML>"}

─────────────────────────────────────────────────────────────────────────────
Структура XML (Yandex Search XML v1.0):

    <yandexsearch>
      <response>
        <results>
          <grouping>
            <group>
              <doc id="...">
                <url>…</url>
                <domain>…</domain>
                <title>текст <hlword>с подсветкой</hlword></title>
                <headline>текст сниппета</headline>     ← или <passages>
                <passages>
                  <passage>текст</passage>
                </passages>
                <modtime>20260808T024916</modtime>      ← может отсутствовать
              </doc>
            </group>
          </grouping>
        </results>
      </response>
    </yandexsearch>

    Теги <hlword> встречаются внутри <title>, <headline>, <passage>.
    Нужна конкатенация всего текстового содержимого ветки.
─────────────────────────────────────────────────────────────────────────────
capabilities:
    "web_search"          — поиск по вебу
    "news"                — новостные страницы (в результатах)
    "market_research"     — страницы маркетплейсов в выдаче
    "trend_research"      — тренды (через поисковые запросы)
    "competitor_research" — страницы конкурентов
─────────────────────────────────────────────────────────────────────────────
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

log = logging.getLogger("selleros.intelligence.yandex_search")

#: Переменная окружения — та же, что для Wordstat.
_TOKEN_ENV = "WORDSTAT_TOKEN"

#: folderId проекта в Yandex Cloud.
_FOLDER_ID = "b1gkc130d7ds9go5s93a"

_API_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"

#: Timeout на HTTP-запрос.
_TIMEOUT_S = 20.0


def _inner_text(element: ET.Element) -> str:
    """
    Собрать весь текст внутри элемента, включая текст дочерних тегов.

    Нужно для <title> и <headline>/<passage>, где Yandex вставляет
    <hlword>…</hlword> вокруг подсвеченных слов.
    ET.Element.itertext() обходит node.text + tail всех потомков.
    """
    return "".join(element.itertext()).strip()


def _parse_modtime(raw: str | None) -> float | None:
    """
    Превратить "20260808T024916" → unix timestamp.
    Возвращает None при любой ошибке.
    """
    if not raw:
        return None
    try:
        dt = datetime.datetime.strptime(raw.strip(), "%Y%m%dT%H%M%S")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


def _parse_xml_results(xml_bytes: bytes) -> list[dict]:
    """
    Разобрать XML-ответ Yandex Search и вернуть список dict-ов.

    Каждый dict:
        url         str
        title       str
        snippet     str   (headline > passages > "")
        domain      str
        published_at float | None
    """
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
    except ET.ParseError as exc:
        log.error("Yandex Search: не удалось разобрать XML: %s", exc)
        return []

    results: list[dict] = []

    # <response> → <results> → <grouping> → <group>+
    response_el = root.find("response")
    if response_el is None:
        log.warning("Yandex Search XML: тег <response> не найден")
        return results

    for group in response_el.findall(".//group"):
        doc = group.find("doc")
        if doc is None:
            continue

        url_el = doc.find("url")
        if url_el is None or not (url_el.text or "").strip():
            continue
        url = url_el.text.strip()

        title_el = doc.find("title")
        title = _inner_text(title_el) if title_el is not None else ""

        domain_el = doc.find("domain")
        domain = (domain_el.text or "").strip() if domain_el is not None else ""

        # snippet: headline первым приоритетом, иначе — первый passage
        headline_el = doc.find("headline")
        if headline_el is not None:
            snippet = _inner_text(headline_el)
        else:
            passages = doc.findall(".//passage")
            snippet = " ".join(_inner_text(p) for p in passages[:2]).strip()

        modtime_el = doc.find("modtime")
        published_at = _parse_modtime(
            modtime_el.text if modtime_el is not None else None
        )

        results.append(
            {
                "url": url,
                "title": title,
                "snippet": snippet,
                "domain": domain,
                "published_at": published_at,
            }
        )

    return results


class YandexSearchAdapter(DataSourceAdapter):
    """
    Адаптер Yandex Search API (Cloud Search XML v2).

    Требует WORDSTAT_TOKEN в .env.
    Нет WORDSTAT_TOKEN → is_available() = False, fetch() бросает SourceUnavailableError.

    Adapter НЕ записывает данные в store — только нормализует сырой ответ
    в список KnowledgeItem. Сохранение — через search_and_store() или
    вручную через IntelligenceStore.save_item() + EvidenceEngine.ingest().
    """

    @property
    def source_id(self) -> str:
        return "yandex_search"

    @property
    def capabilities(self) -> list[str]:
        return [
            "web_search",
            "news",
            "market_research",
            "trend_research",
            "competitor_research",
        ]

    def to_data_source(self) -> DataSource:
        return DataSource(
            id=self.source_id,
            name="Yandex Search API",
            source_type=SourceType.SCRAPED,
            authority=0.75,           # поисковая выдача — хороший сигнал, не первоисточник
            freshness_hours=24,       # выдача обновляется ежедневно
            capabilities=list(self.capabilities),
            base_url=_API_URL,
            is_active=bool(os.getenv(_TOKEN_ENV)),
            metadata={
                "requires_env": _TOKEN_ENV,
                "api_version": "v2",
                "folder_id": _FOLDER_ID,
                "response_format": "FORMAT_XML",
                "max_results_per_page": 10,
            },
        )

    async def is_available(self) -> bool:
        """
        Проверить наличие API-ключа.
        Не выполняет HTTP-запрос.
        """
        token = os.getenv(_TOKEN_ENV)
        if not token:
            log.debug(
                "YandexSearch недоступен: переменная %r не задана в .env", _TOKEN_ENV
            )
            return False
        return True

    async def fetch(
        self,
        *,
        query: str,
        category: str | None = None,
        region: str = "RU",
    ) -> list[KnowledgeItem]:
        """
        Получить результаты поиска из Yandex Search API.

        Параметры
        ─────────
        query       поисковый запрос
        category    категория WB (например "Часы") — попадает в KnowledgeItem.category
        region      регион (информационный, в запрос к API не передаётся)

        Возвращает
        ──────────
        Список KnowledgeItem, по одному на каждый результат выдачи.
        Adapter никуда не сохраняет — только нормализует и возвращает.
        """
        if not await self.is_available():
            raise SourceUnavailableError(
                self.source_id,
                f"Переменная {_TOKEN_ENV!r} не задана. Добавьте API-ключ в .env.",
            )

        token = os.getenv(_TOKEN_ENV, "")
        now = time.time()
        period = datetime.datetime.utcnow().strftime("%Y-%m")

        body = {
            "query": {
                "searchType": "SEARCH_TYPE_RU",
                "queryText": query,
                "familyMode": "FAMILY_MODE_MODERATE",
                "page": "0",
                "fixTypoMode": "FIX_TYPO_MODE_ON",
            },
            "folderId": _FOLDER_ID,
            "responseFormat": "FORMAT_XML",
            "l10N": "LOCALIZATION_RU",
        }

        headers = {
            "Authorization": f"Api-Key {token}",
            "Content-Type": "application/json",
        }

        log.info("YandexSearch: запрос %r (category=%s)", query, category)

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
            log.warning("YandexSearch: пустой rawData в ответе API")
            return []

        xml_bytes = base64.b64decode(raw_b64)
        parsed = _parse_xml_results(xml_bytes)

        log.info(
            "YandexSearch: получено %d результатов на запрос %r",
            len(parsed),
            query,
        )

        items: list[KnowledgeItem] = []
        for doc in parsed:
            content_parts = []
            if doc["title"]:
                content_parts.append(f"Заголовок: {doc['title']}")
            if doc["snippet"]:
                content_parts.append(f"Описание: {doc['snippet']}")
            content_parts.append(f"URL: {doc['url']}")
            content = "\n".join(content_parts)

            item = KnowledgeItem(
                id=str(uuid.uuid4()),
                source_id=self.source_id,
                source_url=doc["url"],
                collected_at=now,
                published_at=doc["published_at"],
                item_type=ItemType.FACT,
                category=category,
                region=region,
                period=period,
                confidence=0.70,
                content=content,
                metadata={
                    "query": query,
                    "title": doc["title"],
                    "url": doc["url"],
                    "domain": doc["domain"],
                    "snippet": doc["snippet"][:300] if doc["snippet"] else "",
                    "provider": "yandex_search",
                },
            )
            items.append(item)

        return items
