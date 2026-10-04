import os
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("GROQ_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")

client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL
)

SYSTEM_PROMPT = """Роль: Вежливый ИИ-эксперт компании «B2B Логистика».
Цель: за 3 шага узнать суть задачи, город и взять телефон для точного расчёта.
Правило: Отвечай строго 1 коротким предложением (до 15 слов). Без воды.
Цен не называй — расчёт индивидуальный.
На флуд: "Оставьте телефон, менеджер свяжется и сделает расчёт."

УСЛУГИ:
1. Оптовые поставки (B2B) — товары под заказ, крупный опт.
2. Грузоперевозки — доставка по Казахстану и СНГ.
3. Складские услуги — хранение, комплектация.

ФАКТЫ:
- Работаем с 2015 года
- Свой автопарк (фуры, рефрижераторы)
- Отсрочка платежа для постоянных клиентов

ВАЖНО ПРО ИМЯ:
- Как только клиент назвал имя — ОБЯЗАТЕЛЬНО используй его в СЛЕДУЮЩЕМ ответе и далее.
- Пример: «Спасибо, Евгений! Что нужно — товар, перевозка или склад?»

ПОРЯДОК (3 шага):
1. Поздоровайся, спроси имя.
2. Что нужно — товар / перевозка / склад? Откуда и куда?
3. Оставьте телефон — менеджер сделает расчёт.

ПРИМЕРЫ:
Клиент: Привет
Бот: Здравствуйте! Как я могу к вам обращаться?

Клиент: Евгений
Бот: Приятно познакомиться, Евгений! Что нужно — товар, перевозка или склад?

Клиент: Перевозка из Алматы в Астану
Бот: Понял, Евгений! Какой груз и объём?

Клиент: 20 тонн товара
Бот: Отлично, Евгений! Оставьте телефон — менеджер рассчитает.

Клиент: Сколько стоит?
Бот: Расчёт индивидуальный, Евгений. Оставьте телефон — менеджер свяжется.

Клиент: Не важно
Бот: Хорошо! Что именно вас интересует?
"""

BAD_MODELS = [
    "whisper", "tts", "orpheus", "guard",
    "arabic", "saudi", "allam",
    "llama-3.2-1b", "llama-3.2-3b",
]

PRIORITY = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "moonshotai/kimi-k2",
]


async def get_good_models() -> list:
    try:
        response = await client.models.list()
        all_models = [m.id for m in response.data]
        good = [m for m in all_models if not any(bad in m.lower() for bad in BAD_MODELS)]
        logger.info(f"Подходящих моделей: {good}")
        return good
    except Exception as e:
        logger.error(f"Ошибка получения моделей: {e}")
        return []


def sort_by_priority(models: list) -> list:
    result = []
    for pref in PRIORITY:
        for m in models:
            if pref in m.lower() and m not in result:
                result.append(m)
    for m in models:
        if m not in result:
            result.append(m)
    return result


async def ask_groq(history):
    if not OPENAI_API_KEY:
        logger.error("API-ключ не задан!")
        return "Ошибка конфигурации."

    models = await get_good_models()
    if not models:
        return "Проблема с AI. Попробуйте позже 🙏"

    models_to_try = sort_by_priority(models)[:5]
    logger.info(f"Порядок попыток: {models_to_try}")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-10:]

    last_error = None
    for model in models_to_try:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=300,
            )
            text = response.choices[0].message.content

            if not text or not text.strip():
                logger.warning(f"{model}: пустой ответ")
                continue

            text = text.strip()
            if text and text[-1] not in ".!?…":
                text += "."

            logger.info(f"✅ Ответила: {model}")
            return text

        except Exception as e:
            last_error = str(e)
            logger.warning(f"{model} не сработала: {e}")
            continue

    logger.error(f"Все упали. Последняя: {last_error}")
    return "Извините, сейчас не могу ответить 🙏"
