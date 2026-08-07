from aiogram.fsm.state import State, StatesGroup


class ProductChat(StatesGroup):
    # Режим «🧠 Обсудить товар»: общаемся только про последний товар,
    # пока пользователь не нажмёт «Закончить диалог».
    discussing = State()
