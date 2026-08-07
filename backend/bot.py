import asyncio
import logging

from aiogram import Bot, Dispatcher

from backend.config import BOT_TOKEN, MEMORY_DB_PATH, WB_PROXY_URLS, WB_SELLER_API_KEY, describe

from backend.handlers import (
    start_router,
    menu_router,
    analyze_router,
    products_router,
    seller_ai_router,
    product_chat_router,
    product_analysis_router,
)

from backend.ai.analyzer import AIAnalyzer
from backend.ai.brain import SellerBrain
from backend.ai.context import (
    AnalysisHistorySource,
    ContextBuilder,
    ProductContextSource,
    SellerStatsContextSource,
)
from backend.memory import MemoryStore
from backend.providers import WBBrowserProvider
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
    proxy_pool = ProxyPool.from_env_value(WB_PROXY_URLS)
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

    print("6. Собираем ProductService...")

    # Главное место получения товаров. WBBrowserProvider теперь —
    # тонкий переходник к WBEngine (имя историческое, не переименовывал).
    product_service = ProductService()
    product_service.register(WBBrowserProvider(wb_engine), priority=10)

    print("7. Подключаем сервисы к диспетчеру...")

    dp["ai_service"] = ai_service
    dp["memory"] = memory_store
    dp["wb_service"] = wb_service
    dp["product_service"] = product_service
    dp["analyzer"] = AIAnalyzer(ai_service=ai_service)
    history = HistoryService(memory_store)
    session = SessionService(memory_store)

    print("8. Собираем интеллект Seller AI...")

    # Источники знаний. Добавить новый = одна строка register().
    context_builder = ContextBuilder()
    context_builder.register(ProductContextSource(session))
    context_builder.register(AnalysisHistorySource(history))
    # Заготовка: заработает, когда подключим Seller API.
    context_builder.register(SellerStatsContextSource(None))

    dp["history"] = history
    dp["session"] = session
    dp["daily"] = DailyPlanner()
    dp["context_builder"] = context_builder
    dp["brain"] = SellerBrain(
        ai_service=ai_service,
        session=session,
        context_builder=context_builder,
        memory_store=memory_store,
    )

    print("9. Подключаем роутеры...")

    dp.include_router(start_router)
    dp.include_router(menu_router)
    dp.include_router(analyze_router)
    dp.include_router(products_router)
    dp.include_router(product_chat_router)
    dp.include_router(product_analysis_router)
    dp.include_router(seller_ai_router)

    print("10. Удаляем webhook...")

    await bot.delete_webhook(drop_pending_updates=True)

    print("11. Запускаем polling...")

    try:
        await dp.start_polling(bot)
    finally:
        print("Останавливаем WB Engine...")
        await wb_service.stop()
        print("Закрываем долговременную память...")
        await memory_store.close()


if __name__ == "__main__":
    asyncio.run(main())
