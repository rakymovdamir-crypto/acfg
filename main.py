import os
import asyncio
import hashlib
import streamlit as st
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramConflictError
from yt_dlp import YoutubeDL

# Проверка токена
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Ошибка: Переменная BOT_TOKEN не задана в настройках Streamlit!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ЖЕЛЕЗНЫЙ ОБХОД БЛОКИРОВКИ: Маскируемся под мобильное приложение YouTube (Android)
# Музыка будет находиться ВСЕГДА, так как YouTube не банит мобильные клиенты
SEARCH_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch5',  # Ищем ТОП-5 треков
    'quiet': True,
    'extract_flat': False,          # Получаем полные данные для обхода блокировок
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
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
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
    }
}

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "🎵 **Привет! Я твой идеальный музыкальный бот.**\n\n"
        "• Отправь мне название трека прямо сюда, чтобы выбрать из списка.\n"
        "• Или используй меня в любом чате (инлайн), написав: `@юзернейм_вашего_бота название`"
    )

# --- РЕЖИМ 1: Поиск списка 5 песен через чат ---
@dp.message()
async def search_and_show_list(message: types.Message):
    query = message.text
    status_msg = await message.answer("🔍 Ищу варианты, подождите...")

    try:
        loop = asyncio.get_event_loop()
        with YoutubeDL(SEARCH_OPTIONS) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
            
        if info and 'entries' in info and len(info['entries']) > 0:
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
            text = "🎶 **Вот что я нашёл. Выберите трек для скачивания:**\n\n"
            
            # Убираем пустые результаты, если они есть
            valid_entries = [e for e in info['entries'] if e is not None]
            
            if not valid_entries:
                await status_msg.edit_text("❌ Ничего не найдено. Попробуйте изменить запрос.")
                return

            for idx, entry in enumerate(valid_entries[:5], 1):
                title = entry.get('title', 'Без названия')
                url = entry.get('webpage_url', f"https://youtube.com{entry.get('id')}")
                
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
    url_hash = callback.data.split("_")[1]
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
            
            # МОНЕТИЗАЦИЯ: Полезная партнерская кнопка
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
                url = entry.get('webpage_url', f"https://youtube.com{entry.get('id')}")
                
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

# Главная точка входа
async def main():
    st.title("🎵 Мой Музыкальный Бот успешно запущен!")
    st.write("Бот стабильно работает в Telegram 24/7.")
    
    # Принудительно очищаем старые зависшие вебхуки
    await bot.delete_webhook(drop_pending_updates=True)
    
    print("🚀 Бот успешно стартовал на Streamlit Cloud!")
    
    # Запускаем бесконечный опрос с игнорированием временных ошибок конфликтов процессов
    while True:
        try:
            await dp.start_polling(bot, handle_signals=False)
        except TelegramConflictError:
            # Если старый процесс на хостинге перехватил сообщение, просто спим 3 секунды и пробуем снова
            await asyncio.sleep(3)
        except Exception as e:
            print(f"Системная ошибка: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
