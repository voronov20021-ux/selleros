"""
intents.py — классификация сообщений продавца.

Перед тем как отвечать, Seller AI понимает, О ЧЁМ его спросили.
От типа зависит и системный промпт, и какой контекст подставить,
и нужно ли вообще обращаться к модели.

Классификатор построен на правилах: он мгновенный, бесплатный
и предсказуемый. Для дообучения достаточно дописать слова в KEYWORDS.

Позже сюда можно добавить LLM-классификацию для спорных случаев —
интерфейс classify() менять не придётся.
"""

from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    SMALL_TALK = "SMALL_TALK"
    GENERAL_QUESTION = "GENERAL_QUESTION"
    PRODUCT_DISCUSSION = "PRODUCT_DISCUSSION"
    SELLER_ANALYTICS = "SELLER_ANALYTICS"
    PRICING = "PRICING"
    MARKETING = "MARKETING"
    PHOTO = "PHOTO"
    REVIEWS = "REVIEWS"
    LOGISTICS = "LOGISTICS"
    COMPETITOR = "COMPETITOR"
    SOLUTION_RESEARCH = "SOLUTION_RESEARCH"
    FINANCE = "FINANCE"
    KNOWLEDGE = "KNOWLEDGE"
    WB_POLICY = "WB_POLICY"
    ACTION_CHECK = "ACTION_CHECK"


#: Ключевые корни слов по темам. Сравнение идёт по началу слова,
#: поэтому «рекламу», «рекламный», «рекламе» ловятся одним корнем «реклам».
KEYWORDS: dict[Intent, tuple[str, ...]] = {

    Intent.PRICING: (
        "цен", "цену", "подорож", "подеш", "скидк", "акци", "распродаж",
        "маржа", "маржинальн", "себестоимост", "наценк", "демпинг",
        "прайс", "дешевл", "дорож", "уценк", "спп", "промокод",
    ),

    Intent.MARKETING: (
        "реклам", "продвиж", "продвига", "буст", "ставк", "трафик",
        "показ", "клик", "кампани", "арк", "автореклам", "seo", "сео",
        "ключев", "запрос", "выдач", "ранжир", "позици", "топ",
        "баннер", "медийн", "бюджет",
    ),

    Intent.PHOTO: (
        "фото", "фотк", "фотограф", "инфографик", "изображен", "картинк",
        "видео", "рич", "визуал", "дизайн", "обложк", "превью", "кадр",
        "главн фото", "съемк", "съёмк",
    ),

    Intent.REVIEWS: (
        "отзыв", "рейтинг", "звезд", "звёзд", "негатив", "жалоб",
        "репутац", "оценк покупател", "вопрос покупател", "брак",
        "возврат",
    ),

    Intent.LOGISTICS: (
        "остат", "склад", "поставк", "отгруз", "fbo", "fbs", "фбо", "фбс",
        "логистик", "доставк", "короб", "хранени", "приемк", "приёмк",
        "запас", "оборачива",
    ),

    Intent.SOLUTION_RESEARCH: (
        "где купить", "где найти", "где взять", "где заказать",
        "что купить", "что заказать", "чем заменить",
        "какой лучше", "какая лучше", "сравни", "дешевле", "красивее",
        "сколько стоит", "какой размер", "наполнитель",
        "купить короб", "купить упаков", "красивые дешев", "красивые дешёв",
        "найди упаков", "варианты упаков", "какую из", "ты бы выбрал",
        "какой бы выбрал", "поставщик",
    ),

    Intent.COMPETITOR: (
        "конкурент", "соперник", "у других", "у остальн", "ниш",
        "сравнен", "лидер", "аналог", "чужую карточк",
    ),

    Intent.SELLER_ANALYTICS: (
        "продаж", "продают", "выручк", "заказ", "оборот", "статистик", "аналитик",
        "воронк", "конверси", "ctr", "цтр", "cr", "дрр", "roi", "ромi",
        "прибыл", "убыт", "отчет", "отчёт", "динамик", "метрик",
        "выкуп", "процент выкуп",
        "тренд", "прогноз", "как изменил",
    ),

    Intent.FINANCE: (
        "закуп", "закупк", "закупочн", "себестоим", "маржинальн",
        "безубыточн", "окупаем", "парти", "за кг", "руб/кг",
        "сколько выйдет", "сколько все выйдет", "сколько всё выйдет",
        "сколько будет стоить", "стоит ли закуп", "юнит эконом",
        "unit economics", "цена закупки",
    ),

    Intent.KNOWLEDGE: (
        "что такое", "что значит", "как посчитать", "как рассчитать",
        "из чего складывается", "какие расходы учитывать",
        "определение", "формула марж", "формула ctr", "формула cvr",
    ),

    Intent.WB_POLICY: (
        "штраф wb", "штраф wildberries", "оферт", "пункт оферты",
        "перечень штраф", "за что штраф", "удержан wb",
    ),

    Intent.ACTION_CHECK: (
        "проверить результат", "сработало ли", "неделю назад мы",
        "помоги проверить", "после смены фото",
    ),
}

