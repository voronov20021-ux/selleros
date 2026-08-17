"""
config.py — ЕДИНСТВЕННОЕ место, где проект читает .env.

Правило проекта:
    Никакой файл не вызывает os.getenv() напрямую.
    Нужна новая переменная — добавляем её здесь.

Так .env гарантированно загружен до первого чтения переменных,
независимо от того, чем запущен проект:
    python -m backend.bot
    python -m backend.test_ai
    pytest
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger("selleros.config")

# --- Ищем .env по абсолютному пути ------------------------------------------
# Path(__file__) = .../SellerOS/backend/config.py
# parents[1]     = .../SellerOS
# Благодаря этому неважно, из какой папки запущен проект.

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


def _check_env_file(path: Path) -> None:
    """Ловим типичные проблемы с .env ДО того, как они станут загадкой."""

    if not path.exists():
        print(f"⚠️  .env не найден: {path}")
        print("   Создайте его рядом с папкой backend.")
        return

    raw = path.read_bytes()

    # PowerShell по умолчанию сохраняет файлы в UTF-16 —
    # python-dotenv такой файл прочитать не может.
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        raise ValueError(
            f".env сохранён в UTF-16: {path}\n"
            "Пересохраните его в UTF-8.\n"
            "В PowerShell: Set-Content .env -Encoding utf8"
        )

    text = raw.decode("utf-8-sig", errors="replace")

    # Дубли: python-dotenv берёт ПОСЛЕДНЮЮ строку с этим ключом.
    seen: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        seen[key] = seen.get(key, 0) + 1

    for key, count in seen.items():
        if count > 1:
            print(
                f"⚠️  В .env переменная {key} указана {count} раза. "
                f"Сработает ПОСЛЕДНЯЯ. Удалите лишние строки."
            )


_check_env_file(ENV_PATH)

# override=True — значения из .env главнее переменных окружения ОС.
# Без этого забытая в терминале переменная (например,
# $env:AI_PROVIDER="off" в PowerShell) молча побеждала бы .env.
load_dotenv(dotenv_path=ENV_PATH, override=True)


def get(name: str, default: str = "") -> str:
    """Прочитать переменную: без лишних пробелов и кавычек."""
    value = os.getenv(name, default) or ""
    return value.strip().strip('"').strip("'")


def _get_int(name: str, default: int) -> int:
    """Число из .env. Опечатка не должна ронять бот."""
    raw = get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        print(f"⚠️  {name}='{raw}' — не число. Использую {default}.")
        return default


def _get_float(name: str, default: float) -> float:
    raw = get(name)
    try:
        return float(raw.replace(",", ".")) if raw else default
    except ValueError:
        print(f"⚠️  {name}='{raw}' — не число. Использую {default}.")
        return default


# --- Telegram ---------------------------------------------------------------

BOT_TOKEN = get("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError(f"BOT_TOKEN не найден в {ENV_PATH}")

#: Максимальный возраст Telegram WebApp initData (секунды). По умолчанию 24ч.
TELEGRAM_AUTH_MAX_AGE = _get_int("TELEGRAM_AUTH_MAX_AGE", 86400)

#: TTL opaque Mini App session token (секунды). По умолчанию 7 дней.
AUTH_SESSION_TTL_SECONDS = _get_int("AUTH_SESSION_TTL_SECONDS", 604800)

#: Encryption key for seller WB API credentials at rest (onboarding).
#: If empty, a deterministic key is derived from BOT_TOKEN (dev/MVP only).
SELLER_SECRETS_KEY = get("SELLER_SECRETS_KEY")

#: Local Mini App DEV preview. Default OFF. Fail closed: never auto-enable
#: in production. Even when 1, POST /api/auth/dev only succeeds from localhost.
#: Production MUST keep this 0/false/unset.
MINIAPP_DEV_AUTH = get("MINIAPP_DEV_AUTH", "0").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

#: Optional environment label. If production/prod, DEV auth is refused
#: even on localhost (belt and suspenders).
APP_ENV = get("APP_ENV", "").lower()

#: Public HTTPS origin of the Mini App (GitHub Pages / same Seller OS UI).
#: Used only for the Telegram button «Открыть полный разбор в Seller OS».
#: Empty → hide the button (do not invent a second app or a fake URL).
MINIAPP_PUBLIC_URL = (get("MINIAPP_PUBLIC_URL") or "").strip().rstrip("/")


def miniapp_product_url(article: int | None) -> str:
    """Hash-router deep link into the existing Mini App product screen."""
    if not MINIAPP_PUBLIC_URL or article is None:
        return ""
    try:
        nm = int(article)
    except (TypeError, ValueError):
        return ""
    if nm <= 0:
        return ""
    return f"{MINIAPP_PUBLIC_URL}/#/products/{nm}"

# --- Долговременная память ---------------------------------------------------

#: Файл SQLite с памятью ARGUS: диалоги, товары, история, рекомендации.
#: Путь строится от корня проекта, поэтому не важно, откуда запущен бот.
MEMORY_DB_PATH = str(PROJECT_ROOT / get("MEMORY_DB_PATH", "data/argus_memory.db"))

#: Файл SQLite Intelligence Layer: рыночные данные, тренды, события.
INTELLIGENCE_DB_PATH = str(PROJECT_ROOT / get("INTELLIGENCE_DB_PATH", "data/intelligence.db"))

#: Competitor evidence enrichment: только top-N после ranking (не все 10–30).
COMPETITOR_ENRICH_TOP_N = max(1, min(_get_int("COMPETITOR_ENRICH_TOP_N", 5), 10))
#: Повторный public fetch того же competitor+query не чаще TTL (секунды).
COMPETITOR_ENRICH_TTL_SECONDS = max(60, _get_int("COMPETITOR_ENRICH_TTL_SECONDS", 6 * 3600))

#: Максимум текстов отзывов в одном проходе Review Intelligence → Argus.
#: Один HTTP/cache fetch; лимит применяется при разборе payload, не N запросов.
MAX_REVIEW_TEXTS = _get_int("MAX_REVIEW_TEXTS", 60)

# --- WB Engine ----------------------------------------------------------------

#: Прокси для источников Wildberries (CDN / Search / feedbacks).
#: PROXY #2. Пусто — источники ходят напрямую.
#: Один прокси или несколько через запятую (WB_PROXY_URLS).
#: Либо один URL в WB_PROXY.
WB_PROXY = get("WB_PROXY")
WB_PROXY_URLS = get("WB_PROXY_URLS")

#: Схема для WBEngine ProxyPool: http | socks5 (socks5 → socks5h).
WB_PROXY_SCHEME = get("WB_PROXY_SCHEME", "socks5").strip().lower() or "socks5"
if WB_PROXY_SCHEME not in ("http", "https", "socks5", "socks5h", "socks"):
    print(
        f"⚠️  WB_PROXY_SCHEME='{WB_PROXY_SCHEME}' — ожидалось http|socks5. "
        "Использую socks5."
    )
    WB_PROXY_SCHEME = "socks5"


def effective_wb_proxy_urls() -> str:
    """Склеить WB_PROXY + WB_PROXY_URLS в одну CSV-строку для ProxyPool."""
    parts: list[str] = []
    if WB_PROXY and WB_PROXY.strip():
        parts.append(WB_PROXY.strip())
    if WB_PROXY_URLS and WB_PROXY_URLS.strip():
        parts.extend(p.strip() for p in WB_PROXY_URLS.split(",") if p.strip())
    return ",".join(parts)


#: Ключ официального Seller API Wildberries. Пусто — источник выключен,
#: WB Engine сразу переходит к следующему по приоритету.
WB_SELLER_API_KEY = get("WB_SELLER_API_KEY")

# --- BrowserProvider (Chromium) ---------------------------------------------
# PROXY #1 → BrowserProvider (BROWSER_PROXY) — ТОЛЬКО HTTP.
# Chromium не поддерживает authenticated SOCKS5.


def _parse_ttl_seconds(raw: str, default: int) -> int:
    """'7d' | '24h' | '90m' | '3600' → секунды."""
    text = (raw or "").strip().lower()
    if not text:
        return default
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text[-1] in mult and text[:-1].replace(".", "", 1).isdigit():
        try:
            return int(float(text[:-1]) * mult[text[-1]])
        except ValueError:
            return default
    try:
        return int(float(text))
    except ValueError:
        print(f"⚠️  TTL='{raw}' — не разобрать. Использую {default} сек.")
        return default


BROWSER_ENABLED = get("BROWSER_ENABLED", "0").lower() not in (
    "0", "false", "off", "no", "",
)
BROWSER_HEADLESS = get("BROWSER_HEADLESS", "1").lower() not in (
    "0", "false", "off", "no",
)
BROWSER_PROXY = get("BROWSER_PROXY")  # PROXY #1; только HTTP для Chromium

#: Список HTTP proxy через запятую/переносы (rotation для BrowserProvider).
#: Пример: BROWSER_PROXY_LIST=http://u1:p1@h1:8080,http://u2:p2@h2:8080
BROWSER_PROXY_LIST = get("BROWSER_PROXY_LIST")

#: Всегда http для BrowserProvider (socks5 здесь запрещён).
BROWSER_PROXY_SCHEME = get("BROWSER_PROXY_SCHEME", "http").strip().lower() or "http"
if BROWSER_PROXY_SCHEME.startswith("socks"):
    print(
        "WARNING: Chromium does not support authenticated SOCKS5 proxy - "
        "BROWSER_PROXY_SCHEME forced to http"
    )
    BROWSER_PROXY_SCHEME = "http"
elif BROWSER_PROXY_SCHEME not in ("http", "https"):
    print(
        f"WARNING: BROWSER_PROXY_SCHEME='{BROWSER_PROXY_SCHEME}' - "
        "BrowserProvider allows only http. Using http."
    )
    BROWSER_PROXY_SCHEME = "http"

# --- SOCKS5 → HTTP CONNECT bridge (localhost → authenticated SOCKS5) ----------
# Chromium → http://127.0.0.1:8080 → SOCKS_UPSTREAM_*.
# WBEngine / WB_PROXY* на bridge НЕ переключаются.

SOCKS_BRIDGE_ENABLED = get("SOCKS_BRIDGE_ENABLED", "0").lower() not in (
    "0", "false", "off", "no", "",
)
SOCKS_BRIDGE_HOST = get("SOCKS_BRIDGE_HOST", "127.0.0.1") or "127.0.0.1"
SOCKS_BRIDGE_PORT = _get_int("SOCKS_BRIDGE_PORT", 8080)
SOCKS_UPSTREAM_HOST = get("SOCKS_UPSTREAM_HOST")
SOCKS_UPSTREAM_PORT = _get_int("SOCKS_UPSTREAM_PORT", 0)
SOCKS_UPSTREAM_USER = get("SOCKS_UPSTREAM_USER")
SOCKS_UPSTREAM_PASSWORD = get("SOCKS_UPSTREAM_PASSWORD")


def socks_bridge_http_url() -> str:
    """Локальный HTTP URL bridge для Playwright (без auth)."""
    return f"http://{SOCKS_BRIDGE_HOST}:{SOCKS_BRIDGE_PORT}"


def effective_browser_proxy_urls() -> list[str]:
    """
    HTTP proxies для BrowserProxyPool:
    при SOCKS_BRIDGE_ENABLED — сначала localhost bridge,
    затем BROWSER_PROXY + BROWSER_PROXY_LIST.

    SOCKS5 URLs не подменяются на http и не берутся из WB_PROXY*.
    WBEngine на bridge не переключается.
    """
    parts: list[str] = []
    if SOCKS_BRIDGE_ENABLED:
        parts.append(socks_bridge_http_url())
    if BROWSER_PROXY and BROWSER_PROXY.strip():
        parts.append(BROWSER_PROXY.strip())
    if BROWSER_PROXY_LIST and BROWSER_PROXY_LIST.strip():
        text = BROWSER_PROXY_LIST.replace("\r", "\n")
        for chunk in text.split(","):
            for line in chunk.split("\n"):
                s = line.strip().strip('"').strip("'")
                if s:
                    parts.append(s)
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _warn_browser_proxy_schemes() -> None:
    """Явный warning, если в Browser попал socks5 (без auto-swap / WB fallback)."""
    from urllib.parse import urlsplit

    for raw in effective_browser_proxy_urls():
        try:
            scheme = (urlsplit(raw.strip()).scheme or "http").lower()
        except Exception:
            continue
        if scheme.startswith("socks"):
            print(
                "WARNING: Chromium does not support authenticated SOCKS5 proxy - "
                "BROWSER_PROXY must be http://LOGIN:PASSWORD@HOST:PORT "
                "(SOCKS5 left for WB_PROXY only; no auto-swap)"
            )
            return


_warn_browser_proxy_schemes()


#: sticky | base | as_is — sticky добавляет -session-N-time-M к login.
BROWSER_PROXY_MODE = get("BROWSER_PROXY_MODE", "sticky").strip().lower() or "sticky"
BROWSER_PROXY_SESSION_ID = get("BROWSER_PROXY_SESSION_ID", "1") or "1"
BROWSER_PROXY_SESSION_TIME = get("BROWSER_PROXY_SESSION_TIME", "60") or "60"

#: Playwright page/navigation timeout (ms).
BROWSER_TIMEOUT_MS = _get_int("BROWSER_TIMEOUT", 60000)

#: Chromium --disable-http2 (рекомендуется для residential HTTP proxy).
BROWSER_DISABLE_HTTP2 = get("BROWSER_DISABLE_HTTP2", "true").lower() not in (
    "0", "false", "off", "no", "",
)

#: Установленный Google Chrome + CDP (не Playwright chromium.launch).
BROWSER_USE_SYSTEM_CHROME = get("BROWSER_USE_SYSTEM_CHROME", "1").lower() not in (
    "0", "false", "off", "no", "",
)
BROWSER_CHROME_PATH = get("BROWSER_CHROME_PATH") or None

BROWSER_RETRIES = max(1, _get_int("BROWSER_RETRIES", 1))

#: Единый TTL публичного browser-кэша (MVP).
_BROWSER_TTL_DEFAULT = _parse_ttl_seconds(get("BROWSER_CACHE_TTL", "7d"), 7 * 86400)
BROWSER_CACHE_TTL_PRODUCT = _parse_ttl_seconds(
    get("BROWSER_CACHE_TTL_PRODUCT", ""), _BROWSER_TTL_DEFAULT,
)
BROWSER_CACHE_TTL_REVIEWS = _parse_ttl_seconds(
    get("BROWSER_CACHE_TTL_REVIEWS", ""), _BROWSER_TTL_DEFAULT,
)
BROWSER_CACHE_TTL_PHOTOS = _parse_ttl_seconds(
    get("BROWSER_CACHE_TTL_PHOTOS", ""), _BROWSER_TTL_DEFAULT,
)
BROWSER_CACHE_TTL_DESCRIPTION = _parse_ttl_seconds(
    get("BROWSER_CACHE_TTL_DESCRIPTION", ""), _BROWSER_TTL_DEFAULT,
)
BROWSER_CACHE_PATH = str(
    PROJECT_ROOT / get("BROWSER_CACHE_PATH", "data/browser_public_cache.db")
)

# --- Seller AI --------------------------------------------------------------

#: Имя, которое видит продавец в Telegram. ЕДИНСТВЕННОЕ место в проекте,
#: где оно задано как текст. Все остальные файлы берут имя отсюда.
#: Поменять имя ассистента — значит поменять одну эту строку
#: (или AI_NAME в .env, тогда даже код трогать не придётся).
AI_NAME = get("AI_NAME", "АРГУС")

#: Какой провайдер работает под капотом AI_NAME (см. выше).
#: Пользователь этого никогда не видит — для него всегда AI_NAME.
KNOWN_PROVIDERS = ("gemini", "claude", "openai")
OFF_VALUES = ("off", "none", "disabled", "false", "0")

#: Если AI_PROVIDER в .env не указан вообще — берём это значение.
#: "off" ставится ТОЛЬКО если пользователь явно написал его в .env.
DEFAULT_AI_PROVIDER = "claude"

_raw_provider = get("AI_PROVIDER")

if not _raw_provider:
    AI_PROVIDER = DEFAULT_AI_PROVIDER
    print(
        f"ℹ️  AI_PROVIDER в .env не задан — использую "
        f"{DEFAULT_AI_PROVIDER} по умолчанию."
    )
else:
    AI_PROVIDER = _raw_provider.lower()

    # Опечатка вроде "cluade" раньше приводила к молчаливому
    # отключению AI. Теперь она видна сразу.
    if AI_PROVIDER not in KNOWN_PROVIDERS + OFF_VALUES:
        print(
            f"⚠️  AI_PROVIDER='{_raw_provider}' — неизвестное значение.\n"
            f"   Допустимо: {', '.join(KNOWN_PROVIDERS)} или off.\n"
            f"   Использую {DEFAULT_AI_PROVIDER}."
        )
        AI_PROVIDER = DEFAULT_AI_PROVIDER

AI_ENABLED = AI_PROVIDER not in OFF_VALUES

# --- Ключи AI-провайдеров ---------------------------------------------------

#: VseGPT — российский шлюз к Claude, GPT и другим моделям.
#: Ключ берётся на https://vsegpt.ru/User/API
VSEGPT_API_KEY = get("VSEGPT_API_KEY")
VSEGPT_BASE_URL = get("VSEGPT_BASE_URL", "https://api.vsegpt.ru/v1")

#: ID моделей в каталоге VseGPT: https://vsegpt.ru/Docs/Models
CLAUDE_MODEL = get("CLAUDE_MODEL", "anthropic/claude-sonnet-5")
OPENAI_MODEL = get("OPENAI_MODEL", "openai/gpt-4o-mini")

#: Явный routing: PRIMARY → FALLBACK.
#: Значения — имя провайдера (claude|openai|gemini) ИЛИ model id
#: вида anthropic/... / openai/... (префикс определит провайдера).
#: Если Claude недоступен на тарифе — поставьте PRIMARY_MODEL=openai.
_raw_primary = get("PRIMARY_MODEL", AI_PROVIDER if AI_ENABLED else "openai")
_raw_fallback = get("FALLBACK_MODEL", "openai")


def _resolve_provider_name(raw: str) -> str:
    n = (raw or "").strip().lower()
    if not n:
        return "openai"
    if n in KNOWN_PROVIDERS:
        return n
    if n.startswith("anthropic/") or "claude" in n:
        return "claude"
    if n.startswith("openai/") or n.startswith("gpt-") or n.startswith("o1"):
        return "openai"
    if n.startswith("gemini") or n.startswith("google/"):
        return "gemini"
    return n


PRIMARY_MODEL = _resolve_provider_name(_raw_primary)
FALLBACK_MODEL = _resolve_provider_name(_raw_fallback)

#: Явно отключить Claude (без HTTP 400 на каждый запрос).
CLAUDE_ENABLED = get("CLAUDE_ENABLED", "1").lower() not in (
    "0", "false", "off", "no", "",
)

#: Общие параметры генерации.
AI_TIMEOUT = _get_int("AI_TIMEOUT", 45)
AI_MAX_TOKENS = _get_int("AI_MAX_TOKENS", 1500)
AI_TEMPERATURE = _get_float("AI_TEMPERATURE", 0.7)

#: Gemini ходит напрямую в Google, не через VseGPT.
GEMINI_API_KEY = get("GEMINI_API_KEY")
GEMINI_MODEL = get("GEMINI_MODEL", "gemini-2.5-flash-lite")


def describe() -> str:
    """Строка диагностики для старта бота."""
    if not AI_ENABLED:
        return f"📄 .env: {ENV_PATH}\n🧠 {AI_NAME}: выключен (off)"

    model = {
        "claude": CLAUDE_MODEL,
        "openai": OPENAI_MODEL,
        "gemini": GEMINI_MODEL,
    }.get(PRIMARY_MODEL, "—")

    key_ok = "есть" if (
        VSEGPT_API_KEY if PRIMARY_MODEL in ("claude", "openai") else GEMINI_API_KEY
    ) else "НЕ НАЙДЕН"

    return (
        f"📄 .env: {ENV_PATH}\n"
        f"🧠 {AI_NAME}: включён\n"
        f"   primary: {PRIMARY_MODEL} ({model})\n"
        f"   fallback: {FALLBACK_MODEL}\n"
        f"   claude: {'on' if CLAUDE_ENABLED else 'off'}\n"
        f"   ключ:   {key_ok}\n"
        f"   таймаут: {AI_TIMEOUT} сек"
    )
