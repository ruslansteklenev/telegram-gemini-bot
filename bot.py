import asyncio
import logging
import os
from io import BytesIO
from asyncio import to_thread
from tempfile import NamedTemporaryFile

# НОВОЕ: Импорты для веб-сервера
from flask import Flask
from threading import Thread

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.utils.markdown import hbold
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from PIL import Image

import google.generativeai as genai

# Загружаем переменные окружения из файла .env
load_dotenv()

# Получаем токены из переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Проверяем, что токены были загружены
if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Необходимо задать TELEGRAM_BOT_TOKEN и GEMINI_API_KEY в .env файле")

# --- ПРОМТЫ ДЛЯ МОДЕЛИ ---
SYSTEM_PROMPT = "Ты — умный и дружелюбный ассистент в Telegram-чате. Твоя задача — помогать пользователям, отвечая на их вопросы. Отвечай коротко и по делу. Используй эмодзи, где это уместно, чтобы сделать общение более живым."
AUDIO_PROMPT = "Ты — умный и дружелюбный ассистент в Telegram-чате. Твоя задача — помогать пользователям, отвечая на их вопросы. Отвечай коротко и по делу. Используй эмодзи, где это уместно, чтобы сделать общение более живым."
IMAGE_PROMPT = "Ты — эксперт по анализу изображений. Внимательно изучи картинку и дай ей подробное, но лаконичное описание.если теребуется преводи текст с изображения на руский язык,то переведи его. Опиши, что на ней происходит, какие объекты присутствуют и какая общая атмосфера. 🎨"

# Конфигурируем Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# Настройки модели Gemini
generation_config = {"temperature": 0.7}
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

# Создаем модель Gemini
# Примечание: на момент написания кода модель "gemini-2.5-flash" может не существовать.
# Если будут ошибки, попробуйте "gemini-1.5-flash".
model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config=generation_config,
    safety_settings=safety_settings,
    system_instruction=SYSTEM_PROMPT,
)

user_chat_sessions = {}

default_properties = DefaultBotProperties(parse_mode=ParseMode.HTML)
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=default_properties)
dp = Dispatcher()


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    await message.answer(f"Привет, {hbold(message.from_user.full_name)}! Я — твой умный ассистент. 🤖\n\nМожешь задать мне вопрос текстом, отправить голосовое сообщение или картинку для анализа.")

@dp.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    help_text = (
        "Я — ассистент на базе нейросети Gemini.\n\n"
        "– Я отвечаю на твои **текстовые сообщения**, помня контекст нашего диалога.\n"
        "– Я **расшифровываю голосовые сообщения** и излагаю их суть.\n"
        "– Я **анализирую изображения**, которые ты мне пришлешь. Можешь даже задать вопрос к картинке в подписи!\n\n"
        f"{hbold('Доступные команды:')}\n"
        "/start - Начать диалог\n"
        "/help - Показать это сообщение\n"
        "/new - Сбросить историю текстового диалога"
    )
    await message.answer(help_text)

@dp.message(Command("new"))
async def command_new_handler(message: Message) -> None:
    user_id = message.from_user.id
    if user_id in user_chat_sessions:
        del user_chat_sessions[user_id]
        await message.answer("Контекст текстового диалога сброшен. Начинаем с чистого листа! 📝")
    else:
        await message.answer("Диалог и так пуст. Можете задавать свой вопрос! 😊")


@dp.message(F.photo)
async def image_handler(message: types.Message):
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        downloaded_file = BytesIO()
        await bot.download_file(file_info.file_path, destination=downloaded_file)
        
        image = Image.open(downloaded_file)
        user_prompt = message.caption if message.caption else IMAGE_PROMPT
        contents = [user_prompt, image]

        logging.info("Отправка изображения в Gemini для анализа...")
        response = await model.generate_content_async(contents)
        logging.info("Ответ от Gemini получен.")
        
        await message.reply(response.text)

    except Exception as e:
        logging.error(f"Ошибка при обработке изображения: {e}")
        await message.reply("Произошла ошибка при обработке вашего изображения. 🖼️ Попробуйте еще раз.")


@dp.message(F.voice)
async def voice_message_handler(message: types.Message):
    uploaded_file = None
    temp_file_path = None
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        voice = message.voice
        file_info = await bot.get_file(voice.file_id)
        with NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
            await bot.download_file(file_info.file_path, destination=temp_file)
            temp_file_path = temp_file.name
        logging.info(f"Голосовое сообщение сохранено во временный файл: {temp_file_path}")
        uploaded_file = await to_thread(
            genai.upload_file, path=temp_file_path, mime_type=voice.mime_type or "audio/ogg"
        )
        logging.info(f"Файл успешно загружен. Имя в Gemini: {uploaded_file.name}")
        response = await model.generate_content_async([AUDIO_PROMPT, uploaded_file])
        await message.reply(response.text)
    except Exception as e:
        logging.error(f"Ошибка при обработке голосового сообщения: {e}")
        await message.reply("Произошла ошибка при обработке вашего голосового сообщения. 😥 Попробуйте еще раз.")
    finally:
        if uploaded_file:
            try:
                logging.info(f"Удаление файла {uploaded_file.name} из Gemini...")
                await to_thread(genai.delete_file, name=uploaded_file.name)
            except Exception as e:
                logging.error(f"Не удалось удалить файл {uploaded_file.name} из Gemini: {e}")
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
            logging.info(f"Временный файл {temp_file_path} удален.")


@dp.message(F.text)
async def gemini_text_handler(message: types.Message) -> None:
    user_id = message.from_user.id
    if user_id not in user_chat_sessions:
        user_chat_sessions[user_id] = model.start_chat()
    chat_session = user_chat_sessions[user_id]
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        response = await chat_session.send_message_async(message.text)
        await message.answer(response.text)
    except Exception as e:
        logging.error(f"Ошибка при обработке текстового сообщения от {user_id}: {e}")
        await message.answer("Ой, что-то пошло не так... технические шоколадки. 🍫 Попробуйте еще раз позже.")


# НОВОЕ: Веб-сервер для поддержания активности на Render
app = Flask(__name__)

@app.route('/')
def index():
    return "I am alive!"

def run_web_server():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
# ----------------------------------------------------


async def main() -> None:
    # НОВОЕ: Запускаем веб-сервер в отдельном потоке
    Thread(target=run_web_server).start()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")