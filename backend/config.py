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

# --- Долговременная память ---------------------------------------------------

#: Файл SQLite с памятью ARGUS: диалоги, товары, история, рекомендации.
#: Путь строится от корня проекта, поэтому не важно, откуда запущен бот.
MEMORY_DB_PATH = str(PROJECT_ROOT / get("MEMORY_DB_PATH", "data/argus_memory.db"))

# --- WB Engine ----------------------------------------------------------------

#: Прокси для источников Wildberries. Пусто — источники ходят напрямую.
#: Один прокси или несколько через запятую:
#:   WB_PROXY_URLS=http://user:pass@1.2.3.4:8000
#:   WB_PROXY_URLS=http://a:1111,http://b:2222
WB_PROXY_URLS = get("WB_PROXY_URLS")

#: Ключ официального Seller API Wildberries. Пусто — источник выключен,
#: WB Engine сразу переходит к следующему по приоритету.
WB_SELLER_API_KEY = get("WB_SELLER_API_KEY")

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
    }.get(AI_PROVIDER, "—")

    key_ok = "есть" if (
        VSEGPT_API_KEY if AI_PROVIDER in ("claude", "openai") else GEMINI_API_KEY
    ) else "НЕ НАЙДЕН"

    return (
        f"📄 .env: {ENV_PATH}\n"
        f"🧠 {AI_NAME}: включён\n"
        f"   модель: {model}\n"
        f"   ключ:   {key_ok}\n"
        f"   таймаут: {AI_TIMEOUT} сек"
    )
