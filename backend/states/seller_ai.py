from aiogram.fsm.state import State, StatesGroup


class SellerAIChat(StatesGroup):
    waiting_for_question = State()
