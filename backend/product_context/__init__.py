"""
backend.product_context — единый слой агрегации карточки для Argus.

Использование::

    from backend.product_context import ProductContextBuilder

    builder = ProductContextBuilder(
        product_service=product_service,   # без BrowserProvider в chain
        reviews_service=wb_reviews,
        browser_provider=browser_provider, # только fallback
        product_cache=public_cache,        # optional read
    )
    ctx = await builder.build(article)
    prompt = ctx.to_prompt()               # → в Argus / AIAnalyzer путь

Тонкий хук для prompt-сборки (не меняет reasoner)::

    from backend.product_context import attach_context_to_prompt
    full = attach_context_to_prompt(existing_prompt, ctx)
"""

from backend.product_context.models import (
    CompletenessReport,
    ProductContext,
    ProductDescription,
    ProductIdentity,
    ProductMedia,
    ProductPricing,
    ProductReviews,
)
from backend.product_context.builder import ProductContextBuilder


def attach_context_to_prompt(
    base_prompt: str | None,
    context: ProductContext | None,
    *,
    separator: str = "\n\n",
) -> str:
    """
    Безопасно дописать context.to_prompt() к уже собранному prompt.

    Не трогает analyzer/brain/reasoner — только конкатенация строк.
    """
    base = (base_prompt or "").rstrip()
    if context is None:
        return base
    block = context.to_prompt().strip()
    if not block:
        return base
    if not base:
        return block
    return f"{base}{separator}{block}"


__all__ = [
    "CompletenessReport",
    "ProductContext",
    "ProductIdentity",
    "ProductPricing",
    "ProductMedia",
    "ProductDescription",
    "ProductReviews",
    "ProductContextBuilder",
    "attach_context_to_prompt",
]
