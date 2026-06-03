import os
import asyncio
import hashlib
import streamlit as st
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from yt_dlp import YoutubeDL

# Считываем токен
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Ошибка: Переменная BOT_TOKEN не задана!")

# Автоматически определяем адрес приложения на Streamlit для вебхука
# Он формируется на основе твоего репозитория acfg
WEBHOOK_HOST = "https://streamlit.app"
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ЖЕЛЕЗНЫЙ ОБХОД БЛОКИРОВКИ YOUTUBE: Меняем заголовки и убираем тяжелый flat-поиск
SEARCH_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch5',
    'quiet': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
    }
}

DOWNLOAD_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'nocheckcertificate': True,
}

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "🎵 **Привет! Я твой музыкальный бот.**\n\n"
        "• Отправь мне название трека прямо сюда, чтобы выбрать из списка.\n"
        "• Или используй меня в любом чате (инлайн), написав: `@юзернейм_бота название`"
    )

# --- РЕЖИМ 1: Поиск списка 5 песен через чат ---
@dp.message()
async def search_and_show_list(message: types.Message):
    query = message.text
    status_msg = await message.answer("🔍 Ищу варианты...")

    try:
        loop = asyncio.get_event_loop()
        with YoutubeDL(SEARCH_OPTIONS) as ydl:
            # Используем полный extract для обхода пустых ответов блокировки
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
            
        if info and 'entries' in info and len(info['entries']) > 0:
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
            text = "🎶 **Вот что я нашёл. Выберите трек:**\n\n"
            
            # Фильтруем пустые результаты, если YouTube что-то заблочил
            valid_entries = [e for e in info['entries'] if e is not None]
            
            for idx, entry in enumerate(valid_entries[:5], 1):
                title = entry.get('title', 'Без названия')
                url = entry.get('url', f"https://www.youtube.com/watch?v={entry.get('id')}")
                
                text += f"{idx}. **{title}**\n"
                
                url_hash = hashlib.md5(url.encode()).hexdigest()
                dp["url_cache_" + url_hash] = url
                
                keyboard.inline_keyboard.append([
                    types.InlineKeyboardButton(text=f"📥 Скачать {idx}", callback_data=f"dl_{url_hash}")
                ])
                
            await status_msg.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await status_msg.edit_text("❌ Ничего не найдено. Попробуйте изменить запрос.")
    except Exception as e:
        await status_msg.edit_text("⚠️ Ошибка поиска треков.")
        print(f"Ошибка поиска: {e}")

# Скачивание файла по нажатию на кнопку
@dp.callback_query(F.data.startswith("dl_"))
async def download_selected_track(callback: types.CallbackQuery):
    url_hash = callback.data.split("_")
    url = dp.get("url_cache_" + url_hash)
    
    if not url:
        await callback.answer("❌ Ссылка устарела. Сделайте поиск заново.", show_alert=True)
        return
        
    await callback.message.edit_text("📥 Скачиваю MP3, подождите...")
    
    try:
        loop = asyncio.get_event_loop()
        with YoutubeDL(DOWNLOAD_OPTIONS) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            
        title = info.get('title', 'music_track')
        file_path = f"{DOWNLOAD_DIR}/{title}.mp3"
        
        if os.path.exists(file_path):
            await callback.message.edit_text("⚡ Отправляю файл в Telegram...")
            audio_file = types.FSInputFile(file_path)
            
            partner_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="🎧 Слушать без интернета (90 дней бесплатно)", 
                        url="https://ya.cc"
                    )
                ]
            ])
            
            await callback.message.answer_audio(
                audio=audio_file, 
                title=title, 
                caption="Готово! Приятного прослушивания 🎧",
                reply_markup=partner_keyboard
            )
            os.remove(file_path)
            await callback.message.delete()
        else:
            await callback.message.edit_text("❌ Ошибка: файл потерялся при конвертации.")
    except Exception as e:
        await callback.message.edit_text("⚠️ Не удалось скачать этот трек.")
        print(f"Ошибка скачивания: {e}")

# --- РЕЖИМ 2: Инлайн-режим ---
@dp.inline_query()
async def inline_search(inline_query: types.InlineQuery):
    query = inline_query.query.strip()
    if not query:
        return

    try:
        loop = asyncio.get_event_loop()
        with YoutubeDL(SEARCH_OPTIONS) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
            
        results = []
        if info and 'entries' in info:
            valid_entries = [e for e in info['entries'] if e is not None]
            for idx, entry in enumerate(valid_entries[:5]):
                title = entry.get('title', 'Без названия')
                url = entry.get('url', f"https://www.youtube.com/watch?v={entry.get('id')}")
                
                input_message_content = types.InputTextMessageContent(
                    message_text=f"🎵 **Найден трек:** [{title}]({url})",
                    parse_mode="Markdown"
                )
                
                results.append(
                    types.InlineQueryResultArticle(
                        id=f"inline_{idx}_{hashlib.md5(url.encode()).hexdigest()}",
                        title=title,
                        input_message_content=input_message_content
                    )
                )
                
        await inline_query.answer(results, cache_time=1)
    except Exception as e:
        print(f"Ошибка инлайн-поиска: {e}")

# Установка вебхука при старте сервера
async def on_startup(bot: Bot) -> None:
    print(f"Установка вебхука на адрес: {WEBHOOK_URL}")
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)

def main():
    st.title("🎵 Музыкальный Бот запущен!")
    st.write("Сервер вебхуков успешно работает.")
    
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    
    dp.startup.register(on_startup)
    setup_application(app, dp, bot=bot)
    
    # Запускаем локальный веб-сервер, который будет ловить пакеты от Telegram
    web.run_app(app, host="0.0.0.0", port=8501)

if __name__ == "__main__":
    main()
