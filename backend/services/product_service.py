"""
ProductService — ГЛАВНОЕ место получения товара в проекте.

Правило проекта:
    Хендлеры и сервисы НЕ знают про BrowserPool, парсеры и API.
    Товар берётся только так:

        product = await product_service.get_product("wildberries", article)

Внутри — список провайдеров по маркетплейсам.
Провайдеры опрашиваются по очереди (по приоритету):
кто первый ответил — того и товар.

Если первый источник отдал контент без цены/рейтинга/числа отзывов,
следующие провайдеры дозаполняют ТОЛЬКО пустые публичные коммерческие
поля (merge), не затирая уже найденное и не трогая SellerData.

Дополнительно (nm provenance):
  - не мержим разные nm_id
  - browser/cache HIT не блокирует CDN verify/refresh
  - nm-verified commercial побеждает unverified/IMT
  - CDN content (desc/chars/photo_count) обновляет слабый browser snapshot
  - optional PublicProductCache обновляется fresher verified snapshot'ом

Заменить источник = зарегистрировать другой провайдер. Одна строка.
"""

import logging

from backend.providers.base import ProductProvider
from backend.wb.cdn_provider import WBProduct
from backend.wb.product_card_provider import needs_commercial_fallback
from backend.wb.provenance import (
    is_browser_source,
    is_nm_verified,
    is_unverified_or_imt,
    looks_sitewide_description,
    note_field,
    same_nm,
)

log = logging.getLogger("selleros.products")

#: Публичные коммерческие поля карточки — merge только None → value
#: (кроме override unverified/IMT → nm-verified).
_COMMERCIAL_FIELDS = (
    "price",
    "old_price",
    "discount",
    "wallet_price",
    "rating",
    "feedbacks",
    "supplier",
    "supplier_id",
    "supplier_rating",
)


def merge_public_commercial(base: WBProduct, extra: WBProduct) -> WBProduct:
    """
    Дозаполнить пустые публичные коммерческие поля из extra в base.
    Не трогает title/description/photos/characteristics и SellerData.
    Отказывает merge при разных nm_id.
    nm-verified extra может заменить unverified/IMT значения base.
    """
    if not same_nm(base, extra):
        log.warning(
            "merge_public_commercial refuse different nm_id base=%s extra=%s",
            getattr(base, "article", None),
            getattr(extra, "article", None),
        )
        return base

    for name in _COMMERCIAL_FIELDS:
        base_val = getattr(base, name, None)
        extra_val = getattr(extra, name, None)
        if extra_val in (None, "", [], {}):
            continue
        if base_val in (None, "", [], {}):
            setattr(base, name, extra_val)
            _copy_prov(base, extra, name)
            continue
        # override: unverified/IMT base ← nm-verified extra
        if is_unverified_or_imt(base, name) and is_nm_verified(extra, name):
            setattr(base, name, extra_val)
            _copy_prov(base, extra, name)
            log.info(
                "nm-verified %s overrides unverified base for article=%s (%s → %s)",
                name, base.article, base_val, extra_val,
            )
    # imt/root — якоря reviews; None не затираем уже найденное.
    if getattr(base, "imt_id", None) is None and getattr(extra, "imt_id", None):
        base.imt_id = extra.imt_id
    if getattr(base, "root_id", None) is None and getattr(extra, "root_id", None):
        base.root_id = extra.root_id
    return base


def merge_canonical_content(base: WBProduct, extra: WBProduct) -> WBProduct:
    """
    Подтянуть CDN/canonical content в browser snapshot:
    description, characteristics, photo_count (+ photos URLs).
    Только same nm_id. Не затирает хорошие canonical значения слабым DOM.
    """
    if not same_nm(base, extra):
        log.warning(
            "merge_canonical_content refuse different nm_id base=%s extra=%s",
            getattr(base, "article", None),
            getattr(extra, "article", None),
        )
        return base

    # description: prefer card.json over og:/sitewide
    extra_desc = getattr(extra, "description", None)
    base_desc = getattr(base, "description", None)
    if extra_desc and (
        not base_desc
        or looks_sitewide_description(base_desc)
        or (is_browser_source(base) and len(str(extra_desc)) > len(str(base_desc or "")))
        or is_nm_verified(extra, "description")
    ):
        if not looks_sitewide_description(extra_desc):
            base.description = extra_desc
            _copy_prov(base, extra, "description")

    # characteristics: prefer richer canonical
    extra_chars = getattr(extra, "characteristics", None) or {}
    base_chars = getattr(base, "characteristics", None) or {}
    if isinstance(extra_chars, dict) and extra_chars:
        if not base_chars or (
            is_browser_source(base) and len(extra_chars) >= len(base_chars)
        ) or is_nm_verified(extra, "characteristics"):
            if len(extra_chars) >= len(base_chars or {}):
                base.characteristics = dict(extra_chars)
                _copy_prov(base, extra, "characteristics")

    # photo_count: canonical pics, never DOM imgs[:20]
    extra_pc = getattr(extra, "photo_count", None) or 0
    base_pc = getattr(base, "photo_count", None) or 0
    try:
        extra_pc_i = int(extra_pc)
        base_pc_i = int(base_pc)
    except (TypeError, ValueError):
        extra_pc_i, base_pc_i = 0, 0
    if extra_pc_i > 0 and (
        base_pc_i <= 0
        or is_nm_verified(extra, "photo_count")
        or (is_browser_source(base) and not is_nm_verified(base, "photo_count"))
    ):
        base.photo_count = extra_pc_i
        _copy_prov(base, extra, "photo_count")
        extra_photos = getattr(extra, "photos", None) or []
        if extra_photos:
            base.photos = list(extra_photos)

    # basket / brand / title gaps
    if not getattr(base, "basket", None) and getattr(extra, "basket", None):
        base.basket = extra.basket
    if not getattr(base, "brand", None) and getattr(extra, "brand", None):
        base.brand = extra.brand
    if not getattr(base, "title", None) and getattr(extra, "title", None):
        base.title = extra.title

    return base


