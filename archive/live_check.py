import asyncio
import sys
from pathlib import Path

# Добавляем папку backend в путь импортов
sys.path.append(str(Path(__file__).resolve().parent.parent))

from providers.wb_public import (
    WildberriesPublicProvider,
    WildberriesParsingError,
)

async def main():
    if len(sys.argv) != 2:
        print("Использование:")
        print("python scripts/live_check.py <ссылка_или_nm_id>")
        return

    provider = WildberriesPublicProvider()

    try:
        product = await provider.get_product(sys.argv[1])

        print("\n========== РЕЗУЛЬТАТ ==========")
        print(f"ID:        {product.nm_id}")
        print(f"Название:  {product.name}")
        print(f"Цена:      {product.price} ₽")
        print(f"Рейтинг:   {product.rating}")
        print(f"Отзывы:    {product.feedbacks_count}")

    except WildberriesParsingError as e:
        print("\nОшибка:")
        print(e)


if __name__ == "__main__":
    asyncio.run(main())