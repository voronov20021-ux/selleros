"""
OpenAIProvider — GPT через тот же VseGPT.

Заглушка удалена. Отдельный ключ OpenAI не нужен:
VseGPT — шлюз сразу ко всем моделям, меняется только ID модели.
"""

from backend.ai.providers.vsegpt import VseGPTProvider
from backend.config import OPENAI_MODEL


class OpenAIProvider(VseGPTProvider):

    provider_label = "OpenAI"

    def __init__(self, model: str | None = None):
        super().__init__(model=model or OPENAI_MODEL)
