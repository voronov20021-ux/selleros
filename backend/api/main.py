"""
SellerOS Dashboard FastAPI app.

    uvicorn backend.api.main:app --reload --port 8000

Auth: POST /api/auth/telegram with Telegram WebApp initData (HMAC required).
Optional local-only: POST /api/auth/dev when MINIAPP_DEV_AUTH=1 AND loopback.
Dashboard endpoints require Authorization: Bearer <session> (or X-Session-Token).
Onboarding: /api/onboarding/* (session seller only).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import config
from backend.auth.router import router as auth_router
from backend.auth.session import SessionStore
from backend.dashboard.router import router as dashboard_router
from backend.dashboard.service import DashboardService

log = logging.getLogger("selleros.api")


def _build_service() -> DashboardService:
    memory_store = None
    product_builder = None
    competitor_intelligence = None
    analyzer = None

    try:
        from backend.memory import MemoryStore

        memory_store = MemoryStore(db_path=config.MEMORY_DB_PATH)
    except Exception as exc:
        log.info("MemoryStore skip: %s", exc)

    try:
        from backend.product_context import ProductContextBuilder

        product_builder = ProductContextBuilder()
    except Exception as exc:
        log.info("ProductContextBuilder skip: %s", exc)

    try:
        from backend.competitor_intelligence import CompetitorIntelligence

        if product_builder is not None:
            competitor_intelligence = CompetitorIntelligence(product_builder=product_builder)
    except Exception as exc:
        log.info("CompetitorIntelligence skip: %s", exc)

    try:
        from backend.ai.analyzer import AIAnalyzer

        analyzer = AIAnalyzer(ai_service=None, category_intelligence=None)
    except Exception as exc:
        log.info("AIAnalyzer skip: %s", exc)

    return DashboardService(
        memory_store=memory_store,
        product_builder=product_builder,
        competitor_intelligence=competitor_intelligence,
        analyzer=analyzer,
        force_demo=False,
    )


def _build_product_service():
    """ProductService with BrowserProvider priority 1 when available."""
    from backend.services.product_service import ProductService

    product_service = ProductService()

    try:
        from backend.providers.browser_provider import BrowserProvider
        from backend.browser.cache import PublicProductCache
        from backend.browser.fetcher import PlaywrightBrowserFetcher
        from backend.browser.proxy_pool import BrowserProxyPool
        from backend.browser.reviews_cache import PublicReviewsCache

        cache = PublicProductCache(
            config.BROWSER_CACHE_PATH,
            ttl_product=config.BROWSER_CACHE_TTL_PRODUCT,
        )
        reviews_cache = PublicReviewsCache(
            config.BROWSER_CACHE_PATH,
            ttl=config.BROWSER_CACHE_TTL_REVIEWS,
        )
        proxy_urls = config.effective_browser_proxy_urls()
        proxy_pool = BrowserProxyPool.from_urls(proxy_urls)
        fetcher = PlaywrightBrowserFetcher(
            proxy_pool=proxy_pool if proxy_pool else None,
            proxy_url=(
                (proxy_urls[0] if proxy_urls else None) if not proxy_pool else None
            ),
            headless=config.BROWSER_HEADLESS,
            timeout_ms=config.BROWSER_TIMEOUT_MS,
            proxy_mode=config.BROWSER_PROXY_MODE,
            disable_http2=config.BROWSER_DISABLE_HTTP2,
            session_id=config.BROWSER_PROXY_SESSION_ID,
            session_time=config.BROWSER_PROXY_SESSION_TIME,
            use_system_chrome=config.BROWSER_USE_SYSTEM_CHROME,
            chrome_path=config.BROWSER_CHROME_PATH,
        )
        browser = BrowserProvider(
            cache=cache,
            fetcher=fetcher,
            reviews_cache=reviews_cache,
            retries=config.BROWSER_RETRIES,
            enabled=config.BROWSER_ENABLED,
        )
        product_service.set_public_cache(cache)
        product_service.register(browser, priority=1)
        log.info("Onboarding ProductService: BrowserProvider priority=1")
        # print(): uvicorn often hides selleros.* INFO; Amvera logs show stdout.
        print(
            "   BrowserProvider: ON "
            f"(playwright={fetcher.is_available()}, "
            f"enabled={config.BROWSER_ENABLED}, "
            f"system_chrome={config.BROWSER_USE_SYSTEM_CHROME})"
        )
    except Exception as exc:
        log.info("BrowserProvider skip for API: %s", exc)
        print(f"   BrowserProvider: SKIP for API ({type(exc).__name__})")

    try:
        from backend.providers.wb_browser_provider import WBBrowserProvider
        from backend.wb_engine import WBEngine
        from backend.wb_engine.sources import (
            CDNSource,
            SearchFallbackSource,
            SellerAPISource,
        )
        from backend.wb_engine.proxy_pool import ProxyPool

        proxy_pool = ProxyPool.from_env_value(
            config.effective_wb_proxy_urls(),
            scheme=config.WB_PROXY_SCHEME,
        )
        engine = WBEngine()
        engine.register(SellerAPISource(api_key=config.WB_SELLER_API_KEY), priority=0)
        engine.register(CDNSource(proxy_pool=proxy_pool), priority=10)
        engine.register(SearchFallbackSource(proxy_pool=proxy_pool), priority=20)
        product_service.register(WBBrowserProvider(engine), priority=10)
        log.info("Onboarding ProductService: WBEngine/HTTP fallback priority=10")
    except Exception as exc:
        log.info("WBEngine fallback skip for API: %s", exc)

    return product_service


def _build_onboarding_service(*, memory_store=None, analyzer=None):
    from backend.onboarding.service import OnboardingService
    from backend.onboarding.store import OnboardingStore

    if analyzer is None:
        try:
            from backend.ai.analyzer import AIAnalyzer

            analyzer = AIAnalyzer(ai_service=None, category_intelligence=None)
        except Exception:
            analyzer = None

    return OnboardingService(
        store=OnboardingStore(db_path=config.MEMORY_DB_PATH),
        memory_store=memory_store,
        product_service=_build_product_service(),
        analyzer=analyzer,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    dashboard = _build_service()
    memory_store = getattr(dashboard, "_memory", None)
    analyzer = getattr(dashboard, "_analyzer", None)

    app.state.dashboard_service = dashboard
    app.state.session_store = SessionStore(
        db_path=config.MEMORY_DB_PATH,
        ttl_seconds=config.AUTH_SESSION_TTL_SECONDS,
    )
    try:
        app.state.session_store.cleanup_expired_sessions()
    except Exception as exc:
        log.info("auth session cleanup skip: %s", exc)

    if memory_store is not None and getattr(memory_store, "_db", None) is None:
        try:
            await memory_store.connect()
        except Exception as exc:
            log.info("MemoryStore connect skip: %s", exc)

    try:
        app.state.onboarding_service = _build_onboarding_service(
            memory_store=memory_store,
            analyzer=analyzer,
        )
        app.state.product_service = getattr(
            app.state.onboarding_service, "product_service", None
        )
    except Exception as exc:
        log.info("OnboardingService skip: %s", exc)
        app.state.onboarding_service = None
        app.state.product_service = None
    app.state.memory_store = memory_store

    try:
        from backend.api.miniapp_store import MiniAppStore
        from backend.foundation.action_service import ActionService
        from backend.foundation.time_service import TimeService
        from backend.knowledge.chat import KnowledgeChat

        app.state.miniapp_store = MiniAppStore(db_path=config.MEMORY_DB_PATH)
        app.state.time_service = TimeService()
        app.state.action_service = ActionService(
            memory_store=memory_store,
            time_service=app.state.time_service,
        )
        app.state.knowledge_chat = KnowledgeChat()
    except Exception as exc:
        log.info("Mini App adapters skip: %s", exc)
        app.state.miniapp_store = None
        app.state.action_service = None
        app.state.knowledge_chat = None

    yield

    if memory_store is not None:
        try:
            await memory_store.close()
        except Exception:
            pass


app = FastAPI(title="SellerOS Dashboard API", version="0.1.0", lifespan=lifespan)

_DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5175",
    "http://localhost:5175",
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "https://voronov20021-ux.github.io",
)


def _cors_origins() -> list[str]:
    """Explicit origins only. Wildcard origin is rejected when credentials are on."""
    origins: list[str] = []
    seen: set[str] = set()
    extra = config.get("CORS_ORIGINS")
    for raw in list(_DEFAULT_CORS_ORIGINS) + [p.strip() for p in extra.split(",") if p.strip()]:
        host = raw.strip().rstrip("/")
        if not host or host == "*" or host in seen:
            continue
        seen.add(host)
        origins.append(host)
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(dashboard_router)

try:
    from backend.onboarding.router import router as onboarding_router

    app.include_router(onboarding_router)
except Exception as exc:
    log.info("onboarding router skip: %s", exc)

try:
    from backend.api.miniapp_router import router as miniapp_router

    app.include_router(miniapp_router)
except Exception as exc:
    log.info("miniapp router skip: %s", exc)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "selleros-dashboard"}
