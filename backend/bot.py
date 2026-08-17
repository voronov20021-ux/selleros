import asyncio
import logging

from aiogram import Bot, Dispatcher

from backend.config import (
    BOT_TOKEN, INTELLIGENCE_DB_PATH, MEMORY_DB_PATH,
    BROWSER_CACHE_PATH, BROWSER_CACHE_TTL_PRODUCT, BROWSER_CACHE_TTL_REVIEWS,
    BROWSER_DISABLE_HTTP2, BROWSER_ENABLED, BROWSER_HEADLESS,
    BROWSER_CHROME_PATH, BROWSER_PROXY_MODE, BROWSER_PROXY_SESSION_ID,
    BROWSER_PROXY_SESSION_TIME, BROWSER_RETRIES, BROWSER_TIMEOUT_MS,
    BROWSER_USE_SYSTEM_CHROME, SOCKS_BRIDGE_ENABLED,
    SOCKS_UPSTREAM_HOST, SOCKS_UPSTREAM_PORT, SOCKS_UPSTREAM_USER,
    WB_PROXY_SCHEME, WB_SELLER_API_KEY,
    describe, effective_browser_proxy_urls, effective_wb_proxy_urls,
    socks_bridge_http_url,
)

from backend.handlers import (
    start_router,
    menu_router,
    analyze_router,
    products_router,
    seller_ai_router,
    product_chat_router,
    product_analysis_router,
    action_verify_router,
    argus_inbox_router,
)

from backend.ai.analyzer import AIAnalyzer
from backend.ai.brain import SellerBrain
from backend.ai.context import (
    AdvisorContextSource,
    AnalysisHistorySource,
    CategoryIntelligenceSource,
    ContextBuilder,
    LearningContextSource,
    ProductContextSource,
    ReasonerContextSource,
    ReviewContextSource,
    SellerStatsContextSource,
)
from backend.memory import MemoryStore
from backend.providers import BrowserProvider, WBBrowserProvider
from backend.services.ai_service import AIService
from backend.services.daily import DailyPlanner
from backend.services.history import HistoryService
from backend.services.session import SessionService
from backend.services.product_service import ProductService
from backend.services.wb_service import WBService
from backend.wb_engine import ProxyPool, WBEngine
from backend.wb_engine.sources import (
    CDNSource,
    HistoryFallbackSource,
    SearchFallbackSource,
    SellerAPISource,
)


