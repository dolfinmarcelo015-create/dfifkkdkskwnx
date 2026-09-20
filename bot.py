import asyncio
import os
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web
import google.generativeai as genai

# === НАСТРОЙКИ (берутся из Environment Variables на Render) ===
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL_NAME = "gemini-1.5-flash"
PORT = int(os.environ.get("PORT", 8080))

if not BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Не заданы BOT_TOKEN или GEMINI_API_KEY")

# === ИНИЦИАЛИЗАЦИЯ ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(MODEL_NAME)

DB_PATH = "chat_history.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_id INTEGER,
            username TEXT,
            text TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS prompts (
            user_id INTEGER PRIMARY KEY,
            prompt TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_message(chat_id, user_id, username, text):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO messages (chat_id, user_id, username, text) VALUES (?, ?, ?, ?)",
        (chat_id, user_id, username, text)
    )
    conn.commit()
    conn.close()

def get_recent_messages(chat_id, limit=100):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT username, text FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
        (chat_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return list(reversed(rows))

def get_user_prompt(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT prompt FROM prompts WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_user_prompt(user_id, prompt):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO prompts (user_id, prompt) VALUES (?, ?)", (user_id, prompt))
    conn.commit()
    conn.close()

# === ОБРАБОТЧИКИ ===

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот-аналитик чата.\n\n"
        "Команды:\n"
        "/analyze — проанализировать последние 100 сообщений\n"
        "/prompt <текст> — задать свой промпт для анализа\n"
        "/myprompt — показать текущий промпт\n"
        "/reset — сбросить промпт\n\n"
        "Добавь меня в групповой чат и дай права читать сообщения."
    )

@dp.message(Command("prompt"))
async def cmd_prompt(message: Message):
    text = message.text.replace("/prompt", "", 1).strip()
    if not text:
        await message.answer("Напиши так: /prompt Выдели только важные сообщения")
        return
    set_user_prompt(message.from_user.id, text)
    await message.answer(f"✅ Твой промпт сохранён:\n\n{text}")

@dp.message(Command("myprompt"))
async def cmd_myprompt(message: Message):
    prompt = get_user_prompt(message.from_user.id)
    if prompt:
        await message.answer(f"📝 Твой текущий промпт:\n\n{prompt}")
    else:
        await message.answer("У тебя нет кастомного промпта. Используй /prompt чтобы задать.")

@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    set_user_prompt(message.from_user.id, "")
    await message.answer("✅ Промпт сброшен. Будет использоваться стандартный анализ.")

@dp.message(Command("analyze"))
async def cmd_analyze(message: Message):
    if message.chat.type == "private":
        await message.answer("Эта команда работает только в групповых чатах.")
        return

    await message.answer("⏳ Анализирую последние сообщения...")

    messages = get_recent_messages(message.chat.id, limit=100)
    if not messages:
        await message.answer("Пока нет сообщений для анализа.")
        return

    context = "\n".join([f"{username}: {text}" for username, text in messages])

    user_prompt = get_user_prompt(message.from_user.id)
    if not user_prompt:
        user_prompt = "Кратко опиши, о чём говорили в чате, выдели главные темы и выводы."

    full_prompt = f"{user_prompt}\n\nВот история сообщений:\n{context}"

    try:
        response = await asyncio.to_thread(model.generate_content, full_prompt)
        answer = response.text
        if len(answer) > 4000:
            answer = answer[:4000] + "..."
        await message.answer(f"📊 **Анализ:**\n\n{answer}", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка при анализе: {str(e)}")

@dp.message(F.text & ~F.text.startswith("/"))
async def save_all_messages(message: Message):
    if message.chat.type in ["group", "supergroup"]:
        save_message(
            message.chat.id,
            message.from_user.id,
            message.from_user.username or message.from_user.first_name,
            message.text
        )

# === ВЕБ-СЕРВЕР ДЛЯ RENDER (health-check) ===
async def health(request):
    return web.Response(text="OK")

async def main():
    init_db()

    # Поднимаем веб-сервер на PORT, который выдал Render
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Health-сервер запущен на порту {PORT}")

    # Параллельно запускаем бота
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())