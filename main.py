import os
import asyncio
import hashlib
import aiohttp
import streamlit as st
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramConflictError

# Проверка токена
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Ошибка: Переменная BOT_TOKEN не задана в настройках Streamlit!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "🎵 **Привет! Я твой идеальный музыкальный бот.**\n\n"
        "Отправь мне название песни или имя исполнителя (текстом), и я найду этот трек!"
    )

# --- РЕЖИМ 1: Поиск списка 5 песен по СЛОВАМ через API ---
@dp.message()
async def search_and_show_list(message: types.Message):
    query = message.text.strip()
    status_msg = await message.answer("🔍 Ищу трек по базе данных...")

    # Используем стабильное открытое музыкальное API для поиска треков
    search_url = f"https://apple.com{query}&entity=song&limit=5&lang=ru_ru"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url) as response:
                if response.status != 200:
                    await status_msg.edit_text("❌ Музыкальная база данных временно недоступна.")
                    return
                data = await response.json()

        if data and data.get('resultCount', 0) > 0:
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
            text = "🎶 **Вот что я нашёл. Выберите трек для скачивания:**\n\n"
            
            for idx, entry in enumerate(data['results'], 1):
                artist = entry.get('artistName', 'Неизвестен')
                track = entry.get('trackName', 'Без названия')
                preview_url = entry.get('previewUrl') # Прямая ссылка на аудиопоток
                
                title = f"{artist} — {track}"
                text += f"{idx}. **{title}**\n"
                
                # Хэшируем URL, чтобы уложиться в лимиты callback_data (64 байта)
                url_hash = hashlib.md5(preview_url.encode()).hexdigest()
                dp["url_cache_" + url_hash] = {
                    "url": preview_url,
                    "title": title
                }
                
                keyboard.inline_keyboard.append([
                    types.InlineKeyboardButton(text=f"📥 Скачать {idx}", callback_data=f"dl_{url_hash}")
                ])
                
            await status_msg.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await status_msg.edit_text("❌ Ничего не найдено. Попробуйте изменить запрос.")
            
    except Exception as e:
        await status_msg.edit_text("⚠️ Произошла ошибка при поиске.")
        print(f"Ошибка поиска: {e}")

# Скачивание и отправка MP3-файла в чат
@dp.callback_query(F.data.startswith("dl_"))
async def download_selected_track(callback: types.CallbackQuery):
    url_hash = callback.data.split("_")[1]
    track_data = dp.get("url_cache_" + url_hash)
    
    if not track_data:
        await callback.answer("❌ Ссылка устарела. Сделайте поиск заново.", show_alert=True)
        return
        
    await callback.message.edit_text("📥 Подготавливаю MP3-файл...")
    
    audio_url = track_data["url"]
    title = track_data["title"]
    file_path = f"{DOWNLOAD_DIR}/{url_hash}.mp3"
    
    try:
        # Скачиваем аудиофайл напрямую по ссылке без использования yt-dlp
        async with aiohttp.ClientSession() as session:
            async with session.get(audio_url) as response:
                if response.status == 200:
                    with open(file_path, 'wb') as f:
                        f.write(await response.read())
        
        if os.path.exists(file_path):
            await callback.message.edit_text("⚡ Отправляю файл в Telegram...")
            audio_file = types.FSInputFile(file_path, filename=f"{title}.mp3")
            
            # Твоя партнерская ссылка для монетизации
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
            await callback.message.edit_text("❌ Ошибка: не удалось обработать аудиофайл.")
            
    except Exception as e:
        await callback.message.edit_text("⚠️ Не удалось отправить этот трек.")
        print(f"Ошибка скачивания: {e}")

# Инлайн-режим (поиск в других чатах)
@dp.inline_query()
async def inline_search(inline_query: types.InlineQuery):
    query = inline_query.query.strip()
    if not query:
        return

    search_url = f"https://apple.com{query}&entity=song&limit=5&lang=ru_ru"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url) as response:
                data = await response.json()
                
        results = []
        if data and 'results' in data:
            for idx, entry in enumerate(data['results']):
                artist = entry.get('artistName', 'Неизвестен')
                track = entry.get('trackName', 'Без названия')
                preview_url = entry.get('previewUrl')
                
                title = f"{artist} — {track}"
                input_message_content = types.InputTextMessageContent(
                    message_text=f"🎵 **Найден трек:** [{title}]({preview_url})",
                    parse_mode="Markdown"
                )
                
                results.append(
                    types.InlineQueryResultArticle(
                        id=f"inline_{idx}_{hashlib.md5(preview_url.encode()).hexdigest()}",
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
    
    await bot.delete_webhook(drop_pending_updates=True)
    print("🚀 Бот успешно стартовал на Streamlit Cloud!")
    
    while True:
        try:
            await dp.start_polling(bot, handle_signals=False)
        except TelegramConflictError:
            await asyncio.sleep(3)
        except Exception as e:
            print(f"Системная ошибка: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
