"""Thin Mini App HTTP adapters.

Wraps existing Advisor / ActionService / FormulaEngine / TimeService / KnowledgeChat.
Does not rewrite Advisor math, funnel, CI, browser, CDN, or WB publish.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.advisor_cards import first_screen_cards
from backend.api.miniapp_catalog import (
    parse_article_input,
    product_payload,
    ready_memory,
    remember_image,
    seller_fields,
    serialize_memory_product,
    upsert_seller_product,
)
from backend.api.miniapp_copy import format_short_reply, human_funnel_status
from backend.api.miniapp_store import DEFAULT_MISSIONS, DEFAULT_SCHEDULE, MiniAppStore
from backend.auth.deps import require_session
from backend.auth.session import SellerSession
from backend.foundation.action_models import ActionType
from backend.foundation.formula_engine import FormulaEngine

log = logging.getLogger("selleros.api.miniapp")

router = APIRouter(prefix="/api", tags=["miniapp"])

MISSION_META = [
    {"id": "profile", "title": "Профиль продавца", "hint": "Тип, имя, категория", "to": "/settings"},
    {"id": "first_product", "title": "Добавить товар", "hint": "Ссылка WB или nmID — ключ кабинета не обязателен", "to": "/products"},
    {"id": "first_analysis", "title": "Первый разбор Argus", "hint": "Карточки: вывод / цифры / делать", "to": "/products"},
    {"id": "first_action", "title": "Принять действие", "hint": "Идея ≠ действие, пока не нажали Принять", "to": "/products"},
    {"id": "dashboard_ready", "title": "Кабинет готов", "hint": "Здоровье карточек на главной", "to": "/"},
    {"id": "ctr_lesson", "title": "Урок CTR/CVR", "hint": "Формулы, которыми пользуется ARGUS", "to": "/lesson"},
    {"id": "wb_connect", "title": "Подключить WB позже", "hint": "Не блокирует разбор публичных карточек", "to": "/settings"},
]


def _prefs(request: Request) -> MiniAppStore:
    store = getattr(request.app.state, "miniapp_store", None)
    if store is None:
        store = MiniAppStore()
        request.app.state.miniapp_store = store
    return store


def _actions(request: Request):
    svc = getattr(request.app.state, "action_service", None)
    if svc is None:
        from backend.foundation.action_service import ActionService

        svc = ActionService()
        request.app.state.action_service = svc
    return svc


def _dashboard(request: Request):
    return getattr(request.app.state, "dashboard_service", None)


def _onboarding(request: Request):
    return getattr(request.app.state, "onboarding_service", None)


def _analyzer(request: Request):
    dash = _dashboard(request)
    if dash is not None:
        return getattr(dash, "_analyzer", None)
    return None


def _memory(request: Request):
    mem = getattr(request.app.state, "memory_store", None)
    if mem is not None:
        return mem
    dash = _dashboard(request)
    if dash is not None and getattr(dash, "_memory", None) is not None:
        return dash._memory
    ob = _onboarding(request)
    if ob is not None:
        return getattr(ob, "memory", None)
    return None


def _product_service(request: Request):
    svc = getattr(request.app.state, "product_service", None)
    if svc is not None:
        return svc
    ob = _onboarding(request)
    if ob is not None:
        return getattr(ob, "product_service", None)
    return None


def _resolve_article(body) -> Optional[int]:
    if getattr(body, "article", None):
        return int(body.article)
    for raw in (getattr(body, "url", None), getattr(body, "text", None)):
        parsed = parse_article_input(raw)
        if parsed:
            return parsed
    return None


def _skip_num(raw: Optional[str], *, as_int: bool = False):
    if raw is None:
        return None
    text = str(raw).strip()
    if text in ("", "-", "—", "нет", "skip"):
        return None
    text = text.replace(",", ".").replace("%", "").replace(" ", "")
    try:
        val = float(text)
    except ValueError:
        return None
    if as_int:
        return int(val)
    return val


class _SellerOverlay:
    """Duck-typed seller_data: MemoryStore row + optional clicks for this request."""

    def __init__(self, rec: Any, extra: dict[str, Any] | None = None):
        self._rec = rec
        self._extra = extra or {}

    def __getattr__(self, name: str):
        if name in self._extra and self._extra[name] is not None:
            return self._extra[name]
        return getattr(self._rec, name, None)


def _seller_int(session: SellerSession) -> int:
    try:
        return int(session.seller_id)
    except (TypeError, ValueError):
        return abs(hash(session.seller_id)) % (10**9)


KNOWN_ZONES = [
    {"id": "Europe/Kaliningrad", "label": "UTC+2 Калининград"},
    {"id": "Europe/Moscow", "label": "UTC+3 Москва"},
    {"id": "Europe/Samara", "label": "UTC+4 Самара"},
    {"id": "Asia/Yekaterinburg", "label": "UTC+5 Екатеринбург"},
    {"id": "Asia/Omsk", "label": "UTC+6 Омск"},
    {"id": "Asia/Krasnoyarsk", "label": "UTC+7 Красноярск"},
    {"id": "Asia/Irkutsk", "label": "UTC+8 Иркутск"},
    {"id": "Asia/Yakutsk", "label": "UTC+9 Якутск"},
    {"id": "Asia/Vladivostok", "label": "UTC+10 Владивосток"},
    {"id": "Asia/Magadan", "label": "UTC+11 Магадан"},
    {"id": "Asia/Kamchatka", "label": "UTC+12 Камчатка"},
    {"id": "UTC", "label": "UTC"},
]


class ProfileUpdate(BaseModel):
    entity: Optional[str] = None
    marketplaces: Optional[list[str]] = None
    category: Optional[str] = None
    display_name: Optional[str] = None


class TimeSettingsUpdate(BaseModel):
    tz: Optional[str] = None
    push_enabled: Optional[bool] = None
    reminder_hour: Optional[int] = Field(default=None, ge=0, le=23)
    schedule: Optional[dict[str, Any]] = None


class CatalogAddBody(BaseModel):
    article: Optional[int] = Field(default=None, gt=0)
    url: Optional[str] = None
    text: Optional[str] = None


class PublicAnalyzeBody(BaseModel):
    article: Optional[int] = Field(default=None, gt=0)
    url: Optional[str] = None
    text: Optional[str] = None


class SellerDataBody(BaseModel):
    ctr: Optional[str] = None
    cvr: Optional[str] = None
    impressions: Optional[str] = None
    views: Optional[str] = None
    clicks: Optional[str] = None
    sales: Optional[str] = None
    orders: Optional[str] = None
    returns: Optional[str] = None
    ads: Optional[str] = None
    cogs: Optional[str] = None
    commission: Optional[str] = None
    logistics: Optional[str] = None
    storage: Optional[str] = None
    period: Optional[str] = None


class FormulaLessonBody(BaseModel):
    impressions: Optional[float] = None
    clicks: Optional[float] = None
    orders: Optional[float] = None
    ctr: Optional[float] = None
    cvr: Optional[float] = None


class ActionProposeBody(BaseModel):
    article: int = Field(..., gt=0)
    recommendation: str = Field(..., min_length=1)
    action_type: str = "OTHER"
    expected_effect: Optional[str] = None


class ActionDeferBody(BaseModel):
    days: float = Field(default=3.0, ge=0.5, le=30)


class ChatBody(BaseModel):
    text: str = Field(..., min_length=1)
    article: Optional[int] = None
    chip: Optional[str] = None


class StickyBody(BaseModel):
    article: Optional[int] = None


class MissionBody(BaseModel):
    skipped: Optional[bool] = None


def _card_from_catalog(data: dict[str, Any], article: int):
    class _Card:
        def __init__(self) -> None:
            photos = [data["image"]] if data.get("image") else []
            self.id = int(article)
            self.article = int(article)
            self.name = data.get("title") or str(article)
            self.title = self.name
            self.brand = data.get("brand") or ""
            self.seller = ""
            self.price = int(data.get("price") or 0)
            self.old_price = data.get("old_price")
            self.discount = None
            self.rating = float(data.get("rating") or 0)
            self.reviews = int(data.get("reviews_count") or 0)
            self.feedbacks = self.reviews
            self.photos = photos
            self.description = str(data.get("description") or "")
            self.characteristics = {}
            self.sizes = []
            self.subject_name = data.get("subject_name")
            self.subject_id = None

        def __getattr__(self, _name: str):
            return None

    return _Card()


def _public_action(a) -> dict[str, Any]:
    d = a.to_dict()
    return {
        "action_id": d["action_id"],
        "article": d["article"],
        "action_type": d["action_type"],
        "recommendation": d["recommendation"],
        "status": d["status"],
        "verification_status": d.get("verification_status"),
        "check_after": d.get("check_after"),
        "reminder_at": d.get("reminder_at"),
        "expected_effect": d.get("expected_effect"),
        "created_at": d.get("created_at"),
        "accepted_at": d.get("accepted_at"),
        "executed_at": d.get("executed_at"),
        "kind": "ACTION",
    }


def _merge_missions(prefs: dict[str, Any], onboarding: dict[str, Any] | None) -> dict[str, bool]:
    missions = dict(prefs.get("missions") or {k: False for k in DEFAULT_MISSIONS})
    if onboarding:
        status = (onboarding.get("status") or "").upper()
        steps = onboarding.get("steps") or {}
        if onboarding.get("wb_connected") or steps.get("wb_connect") or status not in ("", "NEW"):
            if status in ("WB_CONNECTED", "FIRST_PRODUCT_ADDED", "READY"):
                missions["wb_connect"] = True
        if onboarding.get("has_product") or steps.get("first_product"):
            missions["first_product"] = True
        if onboarding.get("has_analysis") or steps.get("first_analysis") or status == "READY":
            missions["first_analysis"] = True
        if status == "READY" or steps.get("dashboard_ready"):
            missions["dashboard_ready"] = True
    entity = (prefs.get("entity") or "").strip()
    category = (prefs.get("category") or "").strip()
    markets = prefs.get("marketplaces") or []
    if entity and category and markets:
        missions["profile"] = True
    return missions


async def _onboarding_state(request: Request, session: SellerSession) -> dict[str, Any] | None:
    svc = _onboarding(request)
    if svc is None:
        return None
    try:
        return await svc.get_state(session)
    except Exception as exc:
        log.info("onboarding state skip: %s", exc)
        return None


@router.get("/profile")
async def get_profile(
    request: Request,
    session: SellerSession = Depends(require_session),
):
    prefs = _prefs(request).get(session.seller_id)
    ob = await _onboarding_state(request, session)
    return {
        "seller_id": session.seller_id,
        "display_name": prefs.get("display_name") or session.display_name,
        "entity": prefs["entity"],
        "marketplaces": prefs["marketplaces"],
        "category": prefs["category"],
        "onboarding": ob,
        "wb_connected": bool(ob and ob.get("wb_connected")),
    }


@router.post("/profile")
async def update_profile(
    body: ProfileUpdate,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    store = _prefs(request)
    prefs = store.upsert(
        session.seller_id,
        entity=body.entity,
        marketplaces=body.marketplaces,
        category=body.category,
        display_name=body.display_name,
    )
    missions = _merge_missions(prefs, await _onboarding_state(request, session))
    if missions.get("profile"):
        store.complete_mission(session.seller_id, "profile")
        prefs = store.get(session.seller_id)
    return {
        "seller_id": session.seller_id,
        "entity": prefs["entity"],
        "marketplaces": prefs["marketplaces"],
        "category": prefs["category"],
        "display_name": prefs.get("display_name"),
        "missions": prefs["missions"],
    }


@router.get("/wb/status")
async def wb_status(
    request: Request,
    session: SellerSession = Depends(require_session),
):
    """Capability check via existing onboarding.check_wb. No fake success."""
    svc = _onboarding(request)
    if svc is None:
        raise HTTPException(status_code=503, detail="onboarding unavailable")
    data = await svc.check_wb(session)
    connected = bool(data.get("connected"))
    # Only ping is verified. Do not invent content/stats/advert rights.
    capabilities = {
        "ping": connected,
        "content": None,
        "analytics": None,
        "advert": None,
        "note": (
            "Проверен только доступ API (ping). Остальные права не подтверждаем."
            if connected
            else "Ключ не подтверждён — доступ не рисуем."
        ),
    }
    return {
        "connected": connected,
        "status": data.get("status"),
        "error": data.get("error"),
        "capabilities": capabilities,
    }


@router.get("/catalog")
async def list_catalog(
    request: Request,
    session: SellerSession = Depends(require_session),
):
    """Seller MemoryStore catalog only. Never returns DashboardService demo SKUs."""
    mem = await ready_memory(_memory(request))
    prefs = _prefs(request).get(session.seller_id)
    meta = prefs.get("catalog_meta") or {}
    items: list[dict[str, Any]] = []
    if mem is not None:
        rows = await mem.list_products(_seller_int(session))
        items = [serialize_memory_product(row, meta) for row in rows]
    scores = [p["argus_score"] for p in items if p.get("argus_score") is not None]
    return {
        "items": items,
        "count": len(items),
        "demo": False,
        "argus_index": round(sum(scores) / len(scores)) if scores else None,
        "updated_at": max((p.get("updated_at") or 0) for p in items) if items else None,
    }


@router.post("/catalog/add")
async def add_catalog_item(
    body: CatalogAddBody,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    article = _resolve_article(body)
    if not article:
        raise HTTPException(status_code=400, detail="Нужна ссылка WB или nmID")
    mem = await ready_memory(_memory(request))
    if mem is None:
        raise HTTPException(status_code=503, detail="Память магазина недоступна")
    ps = _product_service(request)
    if ps is None:
        raise HTTPException(status_code=503, detail="ProductService недоступен")
    product = await ps.get_product("wildberries", int(article))
    if product is None:
        raise HTTPException(status_code=404, detail=f"Товар {article} не найден")
    await upsert_seller_product(mem, _seller_int(session), product, int(article))
    remember_image(_prefs(request), session.seller_id, int(article), product_payload(product, article).get("image"))
    _prefs(request).upsert(session.seller_id, sticky_article=int(article))
    _prefs(request).complete_mission(session.seller_id, "first_product")
    payload = product_payload(product, article)
    payload["owned"] = True
    payload["already_existed"] = False
    return payload


@router.post("/catalog/refresh")
async def refresh_catalog(
    request: Request,
    session: SellerSession = Depends(require_session),
):
    mem = await ready_memory(_memory(request))
    if mem is None:
        raise HTTPException(status_code=503, detail="Память магазина недоступна")
    ps = _product_service(request)
    if ps is None:
        raise HTTPException(status_code=503, detail="ProductService недоступен")
    rows = await mem.list_products(_seller_int(session))
    updated: list[dict[str, Any]] = []
    now = time.time()
    for row in rows:
        article = int(row.article)
        product = None
        try:
            if hasattr(ps, "get_product_snapshot"):
                product = await ps.get_product_snapshot(
                    "wildberries", article, force_refresh=True
                )
            if product is None:
                product = await ps.get_product("wildberries", article)
        except Exception as exc:
            log.info("catalog refresh skip %s: %s", article, exc)
            continue
        if product is None:
            continue
        await upsert_seller_product(mem, _seller_int(session), product, article)
        payload = product_payload(product, article)
        remember_image(_prefs(request), session.seller_id, article, payload.get("image"))
        payload["owned"] = True
        updated.append(payload)
    return {
        "count": len(updated),
        "found": len(updated),
        "updated_at": now,
        "items": updated,
        "demo": False,
    }


@router.post("/analyze/public")
async def analyze_public(
    body: PublicAnalyzeBody,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    """Public card + Advisor. Does not add to seller catalog."""
    article = _resolve_article(body)
    if not article:
        raise HTTPException(status_code=400, detail="Нужна ссылка WB или nmID")
    _prefs(request).upsert(session.seller_id, sticky_article=int(article))
    ps = _product_service(request)
    product = None
    if ps is not None:
        try:
            product = await ps.get_product("wildberries", int(article))
        except Exception as exc:
            log.info("public analyze fetch skip: %s", exc)
    owned = False
    mem = await ready_memory(_memory(request))
    if mem is not None:
        existing = await mem.get_product(_seller_int(session), int(article))
        owned = existing is not None
    base = product_payload(product, article) if product is not None else {
        "article": int(article),
        "title": str(article),
        "image": None,
        "price": None,
        "rating": None,
        "feedback_count": None,
        "brand": "",
        "demo": False,
    }
    analyzer = _analyzer(request)
    plan = None
    score = None
    if analyzer is not None:
        try:
            analysis = await analyzer.analyze(_card_from_catalog(base, article), with_ai=False)
            plan = analysis.get("advisor_plan")
            if analysis.get("score") is not None:
                score = int(analysis["score"])
        except Exception as exc:
            log.info("public analyze advisor skip: %s", exc)
    cards = first_screen_cards(plan)
    return {
        **base,
        "argus_score": score,
        "argus_status": None if score is None else (
            "GREEN" if score >= 75 else "YELLOW" if score >= 50 else "RED"
        ),
        "first_screen": cards,
        "owned": owned,
        "can_add": not owned,
        "demo": False,
        "kind": "public_analyze",
    }


@router.get("/finance")
async def finance_snapshot(
    request: Request,
    session: SellerSession = Depends(require_session),
    scope: str = "shop",
    article: Optional[int] = None,
):
    """Honest shop/SKU finance from Formula Authority. No invented profit."""
    engine = FormulaEngine()
    if scope != "product":
        stack = engine.cost_stack()
        lines = []
        labels = {
            "revenue": "Выручка",
            "COGS": "Себестоимость",
            "commission": "Комиссия WB",
            "logistics": "Логистика",
            "storage": "Хранение",
            "advertising": "Реклама",
            "penalties": "Штрафы",
            "profit": "Прибыль",
        }
        for key, label in labels.items():
            res = stack.get(key)
            status = getattr(res, "status", None)
            status_v = status.value if hasattr(status, "value") else str(status or "MISSING")
            value = getattr(res, "value", None) if res is not None else None
            lines.append({
                "id": key,
                "label": label,
                "value": value,
                "status": status_v,
                "text": "Нет данных" if value is None else None,
            })
        lines.append({"id": "margin", "label": "Маржа", "value": None, "status": "MISSING", "text": "Нет данных"})
        return {
            "scope": "shop",
            "complete": False,
            "profit_defined": False,
            "lines": lines,
            "tariffs": {"confirmed": False, "note": "Тариф не подтверждён"},
            "note": "Полный финансовый расчёт магазина по API недоступен. Прибыль не считаем без выручки и расходов.",
        }

    art = int(article) if article else None
    unit = None
    rec = None
    mem = await ready_memory(_memory(request))
    if mem is not None and art:
        rec = await mem.get_product(_seller_int(session), art)
    if rec is not None:
        from backend.ai.advisor import compute_unit_economics

        unit = compute_unit_economics(rec, rec)
    stack = engine.cost_stack(
        revenue=None if rec is None or rec.price is None or rec.orders is None else float(rec.price) * float(rec.orders),
        cogs=getattr(rec, "cost", None) if rec else None,
        commission=getattr(rec, "commission", None) if rec else None,
        logistics=getattr(rec, "logistics", None) if rec else None,
        storage=getattr(rec, "storage", None) if rec else None,
        advertising=getattr(rec, "ad_spend", None) if rec else None,
    )
    profit = stack.get("profit")
    profit_val = getattr(profit, "value", None) if profit is not None else None
    profit_status = getattr(getattr(profit, "status", None), "value", None) or "MISSING"
    if rec is None or rec.price is None or rec.orders is None:
        profit_val = None
        profit_status = "MISSING"
    return {
        "scope": "product",
        "article": art,
        "complete": bool(unit and unit.get("complete")),
        "profit_defined": profit_val is not None and profit_status == "KNOWN",
        "unit_economics": unit,
        "tariffs": {
            "confirmed": bool(rec and getattr(rec, "commission", None) is not None),
            "note": None if rec and getattr(rec, "commission", None) is not None else "Тариф не подтверждён",
            "commission": getattr(rec, "commission", None) if rec else None,
            "logistics": getattr(rec, "logistics", None) if rec else None,
            "storage": getattr(rec, "storage", None) if rec else None,
        },
        "lines": [
            {"id": "price", "label": "Цена", "value": getattr(rec, "price", None) if rec else None, "status": "KNOWN" if rec and rec.price is not None else "MISSING", "text": None if rec and rec.price is not None else "Нет данных"},
            {"id": "cogs", "label": "Себестоимость", "value": getattr(rec, "cost", None) if rec else None, "status": "KNOWN" if rec and getattr(rec, "cost", None) is not None else "MISSING", "text": None if rec and getattr(rec, "cost", None) is not None else "Нет данных"},
            {"id": "commission", "label": "Комиссия WB", "value": getattr(rec, "commission", None) if rec else None, "status": "KNOWN" if rec and getattr(rec, "commission", None) is not None else "MISSING", "text": "Тариф не подтверждён" if not (rec and getattr(rec, "commission", None) is not None) else None},
            {"id": "logistics", "label": "Логистика", "value": getattr(rec, "logistics", None) if rec else None, "status": "KNOWN" if rec and getattr(rec, "logistics", None) is not None else "MISSING", "text": "Тариф не подтверждён" if not (rec and getattr(rec, "logistics", None) is not None) else None},
            {"id": "storage", "label": "Хранение", "value": getattr(rec, "storage", None) if rec else None, "status": "KNOWN" if rec and getattr(rec, "storage", None) is not None else "MISSING", "text": "Нет данных"},
            {"id": "ads", "label": "Реклама", "value": getattr(rec, "ad_spend", None) if rec else None, "status": "KNOWN" if rec and getattr(rec, "ad_spend", None) is not None else "NOT_INCLUDED", "text": "Нет данных"},
            {"id": "unit_profit", "label": "Прибыль на единицу", "value": (unit or {}).get("contribution"), "status": "KNOWN" if unit and unit.get("complete") else "MISSING", "text": None if unit and unit.get("complete") else "Нет данных"},
            {"id": "margin", "label": "Маржа", "value": (unit or {}).get("margin_pct"), "status": "KNOWN" if unit and unit.get("complete") else "MISSING", "text": None if unit and unit.get("complete") else "Нет данных"},
        ],
        "note": None if unit and unit.get("complete") else "Прибыль не определена: не хватает подтверждённых расходов. Средние по рынку не подставляем.",
    }


@router.get("/products/{article}")
async def product_detail(
    article: int,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    dash = _dashboard(request)
    demo = False
    owned = False
    base: dict[str, Any] = {
        "article": int(article),
        "title": str(article),
        "argus_score": None,
        "argus_status": None,
        "problems": [],
        "recommendations": [],
    }
    mem = await ready_memory(_memory(request))
    rec = None
    if mem is not None:
        rec = await mem.get_product(_seller_int(session), int(article))
        if rec is not None:
            owned = True
            meta = _prefs(request).get(session.seller_id).get("catalog_meta") or {}
            base = serialize_memory_product(rec, meta)
    if not owned and dash is not None and hasattr(dash, "get_catalog_item"):
        found, dash_demo = dash.get_catalog_item(int(article))
        if found and not dash_demo:
            base = found
        elif found and dash_demo:
            demo = True
    elif not owned and dash is not None:
        products, dash_demo = dash._catalog()
        for p in products:
            if int(p.get("article") or 0) == int(article):
                if dash_demo:
                    demo = True
                else:
                    base = dict(p)
                break

    if not owned and not demo:
        ps = _product_service(request)
        if ps is not None:
            try:
                live = await ps.get_product("wildberries", int(article))
                if live is not None:
                    base.update(product_payload(live, article))
                    demo = False
            except Exception as exc:
                log.info("product fetch skip: %s", exc)

    if dash is not None:
        try:
            base = await dash._try_product_context(int(article), base)
            if base.get("_product_context"):
                demo = False
        except Exception as exc:
            log.info("product context skip: %s", exc)

    analyzer = _analyzer(request)
    plan = None
    score = base.get("argus_score")
    if analyzer is not None:
        try:
            analysis = await analyzer.analyze(
                _card_from_catalog(base, article),
                with_ai=False,
                seller_data=rec,
            )
            plan = analysis.get("advisor_plan")
            if analysis.get("score") is not None:
                score = int(analysis["score"])
                base["argus_score"] = score
            if analysis.get("reasons"):
                base["problems"] = list(analysis["reasons"])[:6]
        except Exception as exc:
            log.info("advisor analyze skip: %s", exc)

    if score is None:
        status = base.get("argus_status")
    elif score >= 75:
        status = "GREEN"
    elif score >= 50:
        status = "YELLOW"
    else:
        status = "RED"

    cards = first_screen_cards(plan)
    return {
        "article": int(article),
        "title": base.get("title"),
        "image": base.get("image"),
        "price": base.get("price"),
        "rating": base.get("rating"),
        "feedback_count": base.get("reviews_count") or base.get("feedbacks") or base.get("feedback_count"),
        "position": base.get("position"),
        "brand": base.get("brand"),
        "argus_score": score,
        "argus_status": status,
        "problems": list(base.get("problems") or []),
        "recommendations": list(base.get("recommendations") or []),
        "first_screen": cards,
        "demo": bool(demo and not owned),
        "owned": owned,
        "can_add": not owned,
        "seller_id": session.seller_id,
        "seller_data": seller_fields(rec) if rec is not None else {},
        "source_label": "Источник: карточка WB",
    }


@router.post("/products/{article}/seller-data")
async def save_product_seller_data(
    article: int,
    body: SellerDataBody,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    """Persist seller metrics into existing MemoryStore. Does not create ACTION."""
    mem = await ready_memory(_memory(request))
    if mem is None:
        raise HTTPException(status_code=503, detail="Память магазина недоступна")
    uid = _seller_int(session)
    rec = await mem.get_product(uid, int(article))
    if rec is None:
        raise HTTPException(status_code=404, detail="Сначала добавьте товар в каталог")

    clicks = _skip_num(body.clicks, as_int=True)
    period_raw = (body.period or "").strip()
    period = None if period_raw in ("", "-", "—") else period_raw
    ok = await mem.save_seller_data(
        uid,
        int(article),
        "wildberries",
        sales=_skip_num(body.sales, as_int=True),
        orders=_skip_num(body.orders, as_int=True),
        period=period,
        ctr=_skip_num(body.ctr),
        cvr=_skip_num(body.cvr),
        returns=_skip_num(body.returns, as_int=True),
        ad_spend=_skip_num(body.ads),
        cost=_skip_num(body.cogs),
        commission=_skip_num(body.commission),
        logistics=_skip_num(body.logistics),
        storage=_skip_num(body.storage),
        impressions=_skip_num(body.impressions, as_int=True),
        views=_skip_num(body.views, as_int=True),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Сначала добавьте товар в каталог")
    if clicks is not None and hasattr(mem, "save_metric_snapshot"):
        try:
            await mem.save_metric_snapshot(
                uid,
                int(article),
                clicks=clicks,
                impressions=_skip_num(body.impressions, as_int=True),
                ctr=_skip_num(body.ctr),
                source="seller_input",
            )
        except Exception as exc:
            log.info("metric snapshot skip: %s", exc)

    rec = await mem.get_product(uid, int(article))
    overlay = _SellerOverlay(rec, {"clicks": clicks})
    analyzer = _analyzer(request)
    plan = None
    score = getattr(rec, "score", None)
    base = serialize_memory_product(rec, _prefs(request).get(session.seller_id).get("catalog_meta") or {})
    if analyzer is not None:
        try:
            analysis = await analyzer.analyze(
                _card_from_catalog(base, int(article)),
                with_ai=False,
                seller_data=overlay,
            )
            plan = analysis.get("advisor_plan")
            if analysis.get("score") is not None:
                score = int(analysis["score"])
        except Exception as exc:
            log.info("seller-data analyze skip: %s", exc)
    fields = seller_fields(rec)
    fields["clicks"] = clicks
    return {
        "ok": True,
        "article": int(article),
        "seller_data": fields,
        "first_screen": first_screen_cards(plan),
        "argus_score": score,
        "action_created": False,
    }


@router.get("/formula/lesson")
async def formula_lesson_get(session: SellerSession = Depends(require_session)):
    engine = FormulaEngine()
    ctr = engine.get_spec("F_CTR")
    cvr = engine.get_spec("F_CVR")
    return {
        "title": "Как считаются CTR и CVR",
        "source": "argus_formula_authority_v1",
        "formulas": [ctr.to_dict() if ctr else {}, cvr.to_dict() if cvr else {}],
        "notes": [
            "Формулы, которыми пользуется ARGUS. Новое выражение не выдумываем.",
            "Нет универсального «хороший CTR = 5%» без контекста.",
            "Если нет кликов, показов или заказов — числа не подставляем.",
            "Если показы, клики и заказы не сходятся — говорим, что данные противоречат друг другу, и не пересчитываем причину.",
        ],
        "example_blocked": True,
    }


@router.post("/formula/lesson")
async def formula_lesson_eval(
    body: FormulaLessonBody,
    session: SellerSession = Depends(require_session),
):
    from backend.ai.funnel_consistency import validate_funnel_fields

    engine = FormulaEngine()
    ctr_res = engine.ctr(clicks=body.clicks, impressions=body.impressions, ctr=body.ctr)
    cvr_res = engine.cvr(orders=body.orders, clicks=body.clicks, cvr=body.cvr)
    consistency = validate_funnel_fields(
        impressions=body.impressions,
        clicks=body.clicks,
        orders=body.orders,
        ctr=body.ctr,
        cvr=body.cvr,
        same_period=True,
    )
    return {
        "ctr": ctr_res.to_dict(),
        "cvr": cvr_res.to_dict(),
        "explain_ctr": engine.explain(ctr_res),
        "explain_cvr": engine.explain(cvr_res),
        "funnel_consistency": consistency.to_dict(),
    }


@router.post("/actions")
async def propose_action(
    body: ActionProposeBody,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    svc = _actions(request)
    try:
        at = ActionType(body.action_type)
    except ValueError:
        at = ActionType.OTHER
    action = await svc.propose(
        _seller_int(session),
        int(body.article),
        at,
        body.recommendation,
        expected_effect=body.expected_effect,
        diagnosis="miniapp_first_screen",
    )
    return _public_action(action)


@router.get("/actions")
async def list_actions(
    request: Request,
    session: SellerSession = Depends(require_session),
    article: Optional[int] = None,
):
    svc = _actions(request)
    seller = _seller_int(session)
    if article:
        items = await svc.list_for_product(seller, int(article))
    else:
        items = [a for a in list(getattr(svc, "_mem", {}).values()) if a.seller_id == seller]
        if not items and hasattr(svc, "list_due_checks"):
            items = []
    return {"items": [_public_action(a) for a in items], "kind": "ACTION"}


@router.get("/actions/due")
async def due_actions(
    request: Request,
    session: SellerSession = Depends(require_session),
):
    svc = _actions(request)
    due = await svc.list_due_checks(_seller_int(session))
    ts = svc._time
    return {
        "items": [_public_action(a) for a in due],
        "now": ts.timestamp(),
        "tz": ts.seller_timezone,
    }


@router.get("/actions/history")
async def action_history(
    request: Request,
    session: SellerSession = Depends(require_session),
    article: Optional[int] = None,
):
    """История ARGUS — confirmed actions only (not Advisor IDEA)."""
    svc = _actions(request)
    seller = _seller_int(session)
    items = []
    if article:
        items = await svc.list_for_product(seller, int(article), limit=80)
    else:
        items = [a for a in list(getattr(svc, "_mem", {}).values()) if a.seller_id == seller]
    return {
        "items": [_public_action(a) for a in items],
        "note": "IDEA/CHECK из Advisor не являются ACTION, пока продавец не нажал Принять.",
    }


@router.post("/actions/{action_id}/accept")
async def accept_action(
    action_id: str,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    svc = _actions(request)
    existing = await svc.get(action_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="action not found")
    if int(existing.seller_id) != _seller_int(session):
        raise HTTPException(status_code=403, detail="action does not belong to seller")
    try:
        action = await svc.accept(action_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="action not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _prefs(request).complete_mission(session.seller_id, "first_action")
    return _public_action(action)


@router.post("/actions/{action_id}/done")
async def done_action(
    action_id: str,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    svc = _actions(request)
    try:
        existing = await svc.get(action_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="action not found")
        if int(existing.seller_id) != _seller_int(session):
            raise HTTPException(status_code=403, detail="action does not belong to seller")
        action = await svc.mark_executed(action_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="action not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _public_action(action)


@router.post("/actions/{action_id}/defer")
async def defer_action(
    action_id: str,
    body: ActionDeferBody,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    svc = _actions(request)
    existing = await svc.get(action_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="action not found")
    if int(existing.seller_id) != _seller_int(session):
        raise HTTPException(status_code=403, detail="action does not belong to seller")
    action = await svc.defer(action_id, days=body.days)
    return _public_action(action)


@router.get("/time/settings")
async def get_time_settings(
    request: Request,
    session: SellerSession = Depends(require_session),
):
    from backend.foundation.time_service import TimeService

    prefs = _prefs(request).get(session.seller_id)
    clock = TimeService(seller_timezone=prefs["tz"])
    due = await _actions(request).list_due_checks(_seller_int(session))
    return {
        "tz": prefs["tz"],
        "push_enabled": prefs["push_enabled"],
        "reminder_hour": prefs["reminder_hour"],
        "schedule": prefs.get("schedule") or dict(DEFAULT_SCHEDULE),
        "zones": KNOWN_ZONES,
        "now_seller": clock.now_seller().isoformat(),
        "due_count": len(due),
    }


@router.get("/time/zones")
async def time_zones(session: SellerSession = Depends(require_session)):
    from backend.foundation.time_service import TimeService

    zones = []
    for z in KNOWN_ZONES:
        clock = TimeService(seller_timezone=z["id"])
        zones.append({**z, "resolved": clock.seller_timezone})
    return {"zones": zones, "default": "Europe/Moscow"}


@router.post("/time/settings")
async def update_time_settings(
    body: TimeSettingsUpdate,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    hour = body.reminder_hour
    push = body.push_enabled
    schedule = body.schedule
    if schedule:
        event_on = schedule.get("notify_event")
        if event_on is None:
            event_on = schedule.get("critical_enabled")
        action_on = schedule.get("notify_action_check")
        if action_on is None:
            action_on = schedule.get("action_check_enabled")
        reeng_on = schedule.get("notify_reengagement")
        flags = [event_on, action_on, reeng_on]
        if any(x is not None for x in flags):
            push = any(bool(x) for x in flags if x is not None)
        if action_on is not None and push is None:
            push = bool(action_on)
        action_time = str(schedule.get("action_check_time") or "")
        if action_time and ":" in action_time:
            try:
                hour = max(0, min(23, int(action_time.split(":")[0])))
            except ValueError:
                pass
    prefs = _prefs(request).upsert(
        session.seller_id,
        tz=body.tz,
        push_enabled=push,
        reminder_hour=hour,
        schedule=schedule,
    )
    return {
        "tz": prefs["tz"],
        "push_enabled": prefs["push_enabled"],
        "reminder_hour": prefs["reminder_hour"],
        "schedule": prefs.get("schedule") or dict(DEFAULT_SCHEDULE),
    }


@router.get("/missions")
async def get_missions(
    request: Request,
    session: SellerSession = Depends(require_session),
):
    store = _prefs(request)
    prefs = store.get(session.seller_id)
    ob = await _onboarding_state(request, session)
    missions = _merge_missions(prefs, ob)
    store.upsert(session.seller_id, missions=missions)
    items = []
    for meta in MISSION_META:
        items.append({**meta, "done": bool(missions.get(meta["id"])), "optional": meta["id"] == "wb_connect"})
    next_item = next((m for m in items if not m["done"] and m["id"] != "wb_connect"), None)
    if next_item is None:
        next_item = next((m for m in items if not m["done"]), None)
    return {
        "status": (ob or {}).get("status") or "NEW",
        "items": items,
        "next": next_item,
        "overlay_skipped": prefs["overlay_skipped"],
        "all_done": all(m["done"] for m in items),
    }


@router.post("/missions/{mission_id}/complete")
async def complete_mission(
    mission_id: str,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    if mission_id not in DEFAULT_MISSIONS:
        raise HTTPException(status_code=404, detail="unknown mission")
    store = _prefs(request)
    store.complete_mission(session.seller_id, mission_id)
    return await get_missions(request, session)


@router.post("/missions/skip")
async def skip_overlay(
    body: MissionBody,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    skipped = True if body.skipped is None else bool(body.skipped)
    _prefs(request).upsert(session.seller_id, overlay_skipped=skipped)
    return {"overlay_skipped": skipped}


FOLLOWUPS_DEFAULT = [
    {"id": "why", "label": "Почему?"},
    {"id": "market", "label": "Рынок"},
    {"id": "unit", "label": "Экономика"},
    {"id": "more", "label": "Подробнее"},
    {"id": "simpler", "label": "Объясни проще"},
]


def _followups() -> list[dict[str, str]]:
    return list(FOLLOWUPS_DEFAULT)


async def _persist_chat(request: Request, session: SellerSession, article, user_text: str, bot_text: str) -> None:
    mem = await ready_memory(_memory(request))
    if mem is None:
        return
    uid = _seller_int(session)
    try:
        await mem.touch_user(uid)
        if article:
            await mem.add_product_message(uid, int(article), "user", user_text)
            await mem.add_product_message(uid, int(article), "assistant", bot_text)
        else:
            await mem.add_message(uid, "user", user_text)
            await mem.add_message(uid, "assistant", bot_text)
    except Exception as exc:
        log.info("assistant memory persist skip: %s", exc)


async def _plan_for_article(request: Request, session: SellerSession, article: int):
    mem = await ready_memory(_memory(request))
    rec = None
    base = {"article": int(article), "title": str(article)}
    if mem is not None:
        rec = await mem.get_product(_seller_int(session), int(article))
        if rec is not None:
            meta = _prefs(request).get(session.seller_id).get("catalog_meta") or {}
            base = serialize_memory_product(rec, meta)
    analyzer = _analyzer(request)
    if analyzer is None:
        return None, rec
    try:
        analysis = await analyzer.analyze(
            _card_from_catalog(base, int(article)),
            with_ai=False,
            seller_data=rec,
        )
        return analysis.get("advisor_plan"), rec
    except Exception as exc:
        log.info("assistant plan skip: %s", exc)
        return None, rec


def _short_from_plan(plan, *, simpler: bool = False) -> str:
    if plan is None:
        return format_short_reply()
    verdict = (getattr(plan, "main_verdict", None) or getattr(plan, "diagnosis", None) or "").strip()
    why_pts = list(getattr(plan, "why_points", None) or [])
    why = why_pts[0] if why_pts else (getattr(plan, "confidence_why", None) or "")
    action = (getattr(plan, "do_first", None) or "").strip()
    if simpler:
        verdict = verdict.split(".")[0] if verdict else "Пока мало данных"
        why = "Смотрим только подтверждённые факты карточки, без догадок."
        action = action.split(".")[0] if action else "Сначала уточните цифры продавца"
    return format_short_reply(verdict=verdict, why=str(why), action=action)


@router.get("/assistant/context")
async def assistant_context(
    request: Request,
    session: SellerSession = Depends(require_session),
):
    prefs = _prefs(request).get(session.seller_id)
    article = prefs.get("sticky_article")
    mem = await ready_memory(_memory(request))
    if mem is not None:
        try:
            await mem.touch_user(_seller_int(session))
        except Exception as exc:
            log.debug("last_seen skip: %s", exc)
        if not article:
            try:
                rows = await mem.list_products(_seller_int(session))
                if rows:
                    article = int(rows[0].article)
                    _prefs(request).upsert(session.seller_id, sticky_article=article)
            except Exception as exc:
                log.debug("sticky fallback skip: %s", exc)
    return {
        "article": article,
        "chips": [
            {"id": "unit", "label": "Юнит-экономика"},
            {"id": "market", "label": "Рынок"},
            {"id": "dyn", "label": "Динамика"},
            {"id": "funnel", "label": "Воронка"},
        ],
        "followups": _followups(),
    }


@router.post("/assistant/context")
async def set_assistant_context(
    body: StickyBody,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    prefs = _prefs(request).upsert(session.seller_id, sticky_article=body.article)
    return {"article": prefs.get("sticky_article")}


@router.post("/assistant/chat")
async def assistant_chat(
    body: ChatBody,
    request: Request,
    session: SellerSession = Depends(require_session),
):
    from backend.knowledge.chat import KnowledgeChat, should_handle_knowledge

    text = (body.text or "").strip()
    chip = (body.chip or "").strip()
    article = body.article
    store = _prefs(request)
    linked = parse_article_input(text)
    if linked and not article:
        article = linked
    if article:
        store.upsert(session.seller_id, sticky_article=article)
    else:
        article = store.get(session.seller_id).get("sticky_article")

    if linked:
        public = await analyze_public(
            PublicAnalyzeBody(article=int(linked), text=text),
            request,
            session,
        )
        title = public.get("title") or f"nmID {linked}"
        public["text"] = format_short_reply(
            verdict=f"Быстрый разбор «{title}» по публичной карточке",
            why="В ваши товары не добавлял — это только разбор ссылки.",
            action="Если это ваш SKU — нажмите «Добавить в мои товары».",
        )
        public["kind"] = "public_analyze"
        public["followups"] = _followups()
        await _persist_chat(request, session, linked, text, public["text"])
        return public

    plan = None
    if article:
        plan, _rec = await _plan_for_article(request, session, int(article))

    if chip in ("why", "more") and plan is not None:
        why = " ".join(list(getattr(plan, "why_points", None) or [])[:3]) or getattr(plan, "confidence_why", "") or "Мало подтверждённых фактов."
        extra = getattr(plan, "format_first_screen", lambda: "")()
        reply_text = why if chip == "why" else (extra or why)
        if chip == "more" and extra:
            reply_text = extra
        payload = {
            "kind": "advisor",
            "text": reply_text,
            "article": article,
            "chip": chip,
            "followups": _followups(),
            "used_browser": False,
        }
        await _persist_chat(request, session, article, text or chip, reply_text)
        return payload

    if chip == "simpler":
        reply_text = _short_from_plan(plan, simpler=True)
        payload = {
            "kind": "advisor" if plan else "knowledge",
            "text": reply_text,
            "article": article,
            "chip": chip,
            "followups": _followups(),
            "used_browser": False,
        }
        await _persist_chat(request, session, article, text or chip, reply_text)
        return payload

    if article and chip in ("unit", "market", "dyn", "funnel") and plan is not None:
        meta = getattr(plan, "metadata", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        if chip == "unit":
            unit = meta.get("unit_economics") or {}
            reply_text = format_short_reply(
                verdict=unit.get("text") or "Экономика единицы неполная",
                why=unit.get("honesty") or "Не хватает подтверждённых расходов продавца.",
                action="Добавьте себестоимость, комиссию и логистику — прибыль не выдумываем.",
            )
        elif chip == "market":
            market = meta.get("market_compare") or {}
            reply_text = format_short_reply(
                verdict=market.get("text") or "Рынок не подтверждён",
                why="Медиану не показываем без подтверждённых цен похожих карточек.",
                action="Откройте раздел «Рынок» или пришлите ссылку на карточку.",
            )
        elif chip == "dyn":
            dyn = meta.get("dynamic_analytics") or {}
            reply_text = format_short_reply(
                verdict=dyn.get("summary") or dyn.get("text") or "Подтверждённой динамики пока нет",
                why="Сравниваем только факты по периодам, без догадок.",
                action="После нескольких обновлений карточки динамика появится сама.",
            )
        else:
            funnel = meta.get("funnel_consistency") if isinstance(meta.get("funnel_consistency"), dict) else {}
            reply_text = format_short_reply(
                verdict=human_funnel_status(funnel.get("status") if funnel else None),
                why=funnel.get("check_line") or "Нет показов, кликов и заказов продавца.",
                action="Добавьте данные продавца по воронке, если они есть.",
            )
        payload = {
            "kind": "advisor",
            "text": reply_text,
            "article": article,
            "chip": chip,
            "followups": _followups(),
            "used_browser": False,
        }
        await _persist_chat(request, session, article, text or chip, reply_text)
        return payload

    if article and plan is not None and not should_handle_knowledge(text):
        reply_text = _short_from_plan(plan)
        payload = {
            "kind": "advisor",
            "text": reply_text,
            "article": int(article),
            "followups": _followups(),
            "used_browser": False,
        }
        await _persist_chat(request, session, article, text, reply_text)
        return payload

    chat = getattr(request.app.state, "knowledge_chat", None) or KnowledgeChat()
    reply = chat.handle(text)
    raw = (reply.text or "").strip()
    # Keep knowledge answers conversational but not a wall of text.
    parts = [p.strip() for p in raw.replace("\n\n", "\n").split("\n") if p.strip()]
    short = "\n".join(parts[:6]) if parts else "Данных недостаточно"
    payload = {
        "kind": reply.kind,
        "text": short,
        "article": article,
        "followups": _followups(),
        "used_browser": False,
    }
    await _persist_chat(request, session, article, text, short)
    return payload
