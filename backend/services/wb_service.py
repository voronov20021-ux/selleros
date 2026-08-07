"""
WBService — теперь просто запуск/остановка WB Engine.

Раньше здесь был BrowserPool и методы analyze_product()/search(),
но проверка по всему проекту показала: их уже никто не вызывает —
всё идёт через ProductService и AIAnalyzer напрямую. Оставляю файл
и класс на месте (ничего не удаляю), но внутри теперь только то,
что реально нужно: жизненный цикл WBEngine.
"""

from backend.wb_engine import WBEngine


class WBService:

    def __init__(self, engine: WBEngine):
        self.engine = engine

    async def start(self) -> None:
        # У WBEngine пока нет отдельного шага запуска (источники сами
        # открывают и закрывают соединения на каждый запрос) — метод
        # оставлен для симметрии с stop() и на случай, если позже
        # какому-то источнику понадобится долгоживущее соединение.
        pass

    async def stop(self) -> None:
        pass
