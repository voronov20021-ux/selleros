"""
Тест SearchFallbackSource - проверка получения цен из search API.

ЗАПУСКАТЬ ПОСЛЕ СНЯТИЯ БЛОКИРОВКИ IP (~1 час).

Этот тест проверяет, что:
1. SearchFallbackSource подключается к search.wb.ru
2. Получает данные товара по артикулу
3. apply_detail() извлекает цены из ответа
4. Возвращает полный WBProduct с ценами
"""

import asyncio
import json
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent))

from backend.wb_engine.sources.search_fallback import SearchFallbackSource
from backend.wb_engine.proxy_pool import ProxyPool
from backend.config import WB_PROXY_URLS


async def test_search_fallback(article: int):
    print("\n" + "="*80)
    print("ТЕСТ SearchFallbackSource")
    print("="*80)
    print(f"Артикул: {article}")
    
    # Создаем proxy pool из .env
    proxy_pool = ProxyPool.from_env_value(WB_PROXY_URLS)
    if proxy_pool:
        print(f"Прокси: {len(proxy_pool.proxies)} шт")
    else:
        print("Прокси: не настроены (идем напрямую)")
    
    # Создаем источник
    source = SearchFallbackSource(proxy_pool=proxy_pool, timeout=15.0)
    
    print(f"\nИсточник: {source.name}")
    print("Выполняем запрос...")
    
    try:
        # Пытаемся получить товар
        product = await source.fetch(article)
        
        if product is None:
            print("\n[FAIL] Товар не найден")
            print("Возможные причины:")
            print("  1. IP все еще заблокирован (подождите еще)")
            print("  2. Артикул не существует")
            print("  3. Товар не найден в результатах поиска")
            return False
        
        # Успех!
        print("\n" + "="*80)
        print("[SUCCESS] ТОВАР ПОЛУЧЕН ЧЕРЕЗ SEARCH API!")
        print("="*80)
        
        # Проверяем основные поля
        print(f"\nОСНОВНЫЕ ДАННЫЕ:")
        print(f"  Артикул:    {product.article}")
        print(f"  Название:   {product.title or '—'}")
        print(f"  Бренд:      {product.brand or '—'}")
        print(f"  Источник:   {product.source}")
        
        # ПРОВЕРЯЕМ ЦЕНЫ - ГЛАВНАЯ ЦЕЛЬ
        print(f"\n💰 ЦЕНЫ (ГЛАВНАЯ ПРОВЕРКА):")
        if product.price is not None:
            print(f"  ✅ Цена:         {product.price} руб")
        else:
            print(f"  ❌ Цена:         НЕ ПОЛУЧЕНА")
            
        if product.old_price is not None:
            print(f"  ✅ Старая цена:  {product.old_price} руб")
        else:
            print(f"  ⚠️  Старая цена:  нет")
            
        if product.discount is not None:
            print(f"  ✅ Скидка:       {product.discount}%")
        else:
            print(f"  ⚠️  Скидка:       нет")
        
        # ПРОВЕРЯЕМ РЕЙТИНГ И ОТЗЫВЫ
        print(f"\n⭐ РЕПУТАЦИЯ:")
        if product.rating is not None:
            print(f"  ✅ Рейтинг:      {product.rating}")
        else:
            print(f"  ❌ Рейтинг:      НЕ ПОЛУЧЕН")
            
        if product.feedbacks is not None:
            print(f"  ✅ Отзывы:       {product.feedbacks} шт")
        else:
            print(f"  ❌ Отзывы:       НЕ ПОЛУЧЕНЫ")
        
        # Проверяем размеры
        if product.sizes:
            print(f"\n📦 РАЗМЕРЫ И ОСТАТКИ:")
            print(f"  Всего размеров: {len(product.sizes)}")
            for i, size in enumerate(product.sizes[:3], 1):
                print(f"\n  Размер {i}:")
                print(f"    Название:     {size.name or '—'}")
                print(f"    Цена:         {size.price} руб")
                print(f"    Старая цена:  {size.old_price} руб")
                print(f"    Остаток:      {size.qty} шт")
                print(f"    Складов:      {len(size.stocks)} шт")
            if len(product.sizes) > 3:
                print(f"  ... и еще {len(product.sizes) - 3} размеров")
        
        # Сохраняем результат
        result_dict = product.to_dict()
        filename = f"search_success_{article}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, ensure_ascii=False, indent=2)
        print(f"\n[FILE] Полные данные сохранены: {filename}")
        
        # ИТОГОВАЯ ПРОВЕРКА
        print("\n" + "="*80)
        print("ИТОГОВАЯ ПРОВЕРКА:")
        print("="*80)
        
        has_price = product.price is not None
        has_rating = product.rating is not None
        has_feedbacks = product.feedbacks is not None
        
        if has_price and has_rating and has_feedbacks:
            print("✅ ВСЕ КРИТИЧНЫЕ ДАННЫЕ ПОЛУЧЕНЫ!")
            print("✅ SearchFallbackSource РАБОТАЕТ КОРРЕКТНО!")
            print("✅ ПРОБЛЕМА РЕШЕНА!")
            return True
        else:
            print("⚠️  ЧАСТИЧНЫЙ УСПЕХ:")
            print(f"   Цены:   {'✅' if has_price else '❌'}")
            print(f"   Рейтинг: {'✅' if has_rating else '❌'}")
            print(f"   Отзывы:  {'✅' if has_feedbacks else '❌'}")
            print("\nНужна дополнительная отладка apply_detail()")
            return False
            
    except Exception as e:
        print(f"\n[EXCEPTION] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        
        print("\nВозможные причины:")
        print("  1. IP все еще заблокирован (429 или 403)")
        print("  2. Прокси не работает")
        print("  3. Сетевая ошибка")
        return False


async def test_multiple_articles():
    """Тест на нескольких артикулах"""
    print("\n" + "="*80)
    print("МАССОВЫЙ ТЕСТ")
    print("="*80)
    
    # Популярные товары WB для теста
    test_articles = [
        211246754,  # Основной тестовый
        234916063,  # Альтернативный 1
        207652892,  # Альтернативный 2
    ]
    
    results = []
    for article in test_articles:
        print(f"\n>>> Тестируем артикул {article}...")
        success = await test_search_fallback(article)
        results.append((article, success))
        
        # Пауза между запросами
        if article != test_articles[-1]:
            print("\nПауза 3 сек перед следующим...")
            await asyncio.sleep(3)
    
    # Итоги
    print("\n" + "="*80)
    print("ИТОГИ МАССОВОГО ТЕСТА")
    print("="*80)
    
    for article, success in results:
        status = "✅ OK" if success else "❌ FAIL"
        print(f"  {article}: {status}")
    
    success_count = sum(1 for _, s in results if s)
    print(f"\nУспешно: {success_count}/{len(results)}")
    
    if success_count == len(results):
        print("\n✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        print("✅ SearchFallbackSource ПОЛНОСТЬЮ РАБОТАЕТ!")
    elif success_count > 0:
        print("\n⚠️  ЧАСТИЧНЫЙ УСПЕХ")
        print("Некоторые товары не найдены")
    else:
        print("\n❌ ВСЕ ТЕСТЫ ПРОВАЛЕНЫ")
        print("IP все еще заблокирован или прокси не работает")


async def main():
    print("\n" + "="*80)
    print("ПРОВЕРКА SearchFallbackSource")
    print("ТЕСТ ПОЛУЧЕНИЯ ЦЕН ИЗ SEARCH API")
    print("="*80)
    
    import sys
    
    if len(sys.argv) > 1:
        # Тест конкретного артикула
        try:
            article = int(sys.argv[1])
            await test_search_fallback(article)
        except ValueError:
            print("Ошибка: укажите числовой артикул")
            print("Пример: python test_search_source.py 211246754")
    else:
        # Массовый тест
        await test_multiple_articles()


if __name__ == "__main__":
    asyncio.run(main())
