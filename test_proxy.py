"""
Простой тест прокси через httpx.
Проверяет работоспособность прокси независимо от проекта.
"""

import asyncio
import time
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


# Загрузить .env
PROJECT_ROOT = Path(__file__).parent
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Получить прокси из .env
PROXY_URL = os.getenv("WB_PROXY_URLS", "").strip()

# API для проверки IP
IP_API = "https://api.ipify.org?format=json"


async def get_ip_without_proxy():
    """Получить свой IP без прокси"""
    print("\n" + "="*80)
    print("1. ЗАПРОС БЕЗ ПРОКСИ")
    print("="*80)
    
    try:
        start = time.time()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(IP_API)
        
        elapsed = time.time() - start
        
        if response.status_code == 200:
            data = response.json()
            ip = data.get("ip", "unknown")
            
            print(f"\n[OK] Запрос успешен")
            print(f"Ваш IP: {ip}")
            print(f"Время: {elapsed:.2f} сек")
            print(f"Статус: {response.status_code}")
            
            return ip, elapsed, None
        else:
            print(f"\n[FAIL] Статус: {response.status_code}")
            print(f"Ответ: {response.text}")
            return None, elapsed, f"Status {response.status_code}"
            
    except Exception as e:
        elapsed = time.time() - start
        
        print(f"\n[ERROR] {type(e).__name__}")
        print(f"Сообщение: {e}")
        print(f"Время до ошибки: {elapsed:.2f} сек")
        
        return None, elapsed, str(e)


async def get_ip_with_proxy(proxy_url):
    """Получить IP через прокси"""
    print("\n" + "="*80)
    print("2. ЗАПРОС ЧЕРЕЗ ПРОКСИ")
    print("="*80)
    print(f"Прокси: {proxy_url}")
    
    try:
        start = time.time()
        
        # httpx использует параметр 'proxy', не 'proxies'
        async with httpx.AsyncClient(
            proxy=proxy_url,
            timeout=15.0
        ) as client:
            response = await client.get(IP_API)
        
        elapsed = time.time() - start
        
        if response.status_code == 200:
            data = response.json()
            ip = data.get("ip", "unknown")
            
            print(f"\n[OK] Запрос успешен")
            print(f"IP через прокси: {ip}")
            print(f"Время: {elapsed:.2f} сек")
            print(f"Статус: {response.status_code}")
            
            return ip, elapsed, None
        else:
            print(f"\n[FAIL] Статус: {response.status_code}")
            print(f"Ответ: {response.text}")
            return None, elapsed, f"Status {response.status_code}"
            
    except Exception as e:
        elapsed = time.time() - start
        
        print(f"\n[ERROR] {type(e).__name__}")
        print(f"Сообщение: {e}")
        print(f"Время до ошибки: {elapsed:.2f} сек")
        
        # Полная ошибка для отладки
        import traceback
        print(f"\nПОЛНАЯ ТРАССИРОВКА:")
        traceback.print_exc()
        
        return None, elapsed, str(e)


async def main():
    print("\n" + "="*80)
    print("ТЕСТ ПРОКСИ ЧЕРЕЗ HTTPX")
    print("="*80)
    
    # Проверка наличия прокси в .env
    if not PROXY_URL:
        print("\n[ERROR] Прокси не найден в .env")
        print("Добавьте: WB_PROXY_URLS=http://user:pass@host:port")
        return
    
    print(f"\nПрокси из .env:")
    print(f"  {PROXY_URL}")
    
    # 1. Запрос без прокси
    ip_direct, time_direct, error_direct = await get_ip_without_proxy()
    
    # 2. Запрос через прокси
    ip_proxy, time_proxy, error_proxy = await get_ip_with_proxy(PROXY_URL)
    
    # 3. Итоги
    print("\n" + "="*80)
    print("ИТОГИ")
    print("="*80)
    
    print(f"\nБЕЗ ПРОКСИ:")
    if error_direct:
        print(f"  [FAIL] Ошибка: {error_direct}")
    else:
        print(f"  [OK] IP: {ip_direct}")
        print(f"       Время: {time_direct:.2f} сек")
    
    print(f"\nЧЕРЕЗ ПРОКСИ:")
    if error_proxy:
        print(f"  [FAIL] Ошибка: {error_proxy}")
        print(f"         Время до ошибки: {time_proxy:.2f} сек")
    else:
        print(f"  [OK] IP: {ip_proxy}")
        print(f"       Время: {time_proxy:.2f} сек")
    
    # Анализ
    print("\n" + "="*80)
    print("АНАЛИЗ")
    print("="*80)
    
    if error_direct and error_proxy:
        print("\n[FAIL] Оба запроса провалились")
        print("Проблема с интернетом или firewall")
        
    elif error_direct and not error_proxy:
        print("\n[STRANGE] Без прокси не работает, через прокси работает")
        print("Возможно firewall блокирует прямые запросы")
        
    elif not error_direct and error_proxy:
        print("\n[FAIL] Прокси НЕ РАБОТАЕТ")
        
        if "timeout" in error_proxy.lower():
            print("\nПРИЧИНА: Connection Timeout")
            print("  - Прокси сервер не отвечает")
            print("  - Проверьте host:port")
            print("  - Проверьте что прокси активен")
            
        elif "connection" in error_proxy.lower():
            print("\nПРИЧИНА: Connection Error")
            print("  - Не удается подключиться к прокси")
            print("  - Проверьте host:port")
            
        elif "auth" in error_proxy.lower() or "407" in error_proxy:
            print("\nПРИЧИНА: Authentication Error")
            print("  - Неверные username:password")
            print("  - Проверьте credentials")
            
        else:
            print(f"\nПРИЧИНА: {error_proxy}")
            
    elif not error_direct and not error_proxy:
        if ip_direct == ip_proxy:
            print("\n[WARNING] IP одинаковый!")
            print("Прокси НЕ используется (возможно неправильный формат)")
        else:
            print("\n[SUCCESS] Прокси РАБОТАЕТ!")
            print(f"Ваш IP: {ip_direct}")
            print(f"IP прокси: {ip_proxy}")
            print(f"Разница во времени: {abs(time_proxy - time_direct):.2f} сек")


if __name__ == "__main__":
    asyncio.run(main())
