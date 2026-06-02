import os
import asyncio
import hashlib
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from yt_dlp import YoutubeDL

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Ошибка: Переменная BOT_TOKEN не задана в настройках Space!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Настройки для поиска 5 треков БЕЗ скачивания
SEARCH_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'scsearch5',  # Ищем ТОП-5 результатов в SoundCloud
    'quiet': True,
    'extract_flat': True,           # Быстрое извлечение только метаданных (ссылка, название)
}

# Настройки для скачивания конкретного трека по URL
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
}

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "🎵 **Привет! Я музыкальный бот.**\n\n"
        "1. Отправь мне название трека прямо сюда, чтобы выбрать из списка.\n"
        "2. Или используй меня в любом чате, написав: `@имя_вашего_бота название`"
    )

# --- 1. Обычный режим: Поиск списка песен через чат ---
@dp.message()
async def search_and_show_list(message: types.Message):
    query = message.text
    status_msg = await message.answer("🔍 Ищу варианты в SoundCloud...")

    try:
        loop = asyncio.get_event_loop()
        with YoutubeDL(SEARCH_OPTIONS) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
            
        if 'entries' in info and len(info['entries']) > 0:
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
            text = "🎶 **Вот что я нашёл. Выберите трек для скачивания:**\n\n"
            
            for idx, entry in enumerate(info['entries'][:5], 1):
                title = entry.get('title', 'Без названия')
                url = entry.get('url')
                uploader = entry.get('uploader', 'SoundCloud')
                
                text += f"{idx}. **{title}** — __{uploader}__\n"
                
                # Создаем хэш от URL, так как Telegram разрешает callback_data длиной до 64 байт
                url_hash = hashlib.md5(url.encode()).hexdigest()
                # Сохраняем связь хэш -> URL во временную память бота (очень простой кэш)
                dp["url_cache_" + url_hash] = url
                
                keyboard.inline_keyboard.append([
                    types.InlineKeyboardButton(text=f"📥 Скачать {idx}", callback_data=f"dl_{url_hash}")
                ])
                
            await status_msg.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await status_msg.edit_text("❌ Ничего не найдено по этому запросу.")
    except Exception as e:
        await status_msg.edit_text("⚠️ Ошибка поиска треков.")
        print(f"Ошибка поиска: {e}")

# Обработка нажатия на кнопку "Скачать" из списка
@dp.callback_query(F.data.startswith("dl_"))
async def download_selected_track(callback: types.CallbackQuery):
    url_hash = callback.data.split("_")[1]
    url = dp.get("url_cache_" + url_hash)
    
    if not url:
        await callback.answer("❌ Ссылка устарела. Повторите поиск.", show_alert=True)
        return
        
    await callback.message.edit_text("📥 Начинаю скачивание файла, подождите...")
    
    try:
        loop = asyncio.get_event_loop()
        # Скачиваем конкретный трек по прямой ссылке
        with YoutubeDL(DOWNLOAD_OPTIONS) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            
        title = info.get('title', 'music_track')
        file_path = f"{DOWNLOAD_DIR}/{title}.mp3"
        
        if os.path.exists(file_path):
            await callback.message.edit_text("⚡ Файл готов! Отправляю...")
            audio_file = types.FSInputFile(file_path)
            await callback.message.answer_audio(audio=audio_file, title=title, caption="Ваш трек готов! 🎧")
            os.remove(file_path)
            await callback.message.delete()
        else:
            await callback.message.edit_text("❌ Ошибка: файл не найден на сервере.")
    except Exception as e:
        await callback.message.edit_text("⚠️ Не удалось скачать этот трек.")
        print(f"Ошибка скачивания: {e}")

# --- 2. Инлайн-режим: Поиск прямо во время ввода в любом чате ---
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
        if 'entries' in info:
            for idx, entry in enumerate(info['entries'][:5]):
                title = entry.get('title', 'Без названия')
                url = entry.get('url')
                uploader = entry.get('uploader', 'SoundCloud')
                
                # В инлайн-режиме без своего сервера баз данных проще всего сразу присылать 
                # ссылку на SoundCloud, встроенный плеер Telegram сам её красиво оформит.
                input_message_content = types.InputTextMessageContent(
                    message_text=f"🎵 **Слушаю трек:** [{title}]({url}) через SoundCloud.",
                    parse_mode="Markdown"
                )
                
                results.append(
                    types.InlineQueryResultArticle(
                        id=f"music_{idx}_{hashlib.md5(url.encode()).hexdigest()}",
                        title=title,
                        description=f"Исполнитель: {uploader}",
                        input_message_content=input_message_content
                    )
                )
                
        await inline_query.answer(results, cache_time=1)
    except Exception as e:
        print(f"Ошибка инлайн-поиска: {e}")

async main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
