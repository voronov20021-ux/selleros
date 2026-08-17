from aiogram.fsm.state import State, StatesGroup


class ManualSellerData(StatesGroup):
    """
    Ручной ввод:
      1) gap-fill CARD commercial (только отсутствующие price/rating/feedbacks)
      2) optional private metrics (CTR/CVR/sales/...)
    Не спрашивать заново поля, уже полученные с карточки.
    """

    waiting_for_price = State()
    waiting_for_rating = State()
    waiting_for_feedbacks = State()
    waiting_for_extra_choice = State()
    # Private metrics (все optional, «-» = пропуск)
    waiting_for_ctr = State()
    waiting_for_cvr = State()
    waiting_for_impressions = State()
    waiting_for_views = State()
    waiting_for_sales = State()
    waiting_for_orders = State()
    waiting_for_returns = State()
    waiting_for_ad_spend = State()
    waiting_for_cost = State()
    waiting_for_commission = State()
    waiting_for_logistics = State()
    waiting_for_storage = State()
    waiting_for_period = State()
