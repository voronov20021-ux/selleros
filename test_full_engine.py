"""
Тест полной цепочки WB Engine.
Проверяет работу всей системы получения данных о товарах.

ЗАПУСКАТЬ ПОСЛЕ СНЯТИЯ БЛОКИРОВКИ IP.
"""

import asyncio
import sys
import logging
from pathlib import Path

# Настраиваем логирование для видимости работы движка
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)

# Добавляем корень проекта
sys.path.insert(0, str(Path(__file__).parent))

from backend.wb_engine import WBEngine, ProxyPool
from backend.wb_engine.sources import (
    SellerAPISource,
    CDNSource,
    SearchFallbackSource,
    HistoryFallbackSource,
)
from backend.memory import MemoryStore
from backend.config import WB_PROXY_URLS, WB_SELLER_API_KEY, MEMORY_DB_PATH


async def test_full_engine(article: int):
    """Полный тест WB Engine как в production"""
    
    print("\n" + "="*80)
    print("ТЕСТ ПОЛНОЙ ЦЕПОЧКИ WB ENGINE")
    print("="*80)
    print(f"Артикул: {article}")
    
    # 1. Инициализация как в bot.py
    print("\n1. Инициализация компонентов...")
    
    # Memory store
    memory_store = await MemoryStore.create(MEMORY_DB_PATH)
    print(f"   ✓ Memory store: {MEMORY_DB_PATH}")
    
    # Proxy pool
    proxy_pool = ProxyPool.from_env_value(WB_PROXY_URLS)
    if proxy_pool:
        print(f"   ✓ Proxy pool: {len(proxy_pool.proxies)} прокси")
    else:
        print("   ⚠ Proxy pool: не настроены (идем напрямую)")
    
    # WB Engine
    wb_engine = WBEngine()
    print("   ✓ WB Engine создан")
    
    # 2. Регистрация источников (приоритет как в production)
    print("\n2. Регистрация источников данных...")
    
    # Seller API (priority=0)
    wb_engine.register(
        SellerAPISource(api_key=WB_SELLER_API_KEY), 
        priority=0
    )
    print("   ✓ SellerAPISource (priority=0)")
    
    # CDN (priority=10)
    wb_engine.register(
        CDNSource(proxy_pool=proxy_pool), 
        priority=10
    )
    print("   ✓ CDNSource (priority=10)")
    
    # Search Fallback (priority=20) - ГЛАВНЫЙ ДЛЯ ЦЕН СЕЙЧАС
    wb_engine.register(
        SearchFallbackSource(proxy_pool=proxy_pool), 
        priority=20
    )
    print("   ✓ SearchFallbackSource (priority=20) <- ГЛАВНЫЙ")
    
    # History Fallback (priority=100)
    wb_engine.register(
        HistoryFallbackSource(memory_store), 
        priority=100
    )
    print("   ✓ HistoryFallbackSource (priority=100)")
    
    # 3. Получение товара
    print("\n3. Запрос товара через WB Engine...")
    print("   (следите за логами - они покажут какой источник сработал)\n")
    
    try:
        product = await wb_engine.get_product(article)
        
        if product is None:
            print("\n" + "="*80)
            print("❌ ТОВАР НЕ НАЙДЕН")
            print("="*80)
            print("\nВозможные причины:")
            print("  1. IP все еще заблокирован")
            print("  2. Все источники недоступны")
            print("  3. Товар не существует")
            print("  4. Прокси не работает")
            return False
        
        # 4. Анализ результата
        print("\n" + "="*80)
        print("✅ ТОВАР ПОЛУЧЕН!")
        print("="*80)
        
        print(f"\n📦 ОСНОВНАЯ ИНФОРМАЦИЯ:")
        print(f"   Артикул:     {product.article}")
        print(f"   Название:    {product.title or '—'}")
        print(f"   Бренд:       {product.brand or '—'}")
        print(f"   Источник:    {product.source}")
        print(f"   Время:       {product.scanned_at}")
        
        # КРИТИЧЕСКАЯ ПРОВЕРКА: ЦЕНЫ
        print(f"\n💰 ЦЕНЫ (КРИТИЧЕСКАЯ ПРОВЕРКА):")
        has_price = product.price is not None
        if has_price:
            print(f"   ✅ Цена:          {product.price} руб")
            if product.old_price:
                print(f"   ✅ Старая цена:   {product.old_price} руб")
            if product.discount:
                print(f"   ✅ Скидка:        {product.discount}%")
            if product.wallet_price:
                print(f"   💳 С кошельком:   ~{product.wallet_price} руб")
        else:
            print(f"   ❌ ЦЕНА НЕ ПОЛУЧЕНА!")
            print(f"   ⚠️  Это означает, что:")
            print(f"      - Источник вернул данные без цен")
            print(f"      - Нужна дополнительная отладка")
        
        # РЕПУТАЦИЯ
        print(f"\n⭐ РЕПУТАЦИЯ:")
        has_rating = product.rating is not None
        has_feedbacks = product.feedbacks is not None
        
        if has_rating:
            print(f"   ✅ Рейтинг:       {product.rating}")
        else:
            print(f"   ❌ Рейтинг:       НЕ ПОЛУЧЕН")
            
        if has_feedbacks:
            print(f"   ✅ Отзывы:        {product.feedbacks} шт")
        else:
            print(f"   ❌ Отзывы:        НЕ ПОЛУЧЕНЫ")
        
        # КОНТЕНТ
        print(f"\n📝 КОНТЕНТ:")
        print(f"   Описание:    {'✅' if product.description else '—'}")
        print(f"   Фото:        {product.photo_count} шт")
        print(f"   Видео:       {'✅' if product.video else '—'}")
        print(f"   Характеристик: {len(product.characteristics)}")
        
        # ЛОГИСТИКА
        print(f"\n📦 ЛОГИСТИКА:")
        print(f"   Размеров:    {len(product.sizes)}")
        print(f"   В наличии:   {product.total_qty} шт")
        print(f"   Складов:     {len(product.warehouses)}")
        
        # ИТОГОВАЯ ОЦЕНКА
        print("\n" + "="*80)
        print("ИТОГОВАЯ ОЦЕНКА:")
        print("="*80)
        
        critical_ok = has_price and has_rating and has_feedbacks
        content_ok = product.description and product.photo_count > 0
        
        print(f"\nКритичные данные (цена, рейтинг, отзывы):")
        if critical_ok:
            print("   ✅ ВСЕ ПОЛУЧЕНЫ!")
        else:
            print(f"   ⚠️  ЧАСТИЧНО:")
            print(f"      Цена:   {'✅' if has_price else '❌'}")
            print(f"      Рейтинг: {'✅' if has_rating else '❌'}")
            print(f"      Отзывы:  {'✅' if has_feedbacks else '❌'}")
        
        print(f"\nКонтент (описание, фото):")
        if content_ok:
            print("   ✅ Получен")
        else:
            print("   ⚠️  Частично")
        
        print(f"\n" + "="*80)
        if critical_ok and content_ok:
            print("🎉 ПОЛНЫЙ УСПЕХ! WB ENGINE РАБОТАЕТ ИДЕАЛЬНО!")
        elif critical_ok:
            print("✅ УСПЕХ! Критичные данные получены.")
        elif has_price:
            print("⚠️  ЧАСТИЧНЫЙ УСПЕХ. Цены есть, но нет всех данных.")
        else:
            print("❌ ПРОВАЛ. Цены не получены.")
        print("="*80)
        
        # 5. Сохранение в память (как в production)
        if product.source == "live":
            print("\n5. Сохранение в память ARGUS...")
            await memory_store.save_product_snapshot(
                marketplace="wildberries",
                article=product.article,
                title=product.title,
                price=product.price,
                rating=product.rating,
                photos=product.photo_count,
            )
            print("   ✓ Сохранено")
        
        await memory_store.close()
        
        return critical_ok
        
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        await memory_store.close()
        return False


async def main():
    print("\n" + "="*80)
    print("ПРОВЕРКА РАБОТЫ WB ENGINE")
    print("ПОЛНАЯ ЦЕПОЧКА: Engine -> Sources -> Prices")
    print("="*80)
    
    # Тестовые артикулы
    test_articles = [
        211246754,  # Основной
    ]
    
    if len(sys.argv) > 1:
        try:
            test_articles = [int(sys.argv[1])]
        except ValueError:
            print("Ошибка: укажите числовой артикул")
            return
    
    results = []
    for article in test_articles:
        success = await test_full_engine(article)
        results.append((article, success))
        
        if len(test_articles) > 1 and article != test_articles[-1]:
            print("\nПауза 5 сек перед следующим товаром...")
            await asyncio.sleep(5)
    
    # Итоги
    if len(results) > 1:
        print("\n" + "="*80)
        print("ОБЩИЕ ИТОГИ:")
        print("="*80)
        for article, success in results:
            status = "✅" if success else "❌"
            print(f"  {article}: {status}")
        
        success_count = sum(1 for _, s in results if s)
        print(f"\nУспешно: {success_count}/{len(results)}")


if __name__ == "__main__":
    asyncio.run(main())
