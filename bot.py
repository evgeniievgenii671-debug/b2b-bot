import os
import json
import re
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import ParseMode
from dotenv import load_dotenv

from services.agent import ask_groq
from services.memory import get_memory, add_to_memory, user_memory

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")

ADMIN_IDS_RAW = os.environ.get("ADMIN_IDS", os.environ.get("ADMIN_ID", "0"))
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# ============ ДЕМО-МАТЕРИАЛЫ ============
DEMO_FILE = "demo_data.json"


def load_demo():
    if os.path.exists(DEMO_FILE):
        with open(DEMO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"photos": [], "videos": []}


def save_demo(data):
    with open(DEMO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


demo_data = load_demo()
adding_mode = {"active": False}

# ============ СОСТОЯНИЕ ДИАЛОГОВ ============
user_last_message_time = {}
phone_received = set()
phone_requested = set()

# ============ КЛЮЧЕВЫЕ СЛОВА ============
DEMO_KEYWORDS = [
    "примеры работ", "пример работ", "покажи работы",
    "покажи пример", "скинь пример", "скинь фото",
    "покажи фото", "пришли фото", "портфолио", "кейс",
    "образец", "образцы", "демо", "фотки", "фото работ",
]

# Явное согласие клиента продолжить оформление перевозки.
ORDER_READY_PATTERN = re.compile(
    r"\b(оформляем|оформить|оформляйте|заказываем|заказать|"
    r"согласен|согласна|подтверждаю|бер[её]м|"
    r"готов(ы|а)? заказать|давайте оформим|давайте работать|"
    r"устраивает цена,?\s*давайте)\b",
    re.IGNORECASE,
)

# Признаки того, что ответ бота просит телефон/контакт.
PHONE_REQUEST_PATTERN = re.compile(
    r"(номер\s*(телефона|для связи)?|телефон\s*(для связи)?|"
    r"остав(ьте|ь)\s*(свой\s*)?(номер|телефон)|"
    r"контактн(ый|ые)\s*(номер|данные)|"
    r"как\s*с\s*вами\s*связаться)",
    re.IGNORECASE,
)


def is_demo_request(text):
    t = text.lower()
    return any(kw in t for kw in DEMO_KEYWORDS)


def is_phone_message(text):
    patterns = [
        r"\+7[\s\-\(\)]?\d{3}[\s\-\(\)]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}",
        r"8[\s\-\(\)]?\d{3}[\s\-\(\)]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}",
        r"\d{10,11}",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


# ============ УВЕДОМЛЕНИЯ ============
async def notify_admins(text, photo=None):
    for admin_id in ADMIN_IDS:
        try:
            if photo:
                await bot.send_photo(
                    admin_id, photo=photo, caption=text, parse_mode="HTML"
                )
            else:
                await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Не удалось уведомить админа {admin_id}: {e}")


def is_admin(message):
    return message.from_user.id in ADMIN_IDS


# ============ /start ============
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_memory[user_id] = []
    user_last_message_time[user_id] = 0
    phone_received.discard(user_id)
    phone_requested.discard(user_id)

    text = (
        "👋 Здравствуйте! Я ИИ-менеджер компании <b>B2B Логистика</b>.\n\n"
        "Помогу рассчитать стоимость грузоперевозки, подобрать транспорт или организовать склад.\n\n"
        "<b>Откуда и куда нужно доставить груз?</b> 😊"
    )
    await message.answer(text)


# ============ /checklist ============
@dp.message_handler(commands=["checklist"])
async def cmd_checklist(message: types.Message):
    text = (
        "📋 <b>Чек-лист для заявки</b>\n\n"
        "Чтобы мы сделали точный расчёт, уточните:\n\n"
        "1️⃣ <b>Что нужно</b> — товар, перевозка или склад?\n"
        "2️⃣ <b>Объём</b> — тонны, м³, паллеты?\n"
        "3️⃣ <b>Откуда и куда</b> — города?\n"
        "4️⃣ <b>Сроки</b> — когда нужно?\n"
        "5️⃣ <b>Особые условия</b> — рефрижератор, негабарит?\n\n"
        "💡 <b>Расчёт индивидуальный.</b> Уточните детали перевозки, и мы поможем подготовить заявку."
    )
    await message.answer(text)


# ============ /demo ============
async def send_demo(chat_id):
    if not demo_data["photos"] and not demo_data["videos"]:
        await bot.send_message(chat_id, "📸 Демо-материалы пока не добавлены.")
        return

    await bot.send_message(chat_id, "📸 Вот примеры наших работ:")
    for photo in demo_data["photos"]:
        try:
            await bot.send_photo(
                chat_id, photo=photo["file_id"], caption=photo.get("caption", "")
            )
        except Exception as e:
            logging.error(f"Ошибка фото: {e}")
    for video in demo_data["videos"]:
        try:
            await bot.send_video(
                chat_id, video=video["file_id"], caption=video.get("caption", "")
            )
        except Exception as e:
            logging.error(f"Ошибка видео: {e}")


@dp.message_handler(commands=["demo"])
async def cmd_demo(message: types.Message):
    await send_demo(message.from_user.id)
    await message.answer(
        "📋 Хотите заявку? Отправьте /checklist. Уточним детали и поможем с оформлением 👍"
    )


# ============ АДМИН: демо ============
@dp.message_handler(commands=["add_demo"])
async def cmd_add_demo(message: types.Message):
    if not is_admin(message):
        return
    adding_mode["active"] = True
    await message.answer(
        "✅ Режим добавления включён.\n\n"
        "Отправляй фото и видео (можно с подписью).\n"
        "Когда закончишь — /stop_demo"
    )


@dp.message_handler(commands=["stop_demo"])
async def cmd_stop_demo(message: types.Message):
    if not is_admin(message):
        return
    adding_mode["active"] = False
    await message.answer(
        f"⏹ Выключено.\n📸 Фото: {len(demo_data['photos'])}\n🎥 Видео: {len(demo_data['videos'])}"
    )


@dp.message_handler(commands=["show_list"])
async def cmd_show_list(message: types.Message):
    if not is_admin(message):
        return
    await message.answer(
        f"📊 Фото: {len(demo_data['photos'])}\n🎥 Видео: {len(demo_data['videos'])}"
    )


@dp.message_handler(commands=["clear_demo"])
async def cmd_clear_demo(message: types.Message):
    if not is_admin(message):
        return
    demo_data["photos"] = []
    demo_data["videos"] = []
    save_demo(demo_data)
    await message.answer("🗑 Демо-материалы удалены.")


# ============ АДМИН: прочее ============
@dp.message_handler(commands=["clear"])
async def cmd_clear(message: types.Message):
    if not is_admin(message):
        return
    user_memory.clear()
    phone_received.clear()
    phone_requested.clear()
    await message.answer("🧹 Память очищена!")


@dp.message_handler(commands=["stats"])
async def cmd_stats(message: types.Message):
    if not is_admin(message):
        return
    await message.answer(f"📊 Активных диалогов: {len(user_memory)}")


@dp.message_handler(commands=["help"])
async def cmd_help(message: types.Message):
    text = (
        "🤖 <b>Команды:</b>\n\n"
        "/start, /checklist, /demo, /help\n\n"
        "<b>Админ:</b>\n"
        "/add_demo, /stop_demo, /show_list, /clear_demo\n"
        "/clear, /stats"
    )
    await message.answer(text)


# ============ ФОТО (админ + клиент) ============
@dp.message_handler(content_types=["photo"])
async def handle_photo_unified(message: types.Message):
    if adding_mode["active"] and is_admin(message):
        file_id = message.photo[-1].file_id
        demo_data["photos"].append(
            {"file_id": file_id, "caption": message.caption or ""}
        )
        save_demo(demo_data)
        await message.answer(f"✅ Фото ({len(demo_data['photos'])} шт.)")
        return

    user_id = message.from_user.id
    username = message.from_user.username or "—"
    full_name = message.from_user.full_name
    caption = message.caption or "(без подписи)"
    photo_id = message.photo[-1].file_id

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                admin_id,
                photo=photo_id,
                caption=(
                    f"📷 <b>Клиент прислал фото</b>\n\n"
                    f"👤 {full_name}\n"
                    f"📱 @{username}\n"
                    f"🆔 <code>{user_id}</code>\n\n"
                    f"💬 {caption}"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logging.error(f"Ошибка пересылки фото: {e}")

    await message.answer("📷 Спасибо за фото! Передал менеджеру.")


# ============ ВИДЕО (админ + клиент) ============
@dp.message_handler(content_types=["video"])
async def handle_video_unified(message: types.Message):
    if adding_mode["active"] and is_admin(message):
        file_id = message.video.file_id
        demo_data["videos"].append(
            {"file_id": file_id, "caption": message.caption or ""}
        )
        save_demo(demo_data)
        await message.answer(f"✅ Видео ({len(demo_data['videos'])} шт.)")
        return

    user_id = message.from_user.id
    username = message.from_user.username or "—"
    full_name = message.from_user.full_name
    caption = message.caption or "(без подписи)"
    video_id = message.video.file_id

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_video(
                admin_id,
                video=video_id,
                caption=(
                    f"🎥 <b>Клиент прислал видео</b>\n\n"
                    f"👤 {full_name}\n"
                    f"📱 @{username}\n"
                    f"🆔 <code>{user_id}</code>\n\n"
                    f"💬 {caption}"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logging.error(f"Ошибка пересылки видео: {e}")

    await message.answer("🎥 Спасибо за видео! Передал менеджеру.")


# ============ ОБРАБОТКА ТЕКСТА ============
@dp.message_handler(content_types=["text"])
async def handle_message(message: types.Message):
    if message.text.startswith("/"):
        return

    user_id = message.from_user.id
    user_text = message.text
    username = message.from_user.username or "—"
    full_name = message.from_user.full_name

    # --- АНТИ-ФЛУД ---
    current_time = time.time()
    last_time = user_last_message_time.get(user_id, 0)

    if current_time - last_time < 1.0:
        await message.answer(
            "⚠️ Пожалуйста, не так быстро. Я не успеваю обрабатывать запросы. Подождите пару секунд."
        )
        return

    user_last_message_time[user_id] = current_time
    add_to_memory(user_id, "user", user_text)

    phone_detected = is_phone_message(user_text)
    if phone_detected:
        phone_received.add(user_id)
        phone_requested.discard(user_id)
        await notify_admins(
            f"🔥 <b>НОВАЯ ЗАЯВКА!</b>\n\n"
            f"👤 Клиент: {full_name}\n"
            f"📱 @{username}\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            f"💬 Написал: {user_text}\n\n"
            f"⚡ <b>Свяжитесь с клиентом срочно!</b>"
        )
        await message.answer(
            "Спасибо! Передал номер менеджеру. С вами свяжутся."
        )
        return

    if is_demo_request(user_text):
        await send_demo(user_id)
        add_to_memory(user_id, "assistant", "[Показал примеры работ]")
        await message.answer(
            "📋 Понравилось? Давайте подберём под ваш запрос.\n\n"
            "Что именно нужно — товар, перевозка или склад?"
        )
        await notify_admins(
            f"💬 <b>Новое сообщение</b>\n"
            f"👤 {full_name} (@{username})\n"
            f"🆔 <code>{user_id}</code>\n"
            f"💬 {user_text}\n\n"
            f"→ Бот показал демо-материалы."
        )
        return

    history = get_memory(user_id)[-10:]

    # Смотрим последние сообщения клиента: согласился ли он явно перейти к оформлению.
    recent_user_text = " ".join(
        item.get("content", "")
        for item in history
        if item.get("role") == "user"
    )
    client_ready_to_order = bool(ORDER_READY_PATTERN.search(recent_user_text))

    logistics_prompt = {
        "role": "system",
        "content": (
            "Ты — профессиональный логист компании B2B Логистика. Общайся вежливо, кратко и по делу.\n"
            "Твоя задача: проконсультировать клиента по грузоперевозке, назвать цену и собрать заявку.\n"
            "ВАЖНО: Если клиент спрашивает цену, называй ориентировочную стоимость: "
            "Алматы-Астана от 150 000 тг за 20 тонн; если груз до 500 кг — от 500 тг/кг.\n"
            "СТРОГОЕ ПРАВИЛО ПО ТЕЛЕФОНУ:\n"
            "Не проси номер телефона в начале или середине консультации и сразу после озвучивания цены.\n"
            "Сначала выясни маршрут, груз и вес; ответь на вопросы о стоимости, сроках, машине и условиях доставки.\n"
            "Проси телефон только в конце, когда клиент явно согласился оформить перевозку или продолжить оформление заявки.\n"
            "Если клиент ещё думает, сравнивает цены или задаёт вопросы — продолжай консультацию и не проси телефон.\n"
            "Если номер уже получен, повторно его не спрашивай.\n"
            "Если клиент не знает объём — не требуй его, считай по весу.\n"
            "Не повторяй просьбу о телефоне несколько раз."
        ),
    }

    state_prompt = {
        "role": "system",
        "content": (
            "СОСТОЯНИЕ ЗАЯВКИ:\n"
            + (
                "Клиент уже прислал номер телефона. Больше не спрашивай телефон."
                if user_id in phone_received
                else (
                    "Ты уже просил номер телефона. Повторно не проси."
                    if user_id in phone_requested
                    else (
                        "Клиент явно согласился перейти к оформлению. Сейчас можно один раз попросить номер телефона для связи с менеджером."
                        if client_ready_to_order
                        else "Клиент ещё не выразил явного согласия на оформление. Телефон сейчас НЕ проси. Продолжай консультацию; при необходимости спроси, готов ли клиент перейти к оформлению, но не проси номер."
                    )
                )
            ),
        ),
    }

    history.insert(0, state_prompt)
    history.insert(0, logistics_prompt)

    ai_text = await ask_groq(history)
    ai_text = ai_text.strip()

    # Дополнительная защита: просьба о телефоне допускается один раз и только после явного согласия.
    if PHONE_REQUEST_PATTERN.search(ai_text):
        if user_id in phone_received or user_id in phone_requested:
            ai_text = (
                "Я уже получил ваши контактные данные. Давайте продолжим по условиям перевозки."
                if user_id in phone_received
                else "Давайте продолжим. Если появятся дополнительные вопросы по перевозке, я помогу."
            )
        elif not client_ready_to_order:
            ai_text = (
                "Давайте сначала уточним все детали перевозки. "
                "Вас устраивают предложенные стоимость и условия, и готовы ли вы перейти к оформлению?"
            )
        else:
            phone_requested.add(user_id)

    # ЗАЩИТА ОТ ПОВТОРОВ
    last_bot_messages = [
        item["content"]
        for item in history[-5:]
        if item.get("role") == "assistant"
    ]
    if last_bot_messages and ai_text == last_bot_messages[-1].strip():
        logging.warning(f"Бот повторился: {ai_text}")
        ai_text = (
            "Понял вас. Уточните, пожалуйста, откуда и куда нужно доставить груз?"
        )

    add_to_memory(user_id, "assistant", ai_text)
    await message.answer(ai_text)

    await notify_admins(
        f"💬 <b>Новое сообщение</b>\n"
        f"👤 {full_name} (@{username})\n"
        f"🆔 <code>{user_id}</code>\n"
        f"💬 {user_text}\n\n"
        f"🤖 {ai_text[:250]}..."
    )


# ============ HEALTH-CHECK ============
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"B2B bot is running!")

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logging.info(f"Health server on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
