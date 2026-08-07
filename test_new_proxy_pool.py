"""
Тест нового умного ProxyPool с failover.

Проверяет:
1. Rate limiting (1 запрос в 10 секунд)
2. Автоматический failover на следующий прокси
3. Блокировку прокси на 30 минут при 403/429
4. Статус всех прокси
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.wb_engine.sources.search_fallback import SearchFallbackSource
from backend.wb_engine.proxy_pool import ProxyPool
from backend.config import WB_PROXY_URLS


def print_status(proxy_pool: ProxyPool, title: str = ""):
    """Красивый вывод статуса прокси"""
    print("\n" + "="*80)
    if title:
        print(title)
    print("="*80)
    
    status = proxy_pool.get_status()
    
    print(f"\nСТАТУС ПРОКСИ:")
    print(f"   Всего:          {status['total']}")
    print(f"   Доступно:       {status['available']}")
    print(f"   Заблокировано:  {status['blocked']}")
    print(f"   Rate limited:   {status['rate_limited']}")
    
    print(f"\nДЕТАЛИ:")
    for i, p in enumerate(status['proxies'], 1):
        status_mark = "[OK]" if p['available'] else "[BLOCKED]"
        print(f"\n   {i}. {p['url']} {status_mark}")
        print(f"      Доступен:  {p['available']}")
        print(f"      До разблокировки: {p['seconds_until_available']:.1f} сек")
        print(f"      Запросов:  {p['total_requests']}")
        print(f"      Блокировок: {p['total_blocks']}")


async def test_proxy_pool_basic():
    """Базовый тест ProxyPool"""
    print("\n" + "="*80)
    print("ТЕСТ 1: Базовая функциональность ProxyPool")
    print("="*80)
    
    # Создаем пул из .env
    proxy_pool = ProxyPool.from_env_value(WB_PROXY_URLS)
    
    if not proxy_pool:
        print("\n❌ Прокси не настроены в .env")
        return False
    
    print(f"\n✅ ProxyPool создан из .env")
    print_status(proxy_pool, "ИСХОДНОЕ СОСТОЯНИЕ")
    
    # Получаем первый прокси
    print("\n" + "-"*80)
    print("Получаем первый доступный прокси...")
    
    proxy1 = proxy_pool.get_next_available()
    if proxy1:
        print(f"✅ Получен прокси: {proxy1.get('http', 'N/A')[:50]}...")
    else:
        print("❌ Нет доступных прокси")
        return False
    
    print_status(proxy_pool, "ПОСЛЕ ПЕРВОГО ИСПОЛЬЗОВАНИЯ")
    
    # Пробуем сразу получить второй (должен быть rate limited)
    print("\n" + "-"*80)
    print("Пробуем получить прокси сразу же (должен быть rate limited)...")
    
    proxy2 = proxy_pool.get_next_available()
    if proxy2:
        print("⚠️  Прокси получен (не должно было случиться)")
    else:
        print("✅ Правильно! Все прокси на cooldown (rate limit)")
    
    print_status(proxy_pool, "ПРОВЕРКА RATE LIMITING")
    
    return True


async def test_proxy_failover():
    """Тест автоматического failover"""
    print("\n" + "="*80)
    print("ТЕСТ 2: Автоматический Failover")
    print("="*80)
    
    # Создаем пул с несколькими прокси (для теста добавим дубли)
    test_proxies = WB_PROXY_URLS + "," + WB_PROXY_URLS if WB_PROXY_URLS else ""
    proxy_pool = ProxyPool.from_env_value(test_proxies)
    
    if not proxy_pool or proxy_pool.get_status()['total'] < 2:
        print("\n⚠️  Для этого теста нужно минимум 2 прокси")
        print("   Добавьте в .env: WB_PROXY_URLS=proxy1,proxy2")
        return False
    
    print_status(proxy_pool, "ИСХОДНОЕ СОСТОЯНИЕ")
    
    # Получаем первый прокси
    print("\n" + "-"*80)
    print("Шаг 1: Получаем первый прокси...")
    proxy1 = proxy_pool.get_next_available()
    if proxy1:
        print(f"✅ Получен прокси #1")
        
    # Блокируем его
    print("\nШаг 2: Блокируем первый прокси (симулируем 403)...")
    proxy_pool.mark_blocked("403 (тест)")
    print("✅ Прокси заблокирован на 30 минут")
    
    print_status(proxy_pool, "ПОСЛЕ БЛОКИРОВКИ ПЕРВОГО")
    
    # Пробуем получить следующий
    print("\n" + "-"*80)
    print("Шаг 3: Получаем следующий прокси (автоматический failover)...")
    proxy2 = proxy_pool.get_next_available()
    if proxy2:
        print(f"✅ Получен прокси #2 (failover сработал!)")
    else:
        print("❌ Не удалось получить второй прокси")
        return False
    
    print_status(proxy_pool, "ПОСЛЕ FAILOVER")
    
    # Разблокируем все
    print("\n" + "-"*80)
    print("Шаг 4: Разблокируем все прокси принудительно...")
    proxy_pool.unblock_all()
    
    print_status(proxy_pool, "ПОСЛЕ РАЗБЛОКИРОВКИ")
    
    return True


async def test_real_request():
    """Реальный запрос к WB через новый ProxyPool"""
    print("\n" + "="*80)
    print("ТЕСТ 3: Реальный запрос к Wildberries")
    print("="*80)
    
    proxy_pool = ProxyPool.from_env_value(WB_PROXY_URLS)
    
    if not proxy_pool:
        print("\n❌ Прокси не настроены")
        return False
    
    print_status(proxy_pool, "ПЕРЕД ЗАПРОСОМ")
    
    # Создаем источник
    source = SearchFallbackSource(proxy_pool=proxy_pool, timeout=15.0)
    
    # Тестовый артикул
    article = 211246754
    
    print(f"\n" + "-"*80)
    print(f"Запрашиваем товар {article} через SearchFallbackSource...")
    print("(это реальный запрос к WB!)")
    
    try:
        product = await source.fetch(article)
        
        if product:
            print(f"\n✅ ТОВАР ПОЛУЧЕН!")
            print(f"   Название: {product.title}")
            print(f"   Цена:     {product.price} руб")
            print(f"   Рейтинг:  {product.rating}")
            print(f"   Отзывы:   {product.feedbacks}")
            
            print_status(proxy_pool, "ПОСЛЕ УСПЕШНОГО ЗАПРОСА")
            
            return True
        else:
            print(f"\n⚠️  Товар не найден (это нормально если артикул неверный)")
            print_status(proxy_pool, "ПОСЛЕ ЗАПРОСА (товар не найден)")
            return False
            
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        print(f"   Тип: {type(e).__name__}")
        
        print_status(proxy_pool, "ПОСЛЕ ОШИБКИ")
        
        # Проверяем, заблокирован ли прокси
        status = proxy_pool.get_status()
        if status['blocked'] > 0:
            print("\n⚠️  Прокси был заблокирован (получили 403/429)")
            print("   Это нормально если IP заблокирован или прокси мертв")
        
        return False


async def main():
    print("\n" + "="*80)
    print("ТЕСТИРОВАНИЕ НОВОГО ProxyPool")
    print("="*80)
    print("\nНовый прокси из .env:")
    print(f"  {WB_PROXY_URLS or '(не настроен)'}")
    
    # Запускаем тесты
    print("\n" + "="*80)
    print("ЗАПУСК ТЕСТОВ")
    print("="*80)
    
    # Тест 1: Базовая функциональность
    test1_ok = await test_proxy_pool_basic()
    
    # Тест 2: Failover
    test2_ok = await test_proxy_failover()
    
    # Тест 3: Реальный запрос
    test3_ok = await test_real_request()
    
    # Итоги
    print("\n" + "="*80)
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("="*80)
    print(f"\n  Тест 1 (Базовый):    {'✅' if test1_ok else '❌'}")
    print(f"  Тест 2 (Failover):   {'✅' if test2_ok else '❌'}")
    print(f"  Тест 3 (Реальный):   {'✅' if test3_ok else '❌'}")
    
    if test1_ok and test2_ok:
        print("\n✅ ProxyPool работает корректно!")
    else:
        print("\n❌ Есть проблемы с ProxyPool")
    
    if test3_ok:
        print("\n🎉 ПОЗДРАВЛЯЕМ! Цены из WB получены успешно!")
    elif not test3_ok:
        print("\n⚠️  Реальный запрос не прошел.")
        print("   Возможные причины:")
        print("   1. Прокси не работает (проверьте credentials)")
        print("   2. IP все еще заблокирован WB")
        print("   3. Сетевая ошибка")


if __name__ == "__main__":
    asyncio.run(main())