async def main():

    logging.basicConfig(level=logging.INFO)

    print("1. Создаем бота...")
    bot = Bot(token=BOT_TOKEN)

    print("2. Создаем Dispatcher...")
    dp = Dispatcher()

    print("3. Запускаем Seller AI...")
    print(describe())

    # ОДИН AIService на весь проект. AI_PROVIDER=off — выключен.
    ai_service = AIService()

    print("4. Подключаем долговременную память (SQLite)...")

    # Один файл на диске, переживает перезапуск бота.
    # Когда дорастём до PostgreSQL — поменяется только этот класс,
    # см. backend/memory/store.py.
    memory_store = MemoryStore(MEMORY_DB_PATH)
    await memory_store.connect()

    print("5. Собираем WB Engine...")

    # Сердце получения данных с Wildberries. Источники пробуются по
    # порядку priority; если один заблокирован или упал — движок сам
    # переходит к следующему, пользователь 429 никогда не видит.
    # Подключить новый источник (BrightData, официальный API) —
    # один класс + одна строка register() ниже, больше нигде
    # ничего менять не нужно.
    proxy_pool = ProxyPool.from_env_value(
        effective_wb_proxy_urls(),
        scheme=WB_PROXY_SCHEME,
    )
    if proxy_pool:
        print(f"   прокси: {len(proxy_pool.proxies)} шт.")
    else:
        print("   прокси не настроены — источники ходят напрямую")

    wb_engine = WBEngine()
    wb_engine.register(SellerAPISource(api_key=WB_SELLER_API_KEY), priority=0)
    wb_engine.register(CDNSource(proxy_pool=proxy_pool), priority=10)
    wb_engine.register(SearchFallbackSource(proxy_pool=proxy_pool), priority=20)
    wb_engine.register(HistoryFallbackSource(memory_store), priority=100)

    wb_service = WBService(wb_engine)
    await wb_service.start()

    from backend.browser.socks_bridge import start_bridge_from_config, stop_bridge

    _socks_bridge = None
    if SOCKS_BRIDGE_ENABLED:
        try:
            _socks_bridge = await start_bridge_from_config()
            _up_user = SOCKS_UPSTREAM_USER or ""
            _up_auth = f"{_up_user}:***@" if _up_user else ""
            print(
                f"   SOCKS bridge: {socks_bridge_http_url()} → "
                f"socks5://{_up_auth}{SOCKS_UPSTREAM_HOST}:{SOCKS_UPSTREAM_PORT}"
            )
        except Exception as _bridge_err:
            print(f"   SOCKS bridge: FAILED to start ({type(_bridge_err).__name__})")
            _socks_bridge = None

    print("6. Собираем ProductService...")

    # PRIORITY: BrowserProvider (cache + Chromium) → WBEngine/HTTP fallback.
    # WBBrowserProvider — историческое имя тонкого адаптера к WBEngine.
    from backend.browser.cache import PublicProductCache
    from backend.browser.fetcher import PlaywrightBrowserFetcher
    from backend.browser.proxy_pool import BrowserProxyPool
    from backend.browser.reviews_cache import PublicReviewsCache

    _browser_product_cache = PublicProductCache(
        BROWSER_CACHE_PATH,
        ttl_product=BROWSER_CACHE_TTL_PRODUCT,
    )
    _browser_reviews_cache = PublicReviewsCache(
        BROWSER_CACHE_PATH,
        ttl=BROWSER_CACHE_TTL_REVIEWS,
    )
    _browser_proxy_urls = effective_browser_proxy_urls()
    _browser_proxy_pool = BrowserProxyPool.from_urls(_browser_proxy_urls)
    _browser_fetcher = PlaywrightBrowserFetcher(
        proxy_pool=_browser_proxy_pool if _browser_proxy_pool else None,
        proxy_url=(
            (_browser_proxy_urls[0] if _browser_proxy_urls else None)
            if not _browser_proxy_pool
            else None
        ),
        headless=BROWSER_HEADLESS,
        timeout_ms=BROWSER_TIMEOUT_MS,
        proxy_mode=BROWSER_PROXY_MODE,
        disable_http2=BROWSER_DISABLE_HTTP2,
        session_id=BROWSER_PROXY_SESSION_ID,
        session_time=BROWSER_PROXY_SESSION_TIME,
        use_system_chrome=BROWSER_USE_SYSTEM_CHROME,
        chrome_path=BROWSER_CHROME_PATH,
    )
    _browser_provider = BrowserProvider(
        cache=_browser_product_cache,
        fetcher=_browser_fetcher,
        reviews_cache=_browser_reviews_cache,
        retries=BROWSER_RETRIES,
        enabled=BROWSER_ENABLED,
    )

    product_service = ProductService()
    product_service.set_public_cache(_browser_product_cache)
    product_service.register(_browser_provider, priority=1)
    product_service.register(WBBrowserProvider(wb_engine), priority=10)
    if BROWSER_ENABLED:
        print(
            "   BrowserProvider: ON "
            f"(headless={BROWSER_HEADLESS}, "
            f"proxies={len(_browser_proxy_pool)}, "
            f"scheme=http, "
            f"mode={BROWSER_PROXY_MODE}, "
            f"bridge={'on' if _socks_bridge else 'off'}, "
            f"http2_off={BROWSER_DISABLE_HTTP2}, "
            f"system_chrome={BROWSER_USE_SYSTEM_CHROME}, "
            f"timeout={BROWSER_TIMEOUT_MS}ms, "
            f"ttl={BROWSER_CACHE_TTL_PRODUCT}s)"
        )
    else:
        print("   BrowserProvider: OFF → сразу HTTP/WBEngine")

    print("7. Подключаем сервисы к диспетчеру...")

    dp["ai_service"] = ai_service
    dp["memory"] = memory_store
    dp["wb_service"] = wb_service
    dp["product_service"] = product_service
    from backend.handlers.last_seen import LastSeenMiddleware
    dp.message.middleware(LastSeenMiddleware(memory_store))
    dp.callback_query.middleware(LastSeenMiddleware(memory_store))
    history = HistoryService(memory_store)
    session = SessionService(memory_store)

    # Action Verification Automation (check_after → observe → verify → outcome_after)
    from backend.foundation.action_observation import ActionObservationProvider
    from backend.foundation.action_scheduler import ActionVerificationScheduler
    from backend.foundation.action_service import ActionService
    from backend.foundation.seller_api_observation import SellerAPIObservationProvider
    from backend.handlers.action_verify import set_shared_action_service
    from backend.keyboards.inline import action_verification_kb as _action_verification_kb

    _action_service = ActionService(memory_store=memory_store)
    set_shared_action_service(_action_service)

    async def _resolve_seller_api_key(seller_id: str):
        # Prefer per-seller onboarding credentials; fall back to process env key.
        try:
            from backend.onboarding.store import OnboardingStore
            store = OnboardingStore()
            key = store.get_wb_api_key(str(seller_id))
            if key:
                return key
        except Exception:
            pass
        return WB_SELLER_API_KEY or None

    _seller_api_obs = SellerAPIObservationProvider(
        credential_resolver=_resolve_seller_api_key,
        global_api_key=WB_SELLER_API_KEY,
        # api_adapter=None until official content/analytics endpoints are wired
    )
    _action_obs = ActionObservationProvider(
        memory_store=memory_store,
        product_service=product_service,
        seller_api_provider=_seller_api_obs,
    )

    async def _action_notify(action, text: str, extras=None) -> None:
        kb = None
        if extras and extras.get("keyboard") == "verification":
            kb = _action_verification_kb(action.action_id)
        try:
            await bot.send_message(
                int(action.seller_id),
                text,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logging.getLogger("selleros.bot").debug("action notify skip: %s", exc)

    _action_scheduler = ActionVerificationScheduler(
        _action_service,
        observation=_action_obs,
        memory_store=memory_store,
        notify=_action_notify,
        interval_sec=300.0,
    )
    dp["action_service"] = _action_service
    dp["action_scheduler"] = _action_scheduler
    _action_scheduler.start_background()
    print("   ActionVerificationScheduler: ON (interval=300s)")

    print("8. Собираем интеллект Seller AI...")

    # Intelligence Layer — рыночный контекст для Argus.
    # При недоступном WORDSTAT_TOKEN источник молча возвращает None.
    _cat_intelligence = None
    _intel_catalog = None
    _intel_reasoner = None
    _learning_brain = None
    _outcome_tracker = None
    _outcome_learning = None
    _review_intel = None
    _intel_store = None
    try:
        from backend.intelligence import (
            CategoryIntelligence,
            EvidenceEngine,
            IntelligenceStore,
            MarketEventEngine,
            SeasonalityEngine,
            TrendEngine,
        )
        from backend.intelligence.catalog import IntelligenceCatalog
        from backend.intelligence.cost_guard import YandexCostGuard
        from backend.intelligence.learning_brain import LearningBrain
        from backend.intelligence.learning_integration import OutcomeLearningIntegrator
        from backend.intelligence.market_events import MarketEventIngestor
        from backend.intelligence.outcome_tracker import OutcomeTracker
        from backend.intelligence.reasoner import IntelligenceReasoner
        from backend.intelligence.reviews import ReviewIntelligence
        from backend.intelligence.search_service import SearchService
        from backend.intelligence.sources.yandex_search import YandexSearchAdapter

        _intel_store = IntelligenceStore(INTELLIGENCE_DB_PATH)
        await _intel_store.connect()
        _intel_ev_engine = EvidenceEngine(store=_intel_store)
        _intel_search_adapter = YandexSearchAdapter()
        _intel_guard = YandexCostGuard(store=_intel_store)
        await _intel_guard.setup()
        # CostGuard обязателен: SearchService → CostGuard → Yandex HTTP
        _intel_search_svc = SearchService(
            store=_intel_store,
            engine=_intel_ev_engine,
            adapter=_intel_search_adapter,
            cost_guard=_intel_guard,
        )

        _trend_engine = TrendEngine(store=_intel_store)
        _seas_engine = SeasonalityEngine(store=_intel_store)
        _market_ingestor = MarketEventIngestor(
            store=_intel_store,
            ev_engine=_intel_ev_engine,
        )

        _cat_intelligence = CategoryIntelligence(
            store=_intel_store,
            ev_engine=_intel_ev_engine,
            search_svc=_intel_search_svc,
            trend_engine=_trend_engine,
            seasonality_engine=_seas_engine,
            market_event_engine=MarketEventEngine(
                store=_intel_store,
                ev_engine=_intel_ev_engine,
            ),
            cost_guard=_intel_guard,
        )
        _intel_catalog = IntelligenceCatalog(
            store=_intel_store,
            ev_engine=_intel_ev_engine,
            trend_engine=_trend_engine,
            seasonality_engine=_seas_engine,
            market_event_ingestor=_market_ingestor,
        )
        _intel_reasoner = IntelligenceReasoner()
        _learning_brain = LearningBrain(store=_intel_store)
        _review_intel = ReviewIntelligence(store=_intel_store)
        # OutcomeTracker / Integrator: фундамент. Автообучение пока НЕ включено.
        _outcome_tracker = OutcomeTracker(
            store=_intel_store,
            learning_brain=_learning_brain,
        )
        _outcome_learning = OutcomeLearningIntegrator(learning_brain=_learning_brain)
        print("   Intelligence Layer подключён.")
    except Exception as _intel_err:
        print(f"   Intelligence Layer недоступен: {_intel_err}")
        _cat_intelligence = None
        _intel_catalog = None
        _intel_reasoner = None
        _learning_brain = None
        _outcome_tracker = None
        _outcome_learning = None
        _review_intel = None
        _intel_store = None
        _intel_search_svc = None

    from backend.api.miniapp_store import MiniAppStore
    from backend.foundation.notification_worker import NotificationWorker
    from backend.foundation.time_service import get_time_service

    async def _seller_notify(seller_id: int, text: str, extras=None) -> None:
        try:
            await bot.send_message(
                int(seller_id),
                text,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logging.getLogger("selleros.bot").debug("seller notify skip: %s", exc)

    _notify_worker = NotificationWorker(
        time_service=get_time_service(),
        prefs=MiniAppStore(db_path=MEMORY_DB_PATH),
        memory_store=memory_store,
        action_service=_action_service,
        intelligence_store=_intel_store,
        notify=_seller_notify,
        interval_sec=300.0,
    )
    dp["notification_worker"] = _notify_worker
    _notify_worker.start_background()
    print("   NotificationWorker: ON (interval=300s)")

    # Источники знаний. Добавить новый = одна строка register().
    context_builder = ContextBuilder()
    context_builder.register(ProductContextSource(session))
    context_builder.register(AnalysisHistorySource(history))
    # Заготовка: заработает, когда подключим Seller API.
    context_builder.register(SellerStatsContextSource(None))
    # Market Intelligence: тренды, сезонность, события для категории товара.
    context_builder.register(CategoryIntelligenceSource(_cat_intelligence, session))
    # Intelligence Reasoner: выводы (risks/opportunities) поверх каталога.
    context_builder.register(
        ReasonerContextSource(_intel_catalog, _intel_reasoner, session)
    )
    # Controlled WB review texts (cache TTL 7d, ≤1 HTTP / miss).
    # Public browser reviews cache проверяется до HTTP.
    from backend.services.wb_reviews import WBReviewsService
    _wb_reviews = WBReviewsService(
        proxy_pool=proxy_pool,
        public_reviews_cache=_browser_reviews_cache,
    )
    dp["wb_reviews"] = _wb_reviews
    dp["browser_provider"] = _browser_provider
    dp["browser_product_cache"] = _browser_product_cache
    dp["browser_reviews_cache"] = _browser_reviews_cache

    # Review Intelligence: recurring issues из реальных отзывов session/WB.
    context_builder.register(
        ReviewContextSource(
            _review_intel, session, store=_intel_store, reviews_service=_wb_reviews,
        )
    )
    # Actionable Advisor: FACT → BAD → FIX → ADD → GROW → PRIORITY (deterministic).
    context_builder.register(
        AdvisorContextSource(
            session=session,
            review_intel=_review_intel,
            category_intelligence=_cat_intelligence,
            reviews_service=_wb_reviews,
        )
    )
    # SOLUTION_RESEARCH: Yandex search via CostGuard (no invented shops/prices).
    from backend.ai.context.solution_research import SolutionResearchContextSource
    context_builder.register(
        SolutionResearchContextSource(_intel_search_svc, session)
    )
    # Learning Loop: исторический опыт успешных/неуспешных действий.
    context_builder.register(LearningContextSource(_learning_brain, session))

    dp["history"] = history
    dp["session"] = session
    dp["category_intelligence"] = _cat_intelligence
    dp["intelligence_catalog"] = _intel_catalog
    dp["intelligence_reasoner"] = _intel_reasoner
    dp["learning_brain"] = _learning_brain
    dp["outcome_tracker"] = _outcome_tracker
    dp["outcome_learning"] = _outcome_learning
    dp["review_intel"] = _review_intel
    dp["search_service"] = _intel_search_svc
    dp["analyzer"] = AIAnalyzer(
        ai_service=ai_service,
        category_intelligence=_cat_intelligence,
        outcome_tracker=_outcome_tracker,
        review_intel=_review_intel,
    )
    dp["daily"] = DailyPlanner()
    dp["context_builder"] = context_builder
    dp["brain"] = SellerBrain(
        ai_service=ai_service,
        session=session,
        context_builder=context_builder,
        memory_store=memory_store,
        learning_brain=_learning_brain,
        search_service=_intel_search_svc,
        outcome_tracker=_outcome_tracker,
        public_cache=_browser_product_cache,
    )

    print("9. Подключаем роутеры...")

    dp.include_router(start_router)
    dp.include_router(menu_router)
    dp.include_router(analyze_router)
    dp.include_router(products_router)
    dp.include_router(product_chat_router)
    dp.include_router(product_analysis_router)
    dp.include_router(action_verify_router)
    dp.include_router(seller_ai_router)
    dp.include_router(argus_inbox_router)

    print("10. Удаляем webhook...")

    await bot.delete_webhook(drop_pending_updates=True)

    print("11. Запускаем polling...")

    try:
        await dp.start_polling(bot)
    finally:
        print("Останавливаем ActionVerificationScheduler...")
        try:
            await _action_scheduler.stop()
        except Exception:
            pass
        print("Останавливаем NotificationWorker...")
        try:
            await _notify_worker.stop()
        except Exception:
            pass
        print("Останавливаем SOCKS bridge...")
        await stop_bridge()
        print("Останавливаем WB Engine...")
        await wb_service.stop()
        print("Закрываем долговременную память...")
        await memory_store.close()


if __name__ == "__main__":
    asyncio.run(main())