#: Small talk. Здесь сравнение по вхождению, а не по корню.
SMALL_TALK_WORDS = (
    "привет", "здравств", "хай", "хеллоу", "доброе утро", "добрый день",
    "добрый вечер", "доброй ночи", "приветствую", "здорово", "ку",
    "спасибо", "спс", "благодар", "пасиб", "мерси",
    "пока", "до свидания", "увидимся", "бывай", "спокойной ночи",
    "как дела", "как ты", "что нового", "как жизнь", "как настроение",
    "ты кто", "кто ты", "что умеешь", "что ты умеешь", "твои возможности",
    "ок", "окей", "ok", "понял", "поняла", "ясно", "принял", "угу", "ага",
    "хорошо", "отлично", "супер", "класс", "круто", "здорово", "топ",
    "да", "нет", "давай", "хаха", "лол", ")", "))", "+",
)

#: Отсылки к последнему товару: «а если», «он», «эта карточка».
CONTEXT_REFERENCES = (
    "а если", "если бы", "а что если", "а стоит ли", "стоит ли",
    "у него", "у неё", "у нее", "его", "её", "ее",
    "этот товар", "эта карточка", "по нему", "по этому",
    "тут", "здесь", "в нем", "в нём",
)

#: Максимальная длина сообщения, которое ещё может быть small talk.
SMALL_TALK_MAX_CHARS = 40

_WORD_RE = re.compile(r"[a-zA-Zа-яёА-ЯЁ0-9]+")


def _normalize(text: str) -> str:
    return (text or "").lower().replace("ё", "е").strip()


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _score(text: str, roots: tuple[str, ...]) -> int:
    """Сколько слов сообщения начинаются с указанных корней."""
    words = _words(text)
    hits = 0

    for root in roots:
        root = root.replace("ё", "е")

        if " " in root:
            if root in text:
                hits += 2
            continue

        for word in words:
            if word.startswith(root):
                hits += 1
                break

    return hits


def is_small_talk(text: str) -> bool:
    """
    Приветствие, благодарность, короткое подтверждение.

    Строгая проверка: длинное сообщение или сообщение с деловыми
    словами small talk'ом не считается. «Привет, как поднять продажи?»
    должно уйти в бизнес-ветку, а не в «Привет! Рад тебя видеть».
    """
    text = _normalize(text)

    if not text:
        return False

    if len(text) > SMALL_TALK_MAX_CHARS:
        return False

    # Есть деловые слова — это уже не болтовня.
    for roots in KEYWORDS.values():
        if _score(text, roots):
            return False

    # Вопрос по делу маскируется под короткий: «а фото?»
    words = _words(text)
    if len(words) > 7:
        return False

    for phrase in SMALL_TALK_WORDS:
        if phrase in text:
            return True

    return False


def classify(text: str, *, has_product: bool = False) -> Intent:
    """
    Определить тип сообщения.

    has_product — есть ли в памяти разобранный товар. Это меняет
    трактовку: «а если поднять цену?» при наличии товара — разговор
    про конкретную карточку, без товара — общий вопрос о ценообразовании.
    """

    text = _normalize(text)

    if not text:
        return Intent.GENERAL_QUESTION

    # Finance / funnel — до small talk: короткие деловые фразы
    # («не продаются») не должны ловиться на «да» внутри слова.
    try:
        from backend.ai.finance_planner import is_finance_query
        if is_finance_query(text):
            return Intent.FINANCE
    except Exception:
        pass
    try:
        from backend.ai.dynamic_analytics import is_dynamics_query
        if is_dynamics_query(text):
            return Intent.SELLER_ANALYTICS
    except Exception:
        pass
    try:
        from backend.ai.funnel_economics import is_funnel_query
        if is_funnel_query(text):
            return Intent.SELLER_ANALYTICS
    except Exception:
        pass

    if is_small_talk(text):
        return Intent.SMALL_TALK

    # Считаем совпадения по всем темам.
    scores = {
        intent: _score(text, roots)
        for intent, roots in KEYWORDS.items()
    }

    best_intent = max(scores, key=lambda key: scores[key])
    best_score = scores[best_intent]

    # Тематика не распозналась.
    if best_score == 0:
        # Сообщение ссылается на «него/это/а если» и товар в памяти есть —
        # значит, речь о последнем разобранном товаре.
        if has_product and any(ref in text for ref in CONTEXT_REFERENCES):
            return Intent.PRODUCT_DISCUSSION
        return Intent.GENERAL_QUESTION

    # Тема есть. Если при этом идёт отсылка к товару — это разговор
    # о карточке, но плейбук темы всё равно пригодится (см. Brain).
    return best_intent


def refers_to_product(text: str) -> bool:
    """Ссылается ли сообщение на обсуждаемый товар."""
    text = _normalize(text)
    return any(ref in text for ref in CONTEXT_REFERENCES)
