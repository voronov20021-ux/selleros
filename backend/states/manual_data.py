from aiogram.fsm.state import State, StatesGroup


class ManualSellerData(StatesGroup):
    """Пошаговый ручной ввод данных продавца (цена/рейтинг/отзывы + доп. поля)."""

    waiting_for_price = State()
    waiting_for_rating = State()
    waiting_for_feedbacks = State()
    waiting_for_extra_choice = State()
    waiting_for_sales = State()
    waiting_for_orders = State()
    waiting_for_period = State()
