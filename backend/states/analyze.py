from aiogram.fsm.state import State, StatesGroup


class AnalyzeCard(StatesGroup):
    waiting_for_link = State()