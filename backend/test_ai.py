"""
Проверка Seller AI без Telegram.

    python -m backend.test_ai            — задать тестовый вопрос
    python -m backend.test_ai --models   — список моделей VseGPT (Claude)
"""

import asyncio
import logging
import sys

from backend import config
from backend.services.ai_service import AIService


async def show_models():
    from backend.ai.providers.claude import ClaudeProvider

    print("Запрашиваю список моделей у VseGPT...\n")
    models = await ClaudeProvider().list_models()

    claude = [m for m in models if "claude" in m.lower()]

    print(f"Всего моделей: {len(models)}")
    print(f"\nМодели Claude ({len(claude)}):")
    for model in claude:
        mark = " ← выбрана в .env" if model == config.CLAUDE_MODEL else ""
        print(f"  {model}{mark}")

    if config.CLAUDE_MODEL not in models:
        print(f"\n⚠️  CLAUDE_MODEL='{config.CLAUDE_MODEL}' в списке не найдена.")


async def ask():
    ai = AIService()

    if not ai.enabled:
        print("🔴 Seller AI выключен: в .env стоит AI_PROVIDER=off")
        return

    print("Спрашиваем Seller AI...\n")

    answer = await ai.generate(
        "Ты эксперт Wildberries. Одним предложением: "
        "что сильнее всего влияет на продажи карточки товара?"
    )

    if answer is None:
        print("❌ Seller AI не ответил.")
        print(f"   Причина: {ai.last_error}")
        return

    print("✅ Ответ Seller AI:\n")
    print(answer)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print(config.describe())
    print()

    if "--models" in sys.argv:
        await show_models()
    else:
        await ask()


if __name__ == "__main__":
    asyncio.run(main())
