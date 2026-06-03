import os
import asyncio
import hashlib
import streamlit as st  # Нужно, чтобы Streamlit не закрывал приложение
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from yt_dlp import YoutubeDL

# Считываем токен
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Ошибка: Переменная BOT_TOKEN не задана!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Настройки поиска (ТОП-5 результатов строго из SoundCloud с обходом блокировок)
SEARCH_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'scsearch5',  # Ищем ТОП-5 в SoundCloud
    'quiet': True,
    'extract_flat': True,
    # Передаем публичный ключ авторизации SoundCloud и маскируемся под браузер
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    },
    # Специфичные настройки для SoundCloud, чтобы обойти бан IP хостинга
    'extra_query_params': {
        'client_id': 'iZIs9mchVcX52Xfz9S4v3ZftjndvCuOn' # Официальный публичный веб-ключ SoundCloud
    }
}

# Настройки скачивания MP3 из SoundCloud
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
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    },
    'extra_query_params': {
        'client_id': 'iZIs9mchVcX52Xfz9S4v3ZftjndvCuOn'
    }
}

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "🎵 **Привет! Я твой музыкальный бот.**\n\n"
        "• Отправь мне название трека прямо сюда, чтобы выбрать из списка.\n"
        "• Или используй меня в любом чате (инлайн), написав: `@юзернейм_бота название`"
    )

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
            text = "🎶 **Вот что я нашёл. Выберите трек:**\n\n"
            
            for idx, entry in enumerate(info['entries'][:5], 1):
                title = entry.get('title', 'Без названия')
                url = entry.get('url')
                uploader = entry.get('uploader', 'SoundCloud')
                
                text += f"{idx}. **{title}** — __{uploader}__\n"
                
                url_hash = hashlib.md5(url.encode()).hexdigest()
                dp["url_cache_" + url_hash] = url
                
                keyboard.inline_keyboard.append([
                    types.InlineKeyboardButton(text=f"📥 Скачать {idx}", callback_data=f"dl_{url_hash}")
                ])
                
            await status_msg.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await status_msg.edit_text("❌ Ничего не найдено.")
    except Exception as e:
        await status_msg.edit_text("⚠️ Ошибка поиска треков.")
        print(f"Ошибка поиска: {e}")

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
            await callback.message.answer_audio(audio=audio_file, title=title, caption="Готово! 🎧")
            os.remove(file_path)
            await callback.message.delete()
        else:
            await callback.message.edit_text("❌ Ошибка: файл потерялся.")
    except Exception as e:
        await callback.message.edit_text("⚠️ Не удалось скачать трек.")
        print(f"Ошибка скачивания: {e}")

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
                
                input_message_content = types.InputTextMessageContent(
                    message_text=f"🎵 **Трек из SoundCloud:** [{title}]({url})",
                    parse_mode="Markdown"
                )
                
                results.append(
                    types.InlineQueryResultArticle(
                        id=f"inline_{idx}_{hashlib.md5(url.encode()).hexdigest()}",
                        title=title,
                        description=f"Автор: {uploader}",
                        input_message_content=input_message_content
                    )
                )
                
        await inline_query.answer(results, cache_time=1)
    except Exception as e:
        print(f"Ошибка инлайн-поиска: {e}")

async def main():
    # Простая заглушка для интерфейса Streamlit, чтобы сервер не засыпал
    st.title("🎵 Мой Музыкальный Бот запущен!")
    st.write("Бот успешно работает в Telegram 24/7.")
    
    # СНОСИМ ВСЕ ВЕБХУКИ И СТАРЫЙ СПАМ НА КОРНЮ
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запуск опроса
    await dp.start_polling(bot, handle_signals=False)


if __name__ == "__main__":
    asyncio.run(main())