def _copy_prov(base: WBProduct, extra: WBProduct, field: str) -> None:
    extra_prov = getattr(extra, "field_provenance", None) or {}
    if field in extra_prov and isinstance(extra_prov[field], dict):
        if getattr(base, "field_provenance", None) is None:
            base.field_provenance = {}
        base.field_provenance[field] = dict(extra_prov[field])
    else:
        note_field(
            base,
            field,
            getattr(base, field, None),
            getattr(extra, "source", None) or "provider",
            verified=not is_browser_source(extra),
            scope="nm",
        )


def _needs_canonical_refresh(product: WBProduct | None) -> bool:
    """
    Нужен ли ещё один проход (CDN) после browser.

    Главный кейс смешивания — browser_cache HIT с IMT/DOM-мусором.
    Свежий browser fetch с полным commercial не гоняем в HTTP
    (иначе каждый HIT+success удваивает нагрузку).
    """
    if product is None:
        return False
    src = (getattr(product, "source", None) or "").strip().lower()
    if src == "browser_cache":
        return True
    prov = getattr(product, "field_provenance", None) or {}
    if not isinstance(prov, dict):
        return False
    for f in ("price", "rating", "feedbacks"):
        meta = prov.get(f)
        if not isinstance(meta, dict):
            continue
        if meta.get("verified") is False or meta.get("scope") == "imt":
            return True
    return False


def has_public_commercial_minimum(product) -> bool:
    """Есть ли публичный минимум карточки: цена + рейтинг + число отзывов."""
    if product is None:
        return False
    return (
        getattr(product, "price", None) is not None
        and getattr(product, "rating", None) is not None
        and getattr(product, "feedbacks", None) is not None
    )


def missing_public_commercial_fields(product) -> list[str]:
    """Какие CARD commercial-поля отсутствуют (price/rating/feedbacks)."""
    if product is None:
        return ["price", "rating", "feedbacks"]
    missing: list[str] = []
    if getattr(product, "price", None) is None:
        missing.append("price")
    if getattr(product, "rating", None) is None:
        missing.append("rating")
    if getattr(product, "feedbacks", None) is None:
        missing.append("feedbacks")
    return missing


def has_verified_public_commercial(product) -> bool:
    """
    Verified CARD commercial: price+rating+feedbacks присутствуют и
    nm-verified (browser.detail / DOM nm-proof и т.п.).
    """
    if not has_public_commercial_minimum(product):
        return False
    return all(is_nm_verified(product, f) for f in ("price", "rating", "feedbacks"))


def _clear_unproven_commercial(product: WBProduct) -> list[str]:
    """
    Сбросить price/rating/feedbacks без nm-proof.

    Legacy browser_cache без provenance и поля с verified=False / scope=imt
    не должны оставаться как «факт карточки».

    Browser nm-matched detail / DOM-with-nm-proof (verified=True, scope=nm)
    НЕ очищаем — это канонический fallback при card.wb.ru 403.
    """
    cleared: list[str] = []
    src = (getattr(product, "source", None) or "").strip().lower()
    prov = getattr(product, "field_provenance", None) or {}
    if not isinstance(prov, dict):
        prov = {}

    for name in ("price", "rating", "feedbacks", "old_price", "discount"):
        val = getattr(product, name, None)
        if val in (None, "", [], {}):
            continue
        if is_nm_verified(product, name):
            # browser.detail / browser.dom (nm-proved) / card.wb.ru — keep
            continue
        meta = prov.get(name) if isinstance(prov.get(name), dict) else None
        drop = False
        if meta is not None and (
            meta.get("verified") is False or meta.get("scope") == "imt"
        ):
            drop = True
        elif src in ("browser_cache", "browser") and meta is None:
            # legacy HIT / browser без provenance — не доверяем как nm-specific
            drop = True
        if drop:
            setattr(product, name, None)
            cleared.append(name)
            if getattr(product, "field_provenance", None) is None:
                product.field_provenance = {}
            product.field_provenance[name] = {
                "value": None,
                "source": "cleared_unproven",
                "nm_id": int(product.article),
                "verified": False,
                "scope": "nm",
                "cache": src,
                "prev": val,
            }
    return cleared


