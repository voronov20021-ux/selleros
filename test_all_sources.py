"""
Диагностический скрипт для проверки всех источников данных WB.
Проверяет:
1. Search API - возвращает ли цены
2. Detail API - работает ли вообще
3. Card.json CDN - что возвращает
"""

import asyncio
import json
from curl_cffi import requests


async def test_search_api(article: int):
    """Тест Search API - должен возвращать цены"""
    print("\n" + "="*80)
    print("1. ТЕСТ SEARCH API")
    print("="*80)
    
    url = "https://search.wb.ru/exactmatch/ru/common/v18/search"
    params = {
        "appType": "1",
        "curr": "rub",
        "dest": "-1257786",
        "lang": "ru",
        "page": "1",
        "query": str(article),
        "resultset": "catalog",
        "sort": "popular",
        "spp": "30",
    }
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    }
    
    try:
        async with requests.AsyncSession(
            headers=headers,
            impersonate="chrome124",
        ) as session:
            response = await session.get(url, params=params)
            
        print(f"Статус: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            products = data.get("products", [])
            
            if products:
                product = next((p for p in products if int(p.get("id", -1)) == article), None)
                if product:
                    print(f"\n[OK] Товар найден через Search API!")
                    print(f"Название: {product.get('name')}")
                    print(f"Бренд: {product.get('brand')}")
                    print(f"ID: {product.get('id')}")
                    print(f"Рейтинг: {product.get('reviewRating') or product.get('rating')}")
                    print(f"Отзывы: {product.get('feedbacks')}")
                    
                    # Проверяем цены
                    sizes = product.get("sizes", [])
                    if sizes:
                        print(f"\nРазмеров: {len(sizes)}")
                        for i, size in enumerate(sizes[:3], 1):
                            price_block = size.get("price", {})
                            print(f"\nРазмер {i}:")
                            print(f"  - name: {size.get('name')}")
                            print(f"  - price.basic: {price_block.get('basic')}")
                            print(f"  - price.product: {price_block.get('product')}")
                            print(f"  - price.total: {price_block.get('total')}")
                    
                    # Сохраняем полный ответ
                    with open("search_response.json", "w", encoding="utf-8") as f:
                        json.dump(product, f, ensure_ascii=False, indent=2)
                    print("\n[FILE] Полный ответ сохранён в search_response.json")
                    
                    return True
                else:
                    print(f"[FAIL] Артикул {article} не найден в результатах поиска")
            else:
                print("[FAIL] Поиск вернул пустой список")
        else:
            print(f"[FAIL] Ошибка: {response.status_code}")
            
    except Exception as e:
        print(f"[EXCEPTION] {e}")
    
    return False


async def test_detail_api(article: int):
    """Тест Detail API - получает 403"""
    print("\n" + "="*80)
    print("2. ТЕСТ DETAIL API")
    print("="*80)
    
    url = "https://card.wb.ru/cards/v2/detail"
    params = {
        "appType": "1",
        "curr": "rub",
        "lang": "ru",
        "dest": "-1257786",
        "spp": "30",
        "ab_testing": "false",
        "nm": str(article),
    }
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    }
    
    try:
        async with requests.AsyncSession(
            headers=headers,
            impersonate="chrome124",
        ) as session:
            response = await session.get(url, params=params)
            
        print(f"Статус: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            products = data.get("data", {}).get("products", [])
            
            if products:
                product = products[0]
                print(f"\n[OK] Товар найден через Detail API!")
                print(f"Название: {product.get('name')}")
                print(f"ID: {product.get('id')}")
                
                with open("detail_response.json", "w", encoding="utf-8") as f:
                    json.dump(product, f, ensure_ascii=False, indent=2)
                print("[FILE] Полный ответ сохранён в detail_response.json")
                
                return True
            else:
                print("[FAIL] Detail API вернул пустой список товаров")
        else:
            print(f"[FAIL] Ошибка: {response.status_code}")
            print(f"Текст ответа (первые 500 символов):")
            try:
                print(response.text[:500])
            except:
                pass
                
    except Exception as e:
        print(f"[EXCEPTION] {e}")
    
    return False


async def test_card_cdn(article: int):
    """Тест card.json из CDN"""
    print("\n" + "="*80)
    print("3. ТЕСТ CARD.JSON CDN")
    print("="*80)
    
    vol = article // 100000
    basket = f"{(vol % 40) + 1:02d}"  # Упрощенная логика
    
    url = (
        f"https://basket-{basket}.wbbasket.ru"
        f"/vol{vol}/part{article // 1000}/{article}/info/ru/card.json"
    )
    
    print(f"URL: {url}")
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    
    try:
        async with requests.AsyncSession(
            headers=headers,
            impersonate="chrome124",
        ) as session:
            response = await session.get(url)
            
        print(f"Статус: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"\n[OK] Card.json получен!")
            print(f"Название: {data.get('imt_name')}")
            print(f"Бренд: {(data.get('selling') or {}).get('brand_name')}")
            print(f"Описание (первые 100 символов): {(data.get('description') or '')[:100]}")
            print(f"Фото: {data.get('media', {}).get('photo_count')} шт")
            
            # Проверяем наличие цен в card.json
            print(f"\nЦены в card.json:")
            print(f"  - priceU: {data.get('priceU')}")
            print(f"  - salePriceU: {data.get('salePriceU')}")
            print(f"  - extended.basicPriceU: {data.get('extended', {}).get('basicPriceU')}")
            
            with open("card_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print("\n[FILE] Полный ответ сохранён в card_response.json")
            
            return True
        else:
            print(f"[FAIL] Ошибка: {response.status_code}")
            
    except Exception as e:
        print(f"[EXCEPTION] {e}")
    
    return False


async def main():
    print("\n")
    print("=" * 80)
    print(" "*20 + "ДИАГНОСТИКА ИСТОЧНИКОВ WB")
    print("=" * 80)
    
    # Тестовый артикул (популярный товар)
    article = 211246754
    
    print(f"\nТестируемый артикул: {article}")
    print(f"Ссылка: https://www.wildberries.ru/catalog/{article}/detail.aspx")
    
    # Запускаем все тесты
    search_ok = await test_search_api(article)
    detail_ok = await test_detail_api(article)
    card_ok = await test_card_cdn(article)
    
    # Итоги
    print("\n" + "="*80)
    print("ИТОГИ")
    print("="*80)
    print(f"Search API:  {'[OK] РАБОТАЕТ' if search_ok else '[FAIL] НЕ РАБОТАЕТ'}")
    print(f"Detail API:  {'[OK] РАБОТАЕТ' if detail_ok else '[FAIL] НЕ РАБОТАЕТ'}")
    print(f"Card CDN:    {'[OK] РАБОТАЕТ' if card_ok else '[FAIL] НЕ РАБОТАЕТ'}")
    
    if search_ok:
        print("\n" + "="*80)
        print("РЕКОМЕНДАЦИЯ:")
        print("="*80)
        print("Search API работает и возвращает цены!")
        print("SearchFallbackSource уже настроен и должен работать.")
        print("Проверьте логи WBEngine при запуске бота.")
        print("="*80)


if __name__ == "__main__":
    asyncio.run(main())
