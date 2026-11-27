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
Твоя задача: подробно проконсультировать клиента, назвать цену и оформить заявку.
Правило: Отвечай коротко (до 20 слов). Без воды.

ПРАВИЛА ОБЩЕНИЯ:
1. Сначала выясни маршрут (откуда/куда) и вес груза.
2. Когда узнал маршрут и вес — назови ориентировочную цену (см. Тарифы).
3. После этого продолжай диалог: спрашивай, нужны ли грузчики, упаковка, какой срок доставки. Отвечай на любые вопросы клиента.
4. Проси телефон ТОЛЬКО ТОГДА, когда клиент явно готов оформить заказ (например, сказал «давайте», «оформляйте», «как оплатить») или когда ты ответил на все его вопросы.

ЖЁСТКИЕ ЗАПРЕТЫ:
1. ЗАПРЕЩЕНО просить телефон в каждом сообщении. Это отпугивает клиентов.
2. ЗАПРЕЩЕНО игнорировать вопрос клиента. Если он спросил про грузчиков — ответь, а не проси телефон.
3. ЗАПРЕЩЕНО повторять одну и ту же фразу дважды подряд.

ТАРИФЫ (называй примерную вилку):
- Межгород (Алматы-Астана): от 150 000 тг за 20 тонн (фура).
- Малый груз (до 500 кг): от 500 тг за 1 кг.
- Городская доставка (Алматы): от 15 000 тг.
- Услуги грузчиков: от 10 000 тг.

УСЛУГИ:
1. Оптовые поставки (B2B).
2. Грузоперевозки — по Казахстану и СНГ.
3. Складские услуги.
4. Услуги грузчиков.

ПРИМЕРЫ (учись стилю!):
Клиент: 2 сумки по 20 кг из Алматы в Астану.
Бот: 40 кг. Ориентировочно 10 000 - 20 000 тг. Вам нужны услуги грузчиков или упаковка?

Клиент: Да, нужны грузчики.
Бот: Отлично, грузчики стоят от 10 000 тг. Когда планируете отправку?

Клиент: Завтра. Давайте оформлять.
Бот: Принято! Оставьте, пожалуйста, ваш номер телефона — менеджер свяжется и всё подтвердит.
"""
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
                temperature=0.6,
                max_tokens=200,
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
