import os
import asyncio
import hashlib
import aiohttp
import threading
import streamlit as st
from urllib.parse import quote
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramConflictError

# ─── Конфигурация ────────────────────────────────────────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Ошибка: Переменная BOT_TOKEN не задана в настройках Streamlit!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Кэш для хранения URL треков (вместо dp["key"])
url_cache: dict[str, dict] = {}

# ─── Команда /start ───────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "🎵 *Привет! Я твой музыкальный бот.*\n\n"
        "Отправь мне название песни или имя исполнителя, и я найду этот трек!\n\n"
        "⚠️ Доступны только 30-секундные превью из iTunes.",
        parse_mode="Markdown"
    )

# ─── Поиск треков ─────────────────────────────────────────────────────────────

@dp.message()
async def search_and_show_list(message: types.Message):
    query = message.text.strip()
    if not query:
        return

    status_msg = await message.answer("🔍 Ищу трек по базе данных...")

    # iTunes Search API
    search_url = (
        f"https://itunes.apple.com/search"
        f"?term={quote(query)}&entity=song&limit=5&lang=ru_ru"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    await status_msg.edit_text("❌ Музыкальная база данных временно недоступна.")
                    return
                data = await response.json()

        if not data or data.get("resultCount", 0) == 0:
            await status_msg.edit_text("❌ Ничего не найдено. Попробуйте изменить запрос.")
            return

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
        text = "🎶 *Вот что я нашёл. Выберите трек для скачивания:*\n\n"

        for idx, entry in enumerate(data["results"], 1):
            artist = entry.get("artistName", "Неизвестен")
            track = entry.get("trackName", "Без названия")
            preview_url = entry.get("previewUrl")

            # Пропускаем треки без превью
            if not preview_url:
                continue

            title = f"{artist} — {track}"
            text += f"{idx}. *{title}*\n"

            # Сохраняем URL в кэш по MD5-хэшу
            url_hash = hashlib.md5(preview_url.encode()).hexdigest()
            url_cache[url_hash] = {"url": preview_url, "title": title}

            keyboard.inline_keyboard.append([
                types.InlineKeyboardButton(
                    text=f"📥 Скачать {idx}: {title[:30]}",
                    callback_data=f"dl_{url_hash}"  # "dl_" + 32 символа = 35 байт (< 64)
                )
            ])

        if not keyboard.inline_keyboard:
            await status_msg.edit_text("❌ Треки найдены, но превью недоступны.")
            return

        await status_msg.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

    except asyncio.TimeoutError:
        await status_msg.edit_text("⏱ Превышено время ожидания. Попробуйте ещё раз.")
    except Exception as e:
        await status_msg.edit_text("⚠️ Произошла ошибка при поиске.")
        print(f"Ошибка поиска: {e}")

# ─── Скачивание и отправка трека ──────────────────────────────────────────────

@dp.callback_query(F.data.startswith("dl_"))
async def download_selected_track(callback: types.CallbackQuery):
    # Безопасное извлечение хэша (убираем только префикс "dl_")
    url_hash = callback.data[3:]
    track_data = url_cache.get(url_hash)

    if not track_data:
        await callback.answer("❌ Ссылка устарела. Сделайте поиск заново.", show_alert=True)
        return

    await callback.message.edit_text("📥 Подготавливаю MP3-файл...")

    audio_url = track_data["url"]
    title = track_data["title"]
    # Очищаем имя файла от недопустимых символов
    safe_title = "".join(c for c in title if c not in r'\/:*?"<>|')
    file_path = os.path.join(DOWNLOAD_DIR, f"{url_hash}.mp3")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(audio_url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status != 200:
                    await callback.message.edit_text("❌ Не удалось скачать трек.")
                    return
                with open(file_path, "wb") as f:
                    f.write(await response.read())

        if not os.path.exists(file_path):
            await callback.message.edit_text("❌ Ошибка: файл не был создан.")
            return

        await callback.message.edit_text("⚡ Отправляю файл в Telegram...")

        audio_file = types.FSInputFile(file_path, filename=f"{safe_title}.mp3")

        # Партнёрская кнопка — замените URL на свой
        partner_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(
                text="🎧 Слушать без интернета (90 дней бесплатно)",
                url="https://music.yandex.ru"  # ← замените на партнёрскую ссылку
            )
        ]])

        await callback.message.answer_audio(
            audio=audio_file,
            title=safe_title,
            caption="✅ Готово! Приятного прослушивания 🎧\n\n⚠️ *Это 30-секундное превью из iTunes.*",
            reply_markup=partner_keyboard,
            parse_mode="Markdown"
        )

        await callback.message.delete()

    except asyncio.TimeoutError:
        await callback.message.edit_text("⏱ Превышено время загрузки файла.")
    except Exception as e:
        await callback.message.edit_text("⚠️ Не удалось отправить этот трек.")
        print(f"Ошибка скачивания: {e}")
    finally:
        # Всегда удаляем временный файл
        if os.path.exists(file_path):
            os.remove(file_path)

# ─── Инлайн-режим ─────────────────────────────────────────────────────────────

@dp.inline_query()
async def inline_search(inline_query: types.InlineQuery):
    query = inline_query.query.strip()
    if not query:
        await inline_query.answer([], cache_time=1)
        return

    search_url = (
        f"https://itunes.apple.com/search"
        f"?term={quote(query)}&entity=song&limit=5&lang=ru_ru"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                data = await response.json()

        results = []
        for idx, entry in enumerate(data.get("results", [])):
            artist = entry.get("artistName", "Неизвестен")
            track = entry.get("trackName", "Без названия")
            preview_url = entry.get("previewUrl")

            if not preview_url:
                continue

            title = f"{artist} — {track}"
            results.append(
                types.InlineQueryResultArticle(
                    id=f"inline_{idx}_{hashlib.md5(preview_url.encode()).hexdigest()}",
                    title=title,
                    description="🎵 Нажмите, чтобы поделиться треком",
                    input_message_content=types.InputTextMessageContent(
                        message_text=f"🎵 *{title}*\n\n[Превью]({preview_url})",
                        parse_mode="Markdown"
                    )
                )
            )

        await inline_query.answer(results, cache_time=1)

    except Exception as e:
        print(f"Ошибка инлайн-поиска: {e}")
        await inline_query.answer([], cache_time=1)

# ─── Запуск бота в отдельном потоке (совместимо со Streamlit) ─────────────────

async def run_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    print("🚀 Бот успешно стартовал!")
    while True:
        try:
            await dp.start_polling(bot, handle_signals=False)
        except TelegramConflictError:
            print("⚠️ Конфликт polling — жду 3 секунды...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"Системная ошибка: {e}")
            await asyncio.sleep(5)

def start_bot_thread():
    """Запускает бота в отдельном потоке с собственным event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot())

# ─── Streamlit UI ─────────────────────────────────────────────────────────────

st.title("🎵 Музыкальный Бот")
st.write("Бот работает в Telegram 24/7.")
st.info("Предоставляет 30-секундные превью треков через iTunes API.")

# Запускаем бота один раз при старте Streamlit
if "bot_started" not in st.session_state:
    st.session_state["bot_started"] = True
    thread = threading.Thread(target=start_bot_thread, daemon=True)
    thread.start()
    st.success("✅ Бот запущен!")
else:
    st.success("✅ Бот уже работает.")
