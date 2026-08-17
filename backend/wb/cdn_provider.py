#!/usr/bin/env python3
"""
SellerOS · Wildberries product parser
=====================================

Клиент к публичным эндпоинтам Wildberries:

  * ``basket-XX.wbbasket.ru/.../card.json``  — контент карточки (описание,
    характеристики, состав, сертификаты, кол-во фото);
  * ``card.wb.ru/cards/v4/detail``           — цены, рейтинг, отзывы, склады
    и остатки (primary; при 403 — ProductCardProvider fallback);
  * ``feedbacks{1,2}.wb.ru``                 — детальная статистика отзывов;
  * ``static-basket-01.wbbasket.ru``         — карточка продавца (ИНН/ОГРН).

Ключевые отличия от наивной реализации
--------------------------------------
1. ``card.json`` скачивается **один раз** на артикул (было 3 раза).
2. Номер basket-хоста кешируется по ``vol`` в памяти и на диске — повторные
   прогоны не тратят запросы на перебор.
3. Батчинг цен: 1000 артикулов = 10 HTTP-запросов, а не 1000.
4. Session с keep-alive + ретраи с экспоненциальным бэкоффом и джиттером,
   отдельная обработка 429/5xx.
5. Асинхронный клиент (``AsyncWBClient``) для массового сканирования.
6. Никаких «немых» ``except: pass`` — всё пишется в logging.
7. Размеры/остатки берутся из detail-API (в ``card.json`` их нет — старый
   код всегда возвращал пустые списки).
8. Цена считается из ``sizes[].price.{basic,product,total}`` — актуальная
   схема v2; ``priceU/salePriceU`` в ответе больше не приходят.

Зависимости: ``pip install curl_cffi``
Python: 3.10+
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import logging
import os
import random
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Iterator, Sequence

from curl_cffi import requests

try:  # curl_cffi >= 0.7
    from curl_cffi.requests.exceptions import RequestException as HTTPTransportError
except ImportError:  # pragma: no cover - старые версии
    HTTPTransportError = Exception  # type: ignore[misc,assignment]

from backend.wb.product_card_provider import (
    DETAIL_PRIMARY_URL,
    ProductCardProvider,
    needs_commercial_fallback,
)

__all__ = [
    "WBProduct",
    "WBSize",
    "WBStock",
    "WBClient",
    "AsyncWBClient",
    "BasketResolver",
]

log = logging.getLogger("selleros.wb")

# --------------------------------------------------------------------------- #
# Константы
# --------------------------------------------------------------------------- #

DETAIL_URL = DETAIL_PRIMARY_URL  # card.wb.ru/cards/v4/detail
SUPPLIER_URL = "https://static-basket-01.wbbasket.ru/vol0/data/supplier-by-id/{sid}.json"
FEEDBACK_HOSTS = ("feedbacks1.wb.ru", "feedbacks2.wb.ru")


def _proxy_scheme_from_proxies(proxies: dict[str, str] | None) -> str:
    """Вытащить схему из dict curl_cffi proxies без пароля в логах."""
    if not proxies:
        try:
            from backend.config import WB_PROXY_SCHEME
            return (WB_PROXY_SCHEME or "socks5").strip().lower() or "socks5"
        except Exception:
            return "socks5"
    for key in ("https", "http", "all"):
        raw = (proxies.get(key) or "").strip()
        if "://" in raw:
            return raw.split("://", 1)[0].lower() or "socks5"
    try:
        from backend.config import WB_PROXY_SCHEME
        return (WB_PROXY_SCHEME or "socks5").strip().lower() or "socks5"
    except Exception:
        return "socks5"

#: Москва. Полный список — в ответе ``https://user-geo-data.wildberries.ru/get-geo-info``.
DEFAULT_DEST = -1257786

#: Максимум артикулов в одном запросе к detail-API.
MAX_BATCH = 100

#: Доступные размеры превью на CDN.
PHOTO_SIZES = ("big", "c516x688", "c246x328", "square", "tm")

#: Приблизительный размер скидки WB-кошелька. Меняется — вынесено в константу.
WALLET_DISCOUNT = 0.02

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}

# --------------------------------------------------------------------------- #
# Модели
# --------------------------------------------------------------------------- #


@dataclass
class WBStock:
    """Остаток на конкретном складе."""

    warehouse: int
    qty: int
    delivery_type: int | None = None
    priority: int | None = None


@dataclass
class WBSize:
    """Размер (у товаров без размерной сетки — один безымянный)."""

    name: str = ""
    orig_name: str = ""
    option_id: int | None = None
    chrt_id: int | None = None
    price: int | None = None
    old_price: int | None = None
    qty: int = 0
    stocks: list[WBStock] = field(default_factory=list)


@dataclass
class WBProduct:
    """Нормализованная карточка товара."""

    article: int

    # --- контент -----------------------------------------------------------
    title: str | None = None
    brand: str | None = None
    brand_id: int | None = None
    description: str | None = None
    vendor_code: str | None = None
    subject_name: str | None = None
    subject_root_name: str | None = None
    characteristics: dict[str, Any] = field(default_factory=dict)
    composition: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)

    # --- коммерция ---------------------------------------------------------
    price: int | None = None
    old_price: int | None = None
    discount: int | None = None
    wallet_price: int | None = None

    # --- репутация ---------------------------------------------------------
    rating: float | None = None
    feedbacks: int | None = None
    supplier: str | None = None
    supplier_id: int | None = None
    supplier_rating: float | None = None
    supplier_info: dict[str, Any] = field(default_factory=dict)

    # --- идентификаторы ----------------------------------------------------
    imt_id: int | None = None
    root_id: int | None = None
    basket: str | None = None

    # --- медиа -------------------------------------------------------------
    photo_count: int = 0
    photos: list[str] = field(default_factory=list)
    video: str | None = None

    # --- логистика ---------------------------------------------------------
    sizes: list[WBSize] = field(default_factory=list)
    total_qty: int = 0
    warehouses: list[int] = field(default_factory=list)

    # --- служебное ---------------------------------------------------------
    is_promo: bool = False
    scanned_at: float = field(default_factory=time.time)
    #: Откуда взяты данные: "live" (свежий запрос) | "history" (последний
    #: известный снимок из памяти ARGUS, источники сейчас недоступны).
    #: Добавлено для WB Engine — старый код это поле просто не читает.
    source: str = "live"
    #: Минимальная provenance-мета: field → {value, source, nm_id, verified, scope, ts}.
    #: Не влияет на старый код, который поле не читает.
    field_provenance: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def url(self) -> str:
        return f"https://www.wildberries.ru/catalog/{self.article}/detail.aspx"

    @property
    def in_stock(self) -> bool:
        return self.total_qty > 0

    def photo_urls(self, size: str = "big", ext: str = "webp") -> list[str]:
        """Ссылки на фото в нужном разрешении без повторного запроса."""
        if size not in PHOTO_SIZES:
            raise ValueError(f"size must be one of {PHOTO_SIZES}")
        if not self.basket:
            return []
        return [
            _photo_url(self.basket, self.article, i, size, ext)
            for i in range(1, self.photo_count + 1)
        ]

    def to_dict(self) -> dict[str, Any]:
        """Плоский dict — для JSON API, ClickHouse, Postgres jsonb."""
        data = asdict(self)
        data["url"] = self.url
        data["in_stock"] = self.in_stock
        return data

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)


# --------------------------------------------------------------------------- #
# Basket-резолвер
# --------------------------------------------------------------------------- #

#: Верхние границы ``vol`` для basket-01..34. Wildberries периодически
#: добавляет хосты, поэтому таблица — только *подсказка*: при промахе
#: включается перебор соседей, а удачный номер кешируется.
_BASKET_BOUNDS: tuple[int, ...] = (
    143, 287, 431, 719, 1007, 1061, 1115, 1169, 1313, 1601,
    1655, 1919, 2045, 2189, 2405, 2621, 2837, 3053, 3269, 3485,
    3701, 3917, 4133, 4349, 4565, 4877, 5189, 5501, 5813, 6125,
    6437, 6749, 7061, 7500,
)


class BasketResolver:
    """Определяет номер CDN-хоста по ``vol`` и запоминает удачные попадания."""

    def __init__(self, cache_path: str | os.PathLike[str] | None = None, max_basket: int = 40):
        self.max_basket = max_basket
        self.cache_path = str(cache_path) if cache_path else None
        self._cache: dict[int, str] = {}
        self._dirty = False
        if self.cache_path:
            self._load()

    # -- публичное ----------------------------------------------------------

    def predict(self, vol: int) -> int:
        """Ожидаемый номер basket по таблице границ."""
        return min(bisect.bisect_left(_BASKET_BOUNDS, vol) + 1, self.max_basket)

    def candidates(self, vol: int) -> list[str]:
        """Порядок перебора: кеш → предсказание → соседи по расходящейся спирали."""
        order: list[str] = []
        cached = self._cache.get(vol)
        if cached:
            order.append(cached)

        base = self.predict(vol)
        for delta in (0, 1, -1, 2, -2, 3, -3, 4, -4):
            value = base + delta
            if 1 <= value <= self.max_basket:
                token = f"{value:02d}"
                if token not in order:
                    order.append(token)
        return order

    def remember(self, vol: int, basket: str) -> None:
        if self._cache.get(vol) != basket:
            self._cache[vol] = basket
            self._dirty = True

    def save(self) -> None:
        """Атомарная запись кеша (безопасно при нескольких воркерах)."""
        if not (self.cache_path and self._dirty):
            return
        directory = os.path.dirname(os.path.abspath(self.cache_path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({str(k): v for k, v in self._cache.items()}, fh)
            os.replace(tmp, self.cache_path)
            self._dirty = False
        except OSError as exc:
            log.warning("basket cache save failed: %s", exc)
            if os.path.exists(tmp):
                os.unlink(tmp)

    # -- внутреннее ---------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self.cache_path, encoding="utf-8") as fh:  # type: ignore[arg-type]
                self._cache = {int(k): v for k, v in json.load(fh).items()}
            log.debug("basket cache loaded: %d entries", len(self._cache))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("basket cache load failed: %s", exc)


# --------------------------------------------------------------------------- #
# Чистые функции разбора (общие для sync и async клиентов)
# --------------------------------------------------------------------------- #


def _card_url(basket: str, article: int) -> str:
    return (
        f"https://basket-{basket}.wbbasket.ru"
        f"/vol{article // 100000}/part{article // 1000}/{article}/info/ru/card.json"
    )


def _photo_url(basket: str, article: int, index: int, size: str, ext: str) -> str:
    return (
        f"https://basket-{basket}.wbbasket.ru"
        f"/vol{article // 100000}/part{article // 1000}/{article}/images/{size}/{index}.{ext}"
    )


def _video_url(basket: str, article: int) -> str:
    return (
        f"https://basket-{basket}.wbbasket.ru"
        f"/vol{article // 100000}/part{article // 1000}/{article}/video/index.m3u8"
    )


def chunked(items: Sequence[int], size: int) -> Iterator[Sequence[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _extract_characteristics(card: dict[str, Any]) -> dict[str, Any]:
    """WB отдаёт характеристики в трёх разных схемах — собираем все."""
    chars: dict[str, Any] = {}
    groups = card.get("grouped_options") or []
    for group in groups:
        for opt in group.get("options") or []:
            name, value = opt.get("name"), opt.get("value")
            if name and value is not None:
                chars[name] = value
    for key in ("options", "full_characteristics"):
        for opt in card.get(key) or []:
            name, value = opt.get("name"), opt.get("value")
            if name and value is not None:
                chars.setdefault(name, value)
    return chars


def _photo_count(card: dict[str, Any]) -> int:
    media = card.get("media") or {}
    for key in ("photo_count", "photoCount"):
        if media.get(key):
            return int(media[key])
    for key in ("photo_files", "mediaFiles"):
        files = media.get(key) or card.get(key)
        if files:
            return len(files)
    return int(card.get("pics") or 0)


def _as_positive_int(value: Any) -> int | None:
    """Привести id к int > 0; мусор → None. Без выдуманных значений."""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def extract_imt_id(*sources: Any) -> int | None:
    """
    Достать imt_id из card.json / detail / search payload.

    Реальные ключи WB (по существующим ответам/докам):
      card.json: imt_id, imtId
      detail/search: root  (это и есть imtId для feedbacks*.wb.ru)

    Не подставляет article/nmId — это другой идентификатор.
    """
    for src in sources:
        if src is None:
            continue
        if isinstance(src, (int, float, str)):
            got = _as_positive_int(src)
            if got is not None:
                return got
            continue
        if not isinstance(src, dict):
            continue
        for key in ("imt_id", "imtId", "imtID", "root"):
            got = _as_positive_int(src.get(key))
            if got is not None:
                return got
        # редкий вложенный data{} — только реальный imt/root, НЕ subject_root_id
        # (subject_root_id = категория, не IMT)
        data = src.get("data")
        if isinstance(data, dict):
            for key in ("imt_id", "imtId", "imtID", "root"):
                got = _as_positive_int(data.get(key))
                if got is not None:
                    return got
    return None


def _sync_imt_root(product: WBProduct) -> None:
    """imt_id и root_id — взаимозаменяемые якоря для feedbacks API."""
    if product.imt_id is None and product.root_id is not None:
        product.imt_id = product.root_id
    if product.root_id is None and product.imt_id is not None:
        product.root_id = product.imt_id


def product_from_card(article: int, card: dict[str, Any], basket: str) -> WBProduct:
    """Собирает продукт из ``card.json`` (контентная часть)."""
    selling = card.get("selling") or {}
    data = card.get("data") or {}

    imt = extract_imt_id(card, data)
    # root_id = только реальный imt/root. subject_root_id — категория, не якорь отзывов.
    root = _as_positive_int(card.get("root")) or imt

    product = WBProduct(
        article=article,
        basket=basket,
        title=card.get("imt_name") or card.get("goods_name") or card.get("name"),
        description=card.get("description"),
        vendor_code=card.get("vendor_code") or card.get("nm_code"),
        brand=selling.get("brand_name") or card.get("selling", {}).get("brand"),
        supplier=selling.get("supplier_name"),
        supplier_id=selling.get("supplier_id"),
        imt_id=imt,
        root_id=root,
        subject_name=card.get("subj_name"),
        subject_root_name=card.get("subj_root_name"),
        characteristics=_extract_characteristics(card),
    )
    _sync_imt_root(product)

    compositions = card.get("compositions") or []
    product.composition = [
        c.get("name") for c in compositions if isinstance(c, dict) and c.get("name")
    ]
    product.colors = [
        c.get("name") for c in (card.get("colors") or []) if isinstance(c, dict) and c.get("name")
    ]

    product.photo_count = _photo_count(card)
    product.photos = product.photo_urls()
    if (card.get("media") or {}).get("has_video"):
        product.video = _video_url(basket, article)

    from backend.wb.provenance import note_field

    if product.description:
        note_field(
            product, "description", (product.description or "")[:80],
            "card.json", verified=True, scope="nm",
        )
    if product.characteristics:
        note_field(
            product, "characteristics", len(product.characteristics),
            "card.json", verified=True, scope="nm",
        )
    if product.photo_count:
        note_field(
            product, "photo_count", product.photo_count,
            "card.json", verified=True, scope="nm",
        )

    return product


def apply_detail(product: WBProduct, raw: dict[str, Any]) -> WBProduct:
    """Накладывает данные detail-API: цены, рейтинг, размеры, остатки."""
    from backend.wb.provenance import note_field, raw_nm_id

    raw_id = raw_nm_id(raw)
    if raw_id is not None and int(raw_id) != int(product.article):
        log.warning(
            "apply_detail refuse nm mismatch product=%s raw_id=%s",
            product.article,
            raw_id,
        )
        return product

    product.brand = raw.get("brand") or product.brand
    product.brand_id = raw.get("brandId")
    product.supplier = raw.get("supplier") or product.supplier
    product.supplier_id = raw.get("supplierId") or product.supplier_id
    product.supplier_rating = raw.get("supplierRating")

    # root из detail/search = imtId для feedbacks*.wb.ru
    detail_imt = extract_imt_id(raw)
    if detail_imt is not None:
        product.root_id = product.root_id or detail_imt
        product.imt_id = product.imt_id or detail_imt
    else:
        product.root_id = raw.get("root") or product.root_id
    _sync_imt_root(product)

    product.title = product.title or raw.get("name")
    product.is_promo = bool(raw.get("promoTextCard") or raw.get("panelPromoId"))

    # nm-specific first (nmReviewRating / nmFeedbacks), затем card-level
    rating = raw.get("nmReviewRating") or raw.get("reviewRating") or raw.get("rating")
    if rating:
        product.rating = round(float(rating), 2)
        note_field(
            product, "rating", product.rating, "card.wb.ru/detail",
            verified=True, scope="nm",
        )
        log.debug(
            "article=%s rating=%.2f (источник: %s)",
            product.article,
            product.rating,
            "nmReviewRating" if raw.get("nmReviewRating") is not None
            else "reviewRating" if raw.get("reviewRating") is not None
            else "rating",
        )
    feedbacks = (
        raw.get("nmFeedbacks")
        if raw.get("nmFeedbacks") is not None
        else raw.get("feedbacks")
    )
    if feedbacks is not None:
        product.feedbacks = int(feedbacks)
        note_field(
            product, "feedbacks", product.feedbacks, "card.wb.ru/detail",
            verified=True, scope="nm",
        )
        log.debug("article=%s feedbacks=%d", product.article, product.feedbacks)

    if not product.photo_count and raw.get("pics"):
        product.photo_count = int(raw["pics"])
        product.photos = product.photo_urls()
        note_field(
            product, "photo_count", product.photo_count, "card.wb.ru/detail.pics",
            verified=True, scope="nm",
        )

    warehouses: set[int] = set()
    total_qty = 0

    for raw_size in raw.get("sizes") or []:
        price_block = raw_size.get("price") or {}
        size = WBSize(
            name=raw_size.get("name") or "",
            orig_name=raw_size.get("origName") or "",
            option_id=raw_size.get("optionId"),
            chrt_id=raw_size.get("chrtId") or raw_size.get("optionId"),
        )
        # Цены приходят в копейках.
        basic = price_block.get("basic")
        final = price_block.get("product") or price_block.get("total")
        if basic:
            size.old_price = basic // 100
        if final:
            size.price = final // 100

        for raw_stock in raw_size.get("stocks") or []:
            qty = int(raw_stock.get("qty") or 0)
            if qty <= 0:
                continue
            wh = int(raw_stock.get("wh") or 0)
            size.stocks.append(
                WBStock(
                    warehouse=wh,
                    qty=qty,
                    delivery_type=raw_stock.get("dtype"),
                    priority=raw_stock.get("priority"),
                )
            )
            size.qty += qty
            warehouses.add(wh)

        total_qty += size.qty
        product.sizes.append(size)

    product.total_qty = raw.get("totalQuantity") or total_qty
    product.warehouses = sorted(warehouses)

    # Витринная цена = минимальная среди размеров в наличии.
    priced = [s for s in product.sizes if s.price] or []
    if priced:
        cheapest = min(priced, key=lambda s: s.price or 0)
        product.price = cheapest.price
        product.old_price = cheapest.old_price
        if product.price and product.old_price and product.old_price > product.price:
            product.discount = round((1 - product.price / product.old_price) * 100)
        else:
            product.discount = 0
        product.wallet_price = round(product.price * (1 - WALLET_DISCOUNT))
        note_field(
            product, "price", product.price, "card.wb.ru/detail",
            verified=True, scope="nm",
        )

        # Детальное логирование цен для отладки
        log.info(
            "Цены товара %s: price=%s руб, old_price=%s руб, discount=%s%%, "
            "размеров_с_ценой=%d, total_qty=%d",
            product.article, product.price, product.old_price, product.discount,
            len(priced), product.total_qty
        )
    else:
        log.warning(
            "article=%s НЕТ ЦЕН! sizes=%d, priced=0",
            product.article, len(product.sizes)
        )

    return product


# --------------------------------------------------------------------------- #
# Синхронный клиент
# --------------------------------------------------------------------------- #


class WBClient:
    """Синхронный клиент. Использовать как контекстный менеджер.

    >>> with WBClient() as wb:
    ...     product = wb.scan(211246754)
    """

    def __init__(
        self,
        *,
        dest: int = DEFAULT_DEST,
        timeout: float = 8.0,
        retries: int = 3,
        impersonate: str = "chrome124",
        basket_cache: str | os.PathLike[str] | None = ".wb_basket_cache.json",
        min_interval: float = 0.0,
        proxies: dict[str, str] | None = None,
        batch_size: int = MAX_BATCH,
    ):
        self.dest = dest
        self.timeout = timeout
        self.retries = max(1, retries)
        self.batch_size = min(batch_size, MAX_BATCH)
        self.min_interval = min_interval
        self.baskets = BasketResolver(basket_cache)
        self._proxies = proxies or {}
        self.session = requests.Session(
            headers=DEFAULT_HEADERS,
            impersonate=impersonate,
            proxies=self._proxies,
        )
        self._last_request = 0.0
        self._card_provider = ProductCardProvider(
            dest=dest,
            proxy_scheme=_proxy_scheme_from_proxies(self._proxies),
        )

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self) -> "WBClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self.baskets.save()
        self.session.close()

    # -- транспорт ----------------------------------------------------------

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(8.0, 0.4 * 2 ** (attempt - 1)) * (0.5 + random.random())

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        gap = time.monotonic() - self._last_request
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_request = time.monotonic()

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any | None:
        """GET с ретраями. ``None`` — не удалось получить 2xx."""
        response = self._get_response(url, params=params)
        if response is None:
            return None
        if response.status_code == 200:
            return response
        return None

    def _get_response(self, url: str, params: dict[str, Any] | None = None) -> Any | None:
        """GET: вернуть response при любом HTTP-статусе; None только при обрыве."""
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except Exception as exc:
                # Любая ошибка транспорта (не только HTTPTransportError —
                # curl_cffi может поднять и другие типы при обрыве
                # прокси/сети) не должна ронять параллельный fetch_card():
                # цена необязательна, а карточка (card.json) — нет.
                log.debug("GET %s failed (%d/%d): %s", url, attempt, self.retries, exc)
            else:
                if response.status_code == 200:
                    return response
                if response.status_code == 404:
                    return response
                if response.status_code == 429 or response.status_code >= 500:
                    log.debug("GET %s -> %s (%d/%d)", url, response.status_code, attempt, self.retries)
                    if attempt >= self.retries:
                        return response
                else:
                    log.warning("GET %s -> %s", url, response.status_code)
                    return response
            if attempt < self.retries:
                time.sleep(self._backoff(attempt))
        log.warning("GET %s: все %d попыток исчерпаны", url, self.retries)
        return None

    @staticmethod
    def _json(response: Any) -> dict[str, Any] | None:
        try:
            return response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            log.warning("невалидный JSON: %s", exc)
            return None

    # -- эндпоинты ----------------------------------------------------------

    def fetch_card(self, article: int) -> tuple[dict[str, Any] | None, str | None]:
        """Скачивает ``card.json``, подбирая basket-хост. Один вызов на товар."""
        vol = article // 100000
        for basket in self.baskets.candidates(vol):
            response = self._get(_card_url(basket, article))
            if response is None:
                continue
            payload = self._json(response)
            if payload:
                self.baskets.remember(vol, basket)
                log.debug("article=%s basket=%s", article, basket)
                return payload, basket
        log.info("card.json не найден для article=%s", article)
        return None, None

    def fetch_detail(self, articles: Sequence[int]) -> dict[int, dict[str, Any]]:
        """Цены/остатки/рейтинг батчами по 100 артикулов (primary card.wb.ru)."""
        result: dict[int, dict[str, Any]] = {}
        for chunk in chunked(list(articles), self.batch_size):
            part = self._card_provider.fetch_detail_primary(
                list(chunk),
                self._get_response,
                json_loads=self._json,
            )
            result.update(part)
        return result

    def _enrich_commercial(
        self,
        product: WBProduct,
        *,
        basket: str | None,
    ) -> None:
        """Graceful fallback коммерческих полей, если detail 403/пустой."""
        if not needs_commercial_fallback(product):
            return
        try:
            raw = self._card_provider.enrich_fallback(
                product.article,
                self._get_response,
                json_loads=self._json,
                name=product.title,
                basket=basket or product.basket,
                imt_id=product.imt_id or product.root_id,
            )
        except Exception as exc:
            log.warning(
                "product card fallback failed article=%s: %s",
                product.article,
                exc,
            )
            return
        if raw:
            apply_detail(product, raw)

    def fetch_feedbacks(self, imt_id: int) -> dict[str, Any] | None:
        """Детальная статистика отзывов (распределение по звёздам)."""
        for host in FEEDBACK_HOSTS:
            response = self._get(f"https://{host}/feedbacks/v1/{imt_id}")
            if response is None:
                continue
            payload = self._json(response)
            if payload and payload.get("feedbacks") is not None:
                return payload
        return None

    def fetch_supplier(self, supplier_id: int) -> dict[str, Any] | None:
        """Юрлицо продавца: ИНН, ОГРН, адрес, рейтинг продаж."""
        response = self._get(SUPPLIER_URL.format(sid=supplier_id))
        return self._json(response) if response else None

    # -- высокоуровневое ----------------------------------------------------

    def scan(
        self,
        article: int,
        *,
        with_feedbacks: bool = False,
        with_supplier: bool = False,
    ) -> WBProduct | None:
        results = self.scan_many(
            [article], with_feedbacks=with_feedbacks, with_supplier=with_supplier
        )
        return results[0] if results else None

    def scan_many(
        self,
        articles: Iterable[int],
        *,
        with_feedbacks: bool = False,
        with_supplier: bool = False,
    ) -> list[WBProduct]:
        """Массовое сканирование: N карточек + ceil(N/100) запросов на цены."""
        unique = list(dict.fromkeys(int(a) for a in articles))
        if not unique:
            return []

        details = self.fetch_detail(unique)
        products: list[WBProduct] = []

        for article in unique:
            card, basket = self.fetch_card(article)
            if card is None:
                if article not in details:
                    continue
                # Контента нет, но товар живой — отдаём то, что есть.
                product = WBProduct(article=article)
            else:
                product = product_from_card(article, card, basket or "")

            if article in details:
                apply_detail(product, details[article])

            self._enrich_commercial(product, basket=basket)

            if with_feedbacks and product.imt_id:
                stats = self.fetch_feedbacks(product.imt_id)
                if stats:
                    from backend.wb.product_card_provider import raw_from_feedbacks_meta
                    from backend.wb.provenance import note_field

                    # IMT-wide valuation/feedbackCount — только если nm ownership доказан
                    meta = raw_from_feedbacks_meta(stats, product.article)
                    if product.rating is None and meta.get("reviewRating") is not None:
                        product.rating = float(meta["reviewRating"])
                        note_field(
                            product, "rating", product.rating,
                            "feedbacks1 (nm-safe)", verified=True, scope="nm",
                        )
                    if product.feedbacks is None and meta.get("feedbacks") is not None:
                        product.feedbacks = int(meta["feedbacks"])
                        note_field(
                            product, "feedbacks", product.feedbacks,
                            "feedbacks1 (nm-safe)", verified=True, scope="nm",
                        )

            if with_supplier and product.supplier_id:
                info = self.fetch_supplier(product.supplier_id)
                if info:
                    product.supplier_info = info
                    product.supplier = info.get("supplierName") or product.supplier

            products.append(product)

        self.baskets.save()
        return products


# --------------------------------------------------------------------------- #
# Асинхронный клиент — для массового сканирования
# --------------------------------------------------------------------------- #


class AsyncWBClient:
    """Асинхронная версия. Для прайс-мониторинга тысяч SKU.

    >>> async with AsyncWBClient(concurrency=16) as wb:
    ...     products = await wb.scan_many(articles)
    """

    def __init__(
        self,
        *,
        dest: int = DEFAULT_DEST,
        timeout: float = 8.0,
        retries: int = 3,
        concurrency: int = 8,
        impersonate: str = "chrome124",
        basket_cache: str | os.PathLike[str] | None = ".wb_basket_cache.json",
        proxies: dict[str, str] | None = None,
        batch_size: int = MAX_BATCH,
    ):
        self.dest = dest
        self.timeout = timeout
        self.retries = max(1, retries)
        self.batch_size = min(batch_size, MAX_BATCH)
        self.baskets = BasketResolver(basket_cache)
        self._semaphore = asyncio.Semaphore(concurrency)
        self._impersonate = impersonate
        self._proxies = proxies or {}
        self.session: requests.AsyncSession | None = None
        self._card_provider = ProductCardProvider(
            dest=dest,
            proxy_scheme=_proxy_scheme_from_proxies(self._proxies),
        )

    async def __aenter__(self) -> "AsyncWBClient":
        self.session = requests.AsyncSession(
            headers=DEFAULT_HEADERS,
            impersonate=self._impersonate,
            proxies=self._proxies,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        self.baskets.save()
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> Any | None:
        response = await self._get_response(url, params=params)
        if response is None:
            return None
        if response.status_code == 200:
            return response
        return None

    async def _get_response(self, url: str, params: dict[str, Any] | None = None) -> Any | None:
        """GET: response при любом HTTP-статусе; None только при обрыве транспорта."""
        assert self.session is not None, "используйте `async with AsyncWBClient()`"
        async with self._semaphore:
            for attempt in range(1, self.retries + 1):
                try:
                    response = await self.session.get(url, params=params, timeout=self.timeout)
                except Exception as exc:
                    # См. комментарий в WBClient._get(): любая ошибка
                    # транспорта (не только HTTPTransportError) не должна
                    # пробрасываться наружу и рвать asyncio.gather() в
                    # scan_many() — иначе один упавший fetch_detail()
                    # (цена) выбивает уже готовый fetch_card() (контент).
                    log.debug("GET %s failed (%d/%d): %s", url, attempt, self.retries, exc)
                else:
                    if response.status_code == 200:
                        return response
                    if response.status_code == 404:
                        return response
                    if response.status_code != 429 and response.status_code < 500:
                        log.warning("GET %s -> %s", url, response.status_code)
                        return response
                    log.debug("GET %s -> %s", url, response.status_code)
                    if attempt >= self.retries:
                        return response
                if attempt < self.retries:
                    await asyncio.sleep(WBClient._backoff(attempt))
        log.warning("GET %s: все %d попыток исчерпаны", url, self.retries)
        return None

    async def fetch_card(self, article: int) -> tuple[dict[str, Any] | None, str | None]:
        vol = article // 100000
        for basket in self.baskets.candidates(vol):
            response = await self._get(_card_url(basket, article))
            if response is None:
                continue
            payload = WBClient._json(response)
            if payload:
                self.baskets.remember(vol, basket)
                return payload, basket
        return None, None

    async def fetch_detail(self, articles: Sequence[int]) -> dict[int, dict[str, Any]]:
        chunks = list(chunked(list(articles), self.batch_size))

        async def one(chunk: Sequence[int]) -> dict[int, dict[str, Any]]:
            return await self._card_provider.fetch_detail_primary_async(
                list(chunk),
                self._get_response,
                json_loads=WBClient._json,
            )

        result: dict[int, dict[str, Any]] = {}
        for part in await asyncio.gather(*(one(c) for c in chunks)):
            result.update(part)
        return result

    async def _enrich_commercial(
        self,
        product: WBProduct,
        *,
        basket: str | None,
    ) -> None:
        if not needs_commercial_fallback(product):
            return
        try:
            raw = await self._card_provider.enrich_fallback_async(
                product.article,
                self._get_response,
                json_loads=WBClient._json,
                name=product.title,
                basket=basket or product.basket,
                imt_id=product.imt_id or product.root_id,
            )
        except Exception as exc:
            log.warning(
                "product card fallback failed article=%s: %s",
                product.article,
                exc,
            )
            return
        if raw:
            apply_detail(product, raw)

    async def scan_many(self, articles: Iterable[int]) -> list[WBProduct]:
        unique = list(dict.fromkeys(int(a) for a in articles))
        if not unique:
            return []

        details, cards = await asyncio.gather(
            self.fetch_detail(unique),
            asyncio.gather(*(self.fetch_card(a) for a in unique)),
        )

        products: list[WBProduct] = []
        for article, (card, basket) in zip(unique, cards):
            if card is None and article not in details:
                continue
            product = (
                product_from_card(article, card, basket or "")
                if card
                else WBProduct(article=article)
            )
            if article in details:
                apply_detail(product, details[article])
            await self._enrich_commercial(product, basket=basket)
            products.append(product)

        self.baskets.save()
        return products

    async def scan(self, article: int) -> WBProduct | None:
        products = await self.scan_many([article])
        return products[0] if products else None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _print_human(product: WBProduct) -> None:
    line = "─" * 64
    print(line)
    print(f"{product.title or '—'}  [{product.article}]")
    print(line)
    print(f"Бренд:        {product.brand or '—'}")
    print(f"Категория:    {product.subject_name or '—'}")
    print(f"Цена:         {product.price or '—'} ₽", end="")
    if product.old_price and product.discount:
        print(f"  (было {product.old_price} ₽, −{product.discount}%)")
    else:
        print()
    if product.wallet_price:
        print(f"С кошельком:  ~{product.wallet_price} ₽")
    print(f"Рейтинг:      {product.rating or '—'} ({product.feedbacks or 0} отзывов)")
    print(f"Продавец:     {product.supplier or '—'} (id={product.supplier_id})")
    print(f"Остаток:      {product.total_qty} шт на {len(product.warehouses)} складах")
    print(f"Размеров:     {len(product.sizes)}")
    print(f"Фото:         {product.photo_count}")
    for url in product.photos[:3]:
        print(f"  {url}")
    print(f"Характеристик: {len(product.characteristics)}")
    for key, value in list(product.characteristics.items())[:10]:
        print(f"  {key}: {value}")
    if product.description:
        preview = product.description[:300].replace("\n", " ")
        print(f"\nОписание: {preview}{'…' if len(product.description) > 300 else ''}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SellerOS · парсер карточек Wildberries")
    parser.add_argument("articles", nargs="+", type=int, help="артикулы (nmId)")
    parser.add_argument("--dest", type=int, default=DEFAULT_DEST, help="регион доставки")
    parser.add_argument("--json", action="store_true", help="вывести JSON")
    parser.add_argument("--feedbacks", action="store_true", help="подтянуть статистику отзывов")
    parser.add_argument("--supplier", action="store_true", help="подтянуть юрлицо продавца")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    with WBClient(dest=args.dest) as client:
        products = client.scan_many(
            args.articles,
            with_feedbacks=args.feedbacks,
            with_supplier=args.supplier,
        )

    if not products:
        print("Ничего не найдено", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([p.to_dict() for p in products], ensure_ascii=False, indent=2))
    else:
        for product in products:
            _print_human(product)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())