class ProductService:

    def __init__(self):
        # {"wildberries": [провайдер1, провайдер2], "ozon": [...]}
        self._providers: dict[str, list[ProductProvider]] = {}
        #: optional PublicProductCache — обновлять fresher verified snapshot
        self._public_cache = None
        #: last Source Fetch Policy decision (tests / live diagnostics)
        self.last_fetch_decision: dict | None = None

    def set_public_cache(self, cache) -> None:
        """Подключить PublicProductCache для refresh после CDN verify."""
        self._public_cache = cache

    def register(self, provider: ProductProvider, priority: int = 10):
        """
        Добавить источник товаров.
        Чем меньше priority — тем раньше опрашивается.
        """
        chain = self._providers.setdefault(provider.marketplace, [])
        chain.append((priority, provider))
        chain.sort(key=lambda item: item[0])

        log.info(
            "Провайдер подключён: %s (%s, приоритет %d)",
            provider.name, provider.marketplace, priority,
        )

    async def get_product_snapshot(
        self,
        marketplace: str,
        article: int,
        *,
        session_product: WBProduct | None = None,
        force_refresh: bool = False,
    ) -> WBProduct | None:
        """
        Canonical entry for card data (Source Fetch Policy).

        Reuses session / public-cache verified nm snapshot within TTL so
        Browser is not called again on analysis / discuss / Advisor / reopen.
        Sole path that may trigger Browser within a fresh TTL window.
        """
        from backend.services.source_fetch_policy import try_reuse_verified_snapshot

        article = int(article)
        decision = try_reuse_verified_snapshot(
            article,
            session_product=session_product,
            public_cache=self._public_cache,
            force_refresh=force_refresh,
        )
        self.last_fetch_decision = {
            "article": article,
            "cache_status": decision.cache_status,
            "browser_allowed": decision.browser_allowed,
            "reason": decision.reason,
            "reused": decision.reuse_product is not None,
        }
        if decision.reuse_product is not None and not decision.browser_allowed:
            return decision.reuse_product

        return await self.get_product(marketplace, article)

    async def get_product(
        self,
        marketplace: str,
        article: int,
    ) -> WBProduct | None:
        marketplace = marketplace.lower()
        article = int(article)

        chain = self._providers.get(marketplace, [])

        if not chain:
            log.warning("Нет провайдеров для %s", marketplace)
            return None

        merged: WBProduct | None = None
        primary_source: str | None = None
        saw_non_browser = False
        improved = False

        for _, provider in chain:

            if not await provider.is_available():
                if provider.name == "browser":
                    log.info("fallback to WB Engine: yes")
                continue

            product = await provider.get_product(article)

            if product is None:
                if provider.name == "browser":
                    log.info("fallback to WB Engine: yes")
                continue

            # жёстко: провайдер обязан вернуть тот же nm_id
            try:
                if int(getattr(product, "article", -1)) != article:
                    log.warning(
                        "Товар %s: провайдер %s вернул другой nm_id=%s — skip",
                        article, provider.name, getattr(product, "article", None),
                    )
                    continue
            except (TypeError, ValueError):
                continue

            if merged is None:
                merged = product
                primary_source = provider.name
                log.info(
                    "Товар %s получен через %s",
                    article, provider.name,
                )
            else:
                before_gap = needs_commercial_fallback(merged)
                merge_public_commercial(merged, product)
                merge_canonical_content(merged, product)
                improved = True
                if not is_browser_source(product):
                    saw_non_browser = True
                log.info(
                    "Товар %s: дозаполнены/верифицированы поля из %s "
                    "(gap_before=%s gap_after=%s)",
                    article,
                    provider.name,
                    before_gap,
                    needs_commercial_fallback(merged),
                )

            if not is_browser_source(product) and provider.name != "browser":
                saw_non_browser = True

            complete = has_public_commercial_minimum(merged)
            # browser_cache HIT не должен блокировать CDN verify/refresh
            if complete and not _needs_canonical_refresh(merged):
                log.info(
                    "Товар %s: публичные коммерческие поля полные (primary=%s)",
                    article,
                    primary_source,
                )
                break
            if complete and saw_non_browser:
                log.info(
                    "Товар %s: полные поля после canonical verify (primary=%s)",
                    article,
                    primary_source,
                )
                break

        if merged is not None:
            cleared = _clear_unproven_commercial(merged)
            if cleared:
                improved = True
                log.info(
                    "Товар %s: cleared unproven commercial %s",
                    article,
                    cleared,
                )
            log.info(
                "Товар %s: итог (primary=%s price=%s rating=%s feedbacks=%s "
                "photo_count=%s chars=%s)",
                article,
                primary_source,
                merged.price,
                merged.rating,
                merged.feedbacks,
                getattr(merged, "photo_count", None),
                len(getattr(merged, "characteristics", None) or {}),
            )
            if improved and self._public_cache is not None:
                try:
                    self._public_cache.set_product(merged)
                    log.info(
                        "Товар %s: PublicProductCache refreshed with verified snapshot",
                        article,
                    )
                except Exception as exc:
                    log.debug("public cache refresh skip: %s", exc)
            return merged

        log.info("Товар %s не найден ни в одном источнике", article)
        return None
