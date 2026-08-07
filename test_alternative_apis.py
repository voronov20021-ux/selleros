"""
Поиск альтернативных источников цен на WB.
Проверяем разные публичные API.
"""

import asyncio
import json
from curl_cffi import requests


async def test_catalog_api(article: int):
    """Пробуем catalog API"""
    print("\n" + "="*80)
    print("ТЕСТ CATALOG API")
    print("="*80)
    
    # Попробуем несколько вариантов catalog API
    urls = [
        f"https://catalog.wb.ru/catalog/{article}/data",
        f"https://www.wildberries.ru/webapi/product/{article}",
        f"https://wbx-content-v2.wbstatic.net/price/{article}.json",
    ]
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    
    for url in urls:
        print(f"\nПробую: {url}")
        try:
            async with requests.AsyncSession(
                headers=headers,
                impersonate="chrome124",
            ) as session:
                response = await session.get(url, timeout=10)
                
            print(f"Статус: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    print("[OK] JSON получен!")
                    print(json.dumps(data, ensure_ascii=False, indent=2)[:500])
                    
                    filename = f"catalog_api_{urls.index(url)}.json"
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"[FILE] Сохранено в {filename}")
                    
                    return True
                except:
                    print(f"[INFO] Не JSON, текст: {response.text[:200]}")
            
        except Exception as e:
            print(f"[FAIL] {e}")
    
    return False


async def test_nm_detail_new(article: int):
    """Пробуем новый формат nm-detail"""
    print("\n" + "="*80)
    print("ТЕСТ NM-DETAIL NEW API")
    print("="*80)
    
    # Новый формат, который может работать
    url = f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={article}"
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "ru",
        "Referer": f"https://www.wildberries.ru/catalog/{article}/detail.aspx",
    }
    
    try:
        async with requests.AsyncSession(
            headers=headers,
            impersonate="chrome124",
        ) as session:
            response = await session.get(url, timeout=10)
            
        print(f"Статус: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("[OK] Данные получены!")
            
            products = data.get("data", {}).get("products", [])
            if products:
                product = products[0]
                print(f"Название: {product.get('name')}")
                print(f"ID: {product.get('id')}")
                
                with open("nm_detail_v1.json", "w", encoding="utf-8") as f:
                    json.dump(product, f, ensure_ascii=False, indent=2)
                print("[FILE] Сохранено в nm_detail_v1.json")
                
                return True
        else:
            print(f"[FAIL] Статус {response.status_code}")
            print(response.text[:300])
            
    except Exception as e:
        print(f"[EXCEPTION] {e}")
    
    return False


async def test_public_basket_info(article: int):
    """Пробуем найти info.json или другие файлы в basket"""
    print("\n" + "="*80)
    print("ТЕСТ BASKET PUBLIC FILES")
    print("="*80)
    
    vol = article // 100000
    part = article // 1000
    
    # Пробуем разные basket номера
    for basket_num in range(1, 41):
        basket = f"{basket_num:02d}"
        
        # Пробуем разные файлы
        files = [
            f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/info/price-history.json",
            f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/info/sellers.json",
            f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/info/ru/card.json",
            f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/detail.json",
        ]
        
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
        }
        
        for url in files:
            try:
                async with requests.AsyncSession(
                    headers=headers,
                    impersonate="chrome124",
                ) as session:
                    response = await session.get(url, timeout=5)
                    
                if response.status_code == 200:
                    print(f"\n[OK] Найден: {url}")
                    print(f"Статус: {response.status_code}")
                    
                    try:
                        data = response.json()
                        print("Содержимое (первые 300 символов):")
                        print(json.dumps(data, ensure_ascii=False, indent=2)[:300])
                        
                        filename = f"basket_found_{basket_num}.json"
                        with open(filename, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        print(f"[FILE] Сохранено в {filename}")
                        
                        return True, url, data
                    except:
                        print(f"Не JSON: {response.text[:100]}")
                        
            except:
                continue  # Таймаут или ошибка - пробуем дальше
    
    print("[FAIL] Ничего не найдено в basket CDN")
    return False, None, None


async def main():
    print("\n" + "="*80)
    print("ПОИСК АЛЬТЕРНАТИВНЫХ ИСТОЧНИКОВ ЦЕН WB")
    print("="*80)
    
    article = 211246754
    print(f"Артикул: {article}")
    
    # Тестируем все варианты
    catalog_ok = await test_catalog_api(article)
    nm_ok = await test_nm_detail_new(article)
    basket_ok, basket_url, basket_data = await test_public_basket_info(article)
    
    # Итоги
    print("\n" + "="*80)
    print("ИТОГИ")
    print("="*80)
    print(f"Catalog API:     {'[OK]' if catalog_ok else '[FAIL]'}")
    print(f"NM Detail v1:    {'[OK]' if nm_ok else '[FAIL]'}")
    print(f"Basket Public:   {'[OK]' if basket_ok else '[FAIL]'}")
    
    if basket_ok:
        print(f"\n[SUCCESS] Найден рабочий источник!")
        print(f"URL: {basket_url}")
    elif catalog_ok or nm_ok:
        print(f"\n[SUCCESS] Найден альтернативный API!")
    else:
        print("\n[INFO] Все публичные API недоступны без прокси.")
        print("Рекомендация: использовать рабочий прокси или браузер.")


if __name__ == "__main__":
    asyncio.run(main())
