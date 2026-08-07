"""
Простой тест нового прокси Proxy6.
Один запрос к товару WB через SearchFallbackSource.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.wb_engine.sources.search_fallback import SearchFallbackSource
from backend.wb_engine.proxy_pool import ProxyPool
from backend.config import WB_PROXY_URLS


async def test_one_product():
    """Один запрос к товару"""
    
    print("\n" + "="*80)
    print("ТЕСТ НОВОГО ПРОКСИ PROXY6")
    print("="*80)
    
    print(f"\nПрокси из .env:")
    print(f"  {WB_PROXY_URLS}")
    
    # Создаем proxy pool
    proxy_pool = ProxyPool.from_env_value(WB_PROXY_URLS)
    
    if not proxy_pool:
        print("\n[ERROR] Прокси не настроены")
        return
    
    print(f"\n[OK] ProxyPool создан")
    
    # Статус до запроса
    status = proxy_pool.get_status()
    print(f"\nСтатус прокси:")
    print(f"  Доступно: {status['available']}/{status['total']}")
    
    # Создаем источник
    source = SearchFallbackSource(proxy_pool=proxy_pool, timeout=15.0)
    
    # Тестовый товар
    article = 211246754
    
    print(f"\n" + "-"*80)
    print(f"Запрос товара {article} через SearchFallbackSource...")
    print(f"URL: https://www.wildberries.ru/catalog/{article}/detail.aspx")
    print("-"*80)
    
    try:
        product = await source.fetch(article)
        
        if product is None:
            print("\n[FAIL] Товар не найден")
            print("Возможно артикул неверный или прокси заблокирован")
            
            # Статус после
            status = proxy_pool.get_status()
            print(f"\nСтатус прокси после запроса:")
            print(f"  Доступно: {status['available']}/{status['total']}")
            print(f"  Заблокировано: {status['blocked']}/{status['total']}")
            
            return False
        
        # Успех!
        print("\n" + "="*80)
        print("[SUCCESS] ТОВАР ПОЛУЧЕН!")
        print("="*80)
        
        print(f"\nОсновная информация:")
        print(f"  Артикул:   {product.article}")
        print(f"  Название:  {product.title or '—'}")
        print(f"  Бренд:     {product.brand or '—'}")
        print(f"  Источник:  {product.source}")
        
        # ГЛАВНАЯ ПРОВЕРКА: ЦЕНЫ
        print(f"\n💰 ЦЕНЫ (ГЛАВНАЯ ЦЕЛЬ):")
        
        has_price = product.price is not None
        has_rating = product.rating is not None
        has_feedbacks = product.feedbacks is not None
        
        if has_price:
            print(f"  ✅ Цена:         {product.price} руб")
        else:
            print(f"  ❌ Цена:         НЕТ")
        
        if product.old_price:
            print(f"  ✅ Старая цена:  {product.old_price} руб")
        
        if product.discount:
            print(f"  ✅ Скидка:       {product.discount}%")
        
        if has_rating:
            print(f"  ✅ Рейтинг:      {product.rating}")
        else:
            print(f"  ❌ Рейтинг:      НЕТ")
        
        if has_feedbacks:
            print(f"  ✅ Отзывы:       {product.feedbacks} шт")
        else:
            print(f"  ❌ Отзывы:       НЕТ")
        
        # Статус прокси после успеха
        status = proxy_pool.get_status()
        print(f"\nСтатус прокси после успешного запроса:")
        print(f"  Доступно: {status['available']}/{status['total']}")
        print(f"  Запросов: {status['proxies'][0]['total_requests']}")
        
        # Итог
        print("\n" + "="*80)
        if has_price and has_rating and has_feedbacks:
            print("🎉 ПОЛНЫЙ УСПЕХ!")
            print("✅ Прокси работает")
            print("✅ Цены получены")
            print("✅ Рейтинг получен")
            print("✅ Отзывы получены")
            print("\n➡️  Можно запускать бота!")
        elif has_price:
            print("✅ ЧАСТИЧНЫЙ УСПЕХ")
            print("✅ Прокси работает")
            print("✅ Цены получены")
            print("⚠️  Не все данные получены")
        else:
            print("⚠️  ПРОКСИ РАБОТАЕТ, НО ЦЕН НЕТ")
            print("Нужна дополнительная отладка")
        print("="*80)
        
        return has_price
        
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}")
        print(f"Сообщение: {e}")
        
        # Статус после ошибки
        status = proxy_pool.get_status()
        print(f"\nСтатус прокси после ошибки:")
        print(f"  Доступно: {status['available']}/{status['total']}")
        print(f"  Заблокировано: {status['blocked']}/{status['total']}")
        
        if status['blocked'] > 0:
            print(f"\n⚠️  Прокси заблокирован (получен 403 или 429)")
            print(f"  Заблокирован на 30 минут")
        
        return False


if __name__ == "__main__":
    asyncio.run(test_one_product())
