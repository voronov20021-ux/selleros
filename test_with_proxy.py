"""
Тест всех API с прокси из .env
"""

import asyncio
import json
from curl_cffi import requests

# Прокси из .env
PROXY_URL = "http://aAx7FV:HF3xjc@192.109.100.96:8000"


async def test_detail_api_with_proxy(article: int):
    """Тест Detail API с прокси"""
    print("\n" + "="*80)
    print("ТЕСТ DETAIL API С ПРОКСИ")
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
    
    proxies = {
        "http": PROXY_URL,
        "https": PROXY_URL,
    }
    
    try:
        async with requests.AsyncSession(
            headers=headers,
            impersonate="chrome124",
            proxies=proxies,
        ) as session:
            response = await session.get(url, params=params, timeout=15)
            
        print(f"Статус: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            products = data.get("data", {}).get("products", [])
            
            if products:
                product = products[0]
                print(f"\n[OK] Товар найден!")
                print(f"ID: {product.get('id')}")
                print(f"Название: {product.get('name')}")
                print(f"Бренд: {product.get('brand')}")
                print(f"Рейтинг: {product.get('reviewRating')}")
                print(f"Отзывы: {product.get('feedbacks')}")
                
                # Проверяем цены
                sizes = product.get("sizes", [])
                if sizes:
                    print(f"\nРазмеров: {len(sizes)}")
                    first_size = sizes[0]
                    price_block = first_size.get("price", {})
                    print(f"\nЦены первого размера:")
                    print(f"  - price.basic (старая): {price_block.get('basic')} копеек")
                    print(f"  - price.product (финальная): {price_block.get('product')} копеек")
                    print(f"  - price.total: {price_block.get('total')} копеек")
                    
                    # В рублях
                    if price_block.get('product'):
                        print(f"\nВ рублях:")
                        print(f"  - Финальная цена: {price_block.get('product') // 100} руб")
                    if price_block.get('basic'):
                        print(f"  - Старая цена: {price_block.get('basic') // 100} руб")
                
                with open("detail_with_proxy.json", "w", encoding="utf-8") as f:
                    json.dump(product, f, ensure_ascii=False, indent=2)
                print("\n[FILE] Сохранено в detail_with_proxy.json")
                
                return True
            else:
                print("[FAIL] Пустой список товаров")
        else:
            print(f"[FAIL] Ошибка {response.status_code}")
            print("Ответ:", response.text[:500])
            
    except Exception as e:
        print(f"[EXCEPTION] {e}")
        import traceback
        traceback.print_exc()
    
    return False


async def test_search_api_with_proxy(article: int):
    """Тест Search API с прокси"""
    print("\n" + "="*80)
    print("ТЕСТ SEARCH API С ПРОКСИ")
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
    
    proxies = {
        "http": PROXY_URL,
        "https": PROXY_URL,
    }
    
    try:
        async with requests.AsyncSession(
            headers=headers,
            impersonate="chrome124",
            proxies=proxies,
        ) as session:
            response = await session.get(url, params=params, timeout=15)
            
        print(f"Статус: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            products = data.get("products", [])
            
            if products:
                product = next((p for p in products if int(p.get("id", -1)) == article), None)
                if product:
                    print(f"\n[OK] Товар найден!")
                    print(f"ID: {product.get('id')}")
                    print(f"Название: {product.get('name')}")
                    print(f"Бренд: {product.get('brand')}")
                    print(f"Рейтинг: {product.get('reviewRating') or product.get('rating')}")
                    print(f"Отзывы: {product.get('feedbacks')}")
                    
                    # Проверяем цены
                    sizes = product.get("sizes", [])
                    if sizes:
                        print(f"\nРазмеров: {len(sizes)}")
                        first_size = sizes[0]
                        price_block = first_size.get("price", {})
                        print(f"\nЦены первого размера:")
                        print(f"  - price.basic: {price_block.get('basic')} копеек")
                        print(f"  - price.product: {price_block.get('product')} копеек")
                        print(f"  - price.total: {price_block.get('total')} копеек")
                    
                    with open("search_with_proxy.json", "w", encoding="utf-8") as f:
                        json.dump(product, f, ensure_ascii=False, indent=2)
                    print("\n[FILE] Сохранено в search_with_proxy.json")
                    
                    return True
                else:
                    print(f"[FAIL] Артикул {article} не найден в результатах")
            else:
                print("[FAIL] Пустой список товаров")
        else:
            print(f"[FAIL] Ошибка {response.status_code}")
            print("Ответ:", response.text[:500])
            
    except Exception as e:
        print(f"[EXCEPTION] {e}")
        import traceback
        traceback.print_exc()
    
    return False


async def main():
    print("\n" + "="*80)
    print("ТЕСТИРОВАНИЕ С ПРОКСИ")
    print("="*80)
    print(f"Прокси: {PROXY_URL}")
    
    article = 211246754
    print(f"Артикул: {article}")
    
    # Сначала тестируем Search API (обычно менее строгий)
    search_ok = await test_search_api_with_proxy(article)
    
    # Потом Detail API
    detail_ok = await test_detail_api_with_proxy(article)
    
    # Итоги
    print("\n" + "="*80)
    print("ИТОГИ С ПРОКСИ")
    print("="*80)
    print(f"Search API:  {'[OK] РАБОТАЕТ' if search_ok else '[FAIL] НЕ РАБОТАЕТ'}")
    print(f"Detail API:  {'[OK] РАБОТАЕТ' if detail_ok else '[FAIL] НЕ РАБОТАЕТ'}")
    
    if search_ok or detail_ok:
        print("\n[SUCCESS] Хотя бы один источник работает с прокси!")
        print("Проблема была в блокировке IP. Прокси решает проблему.")
    else:
        print("\n[FAIL] Даже с прокси не работает. Нужен другой подход.")


if __name__ == "__main__":
    asyncio.run(main())
