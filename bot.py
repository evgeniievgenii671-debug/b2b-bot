import os
import json
import re
import logging
import threading
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

# ============ КЛЮЧЕВЫЕ СЛОВА ============
DEMO_KEYWORDS = [
    "примеры работ", "пример работ", "покажи работы",
    "покажи пример", "скинь пример", "скинь фото",
    "покажи фото", "пришли фото", "портфолио", "кейс",
    "образец", "образцы", "демо", "фотки", "фото работ",
]

def is_demo_request(text):
    t = text.lower()
    return any(kw in t for kw in DEMO_KEYWORDS)


def is_phone_message(text):
    patterns = [
        r"\+7[\s\-\(\)]?\d{3}[\s\-\(\)]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}",
        r"8[\s\-\(\)]?\d{3}[\s\-\(\)]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}",
        r"\d{10,11}",
    ]
    for p in patterns:
        if re.search(p, text):
            return True
    return False


# ============ УВЕДОМЛЕНИЯ ============
async def notify_admins(text, photo=None):
    for admin_id in ADMIN_IDS:
        try:
            if photo:
                await bot.send_photo(admin_id, photo=photo, caption=text, parse_mode="HTML")
            else:
                await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Не удалось уведомить админа {admin_id}: {e}")


def is_admin(message):
    return message.from_user.id in ADMIN_IDS


# ============ /start ============
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    user_memory[message.from_user.id] = []
   text = (
    "👋 Здравствуйте! Я ИИ-ассистент компании <b>B2B Логистика</b>.\n\n"
    "Помогу с оптовыми поставками, грузоперевозками и складом.\n\n"
    "Как я могу к вам обращаться? 😊"
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
        "💡 <b>Расчёт индивидуальный.</b> Оставьте номер 👇"
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
            await bot.send_photo(chat_id, photo=photo["file_id"], caption=photo.get("caption", ""))
        except Exception as e:
            logging.error(f"Ошибка фото: {e}")
    for video in demo_data["videos"]:
        try:
            await bot.send_video(chat_id, video=video["file_id"], caption=video.get("caption", ""))
        except Exception as e:
            logging.error(f"Ошибка видео: {e}")


@dp.message_handler(commands=["demo"])
async def cmd_demo(message: types.Message):
    await send_demo(message.from_user.id)
    await message.answer(
        "📋 Хотите заявку? Отправьте /checklist.\n"
        "Или оставьте номер — менеджер свяжется 👍"
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
    await message.answer(f"📊 Фото: {len(demo_data['photos'])}\n🎥 Видео: {len(demo_data['videos'])}")


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
        demo_data["photos"].append({"file_id": file_id, "caption": message.caption or ""})
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
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Ошибка пересылки фото: {e}")

    await message.answer("📷 Спасибо за фото! Передал менеджеру.")


# ============ ВИДЕО (админ + клиент) ============
@dp.message_handler(content_types=["video"])
async def handle_video_unified(message: types.Message):
    if adding_mode["active"] and is_admin(message):
        file_id = message.video.file_id
        demo_data["videos"].append({"file_id": file_id, "caption": message.caption or ""})
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
                parse_mode="HTML"
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

    add_to_memory(user_id, "user", user_text)

    phone_detected = is_phone_message(user_text)

    if phone_detected:
        await notify_admins(
            f"🔥 <b>НОВАЯ ЗАЯВКА!</b>\n\n"
            f"👤 Клиент: {full_name}\n"
            f"📱 @{username}\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            f"💬 Написал: {user_text}\n\n"
            f"⚡ <b>Свяжитесь с клиентом срочно!</b>"
        )

    if is_demo_request(user_text):
        await send_demo(user_id)
        add_to_memory(user_id, "assistant", "[Показал примеры работ]")
        await message.answer(
            "📋 Понравилось? Давайте подберём под ваш запрос.\n\n"
            "Что именно нужно — товар, перевозка или склад?"
        )
        if not phone_detected:
            await notify_admins(
                f"💬 <b>Новое сообщение</b>\n"
                f"👤 {full_name} (@{username})\n"
                f"🆔 <code>{user_id}</code>\n"
                f"💬 {user_text}\n\n"
                f"→ Бот показал демо-материалы."
            )
        return

    history = get_memory(user_id)[-10:]
    ai_text = await ask_groq(history)
    add_to_memory(user_id, "assistant", ai_text)

    await message.answer(ai_text)

    if not phone_detected:
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
