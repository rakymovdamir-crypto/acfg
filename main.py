import os
import asyncio
import hashlib
import threading
import yt_dlp
import soundcloud
import streamlit as st
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramConflictError

# ─── Глобальный флаг — защита от двойного запуска ────────────────────────────
_BOT_STARTED = False
_BOT_LOCK = threading.Lock()

# ─── Конфигурация ────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Ошибка: BOT_TOKEN не задан в настройках Streamlit!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Кэш треков: { url_hash: { "url": "...", "title": "..." } }
url_cache: dict[str, dict] = {}

# ─── SoundCloud: поиск через yt-dlp, fallback — soundcloud-v2 ────────────────

def _search_ytdlp(query: str, limit: int = 5) -> list[dict]:
    """Ищет треки на SoundCloud через yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,       # не скачиваем, только метаданные
        "playlist_items": f"1-{limit}",
    }
    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            f"scsearch{limit}:{query}", download=False
        )
        for entry in info.get("entries", []):
            if not entry:
                continue
            results.append({
                "title": entry.get("title", "Без названия"),
                "uploader": entry.get("uploader", "Неизвестен"),
                "url": entry.get("url") or entry.get("webpage_url"),
                "duration": entry.get("duration"),
            })
    return results


def _search_soundcloud_v2(query: str, limit: int = 5) -> list[dict]:
    """Ищет треки через неофициальный SoundCloud API (soundcloud-v2)."""
    try:
        client = soundcloud.Client()
        tracks = client.search_tracks(query, limit=limit)
        results = []
        for t in tracks:
            results.append({
                "title": t.title,
                "uploader": t.user.get("username", "Неизвестен") if isinstance(t.user, dict) else str(t.user),
                "url": t.permalink_url,
                "duration": getattr(t, "duration", None),
            })
        return results
    except Exception as e:
        print(f"soundcloud-v2 ошибка поиска: {e}")
        return []


async def search_soundcloud(query: str, limit: int = 5) -> list[dict]:
    """Запускает поиск в executor (yt-dlp синхронный). Fallback на soundcloud-v2."""
    loop = asyncio.get_event_loop()

    # Сначала пробуем yt-dlp
    try:
        results = await loop.run_in_executor(None, _search_ytdlp, query, limit)
        if results:
            return results
    except Exception as e:
        print(f"yt-dlp поиск не удался: {e}")

    # Fallback: soundcloud-v2
    print("Переключаемся на soundcloud-v2...")
    results = await loop.run_in_executor(None, _search_soundcloud_v2, query, limit)
    return results


def _download_track_ytdlp(url: str, file_path: str) -> bool:
    """Скачивает трек через yt-dlp в MP3."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": file_path.replace(".mp3", ".%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        # yt-dlp может добавить расширение — ищем файл
        base = file_path.replace(".mp3", "")
        for ext in ["mp3", "m4a", "opus", "webm"]:
            candidate = f"{base}.{ext}"
            if os.path.exists(candidate):
                if candidate != file_path:
                    os.rename(candidate, file_path)
                return True
        return False
    except Exception as e:
        print(f"yt-dlp скачивание не удалось: {e}")
        return False


async def download_track(url: str, file_path: str) -> bool:
    """Асинхронная обёртка над скачиванием."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download_track_ytdlp, url, file_path)

# ─── Команда /start ───────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "🎵 *Привет! Я музыкальный бот.*\n\n"
        "Отправь мне название песни или имя исполнителя — "
        "найду трек на SoundCloud и пришлю MP3!\n\n"
        "Пример: _Travis Scott — Goosebumps_",
        parse_mode="Markdown"
    )

# ─── Поиск треков ─────────────────────────────────────────────────────────────

@dp.message()
async def search_and_show_list(message: types.Message):
    query = message.text.strip()
    if not query:
        return

    status_msg = await message.answer("🔍 Ищу трек на SoundCloud...")

    try:
        tracks = await search_soundcloud(query, limit=5)
    except Exception as e:
        await status_msg.edit_text("⚠️ Ошибка при поиске. Попробуйте позже.")
        print(f"Ошибка поиска: {e}")
        return

    if not tracks:
        await status_msg.edit_text("❌ Ничего не найдено. Попробуйте изменить запрос.")
        return

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
    text = "🎶 *Результаты поиска на SoundCloud:*\n\n"

    for idx, track in enumerate(tracks, 1):
        title = f"{track['uploader']} — {track['title']}"
        duration = track.get("duration")
        dur_str = f" ({int(duration)//60}:{int(duration)%60:02d})" if duration else ""
        text += f"{idx}. *{title}*{dur_str}\n"

        url_hash = hashlib.md5(track["url"].encode()).hexdigest()
        url_cache[url_hash] = {"url": track["url"], "title": title}

        # Обрезаем название кнопки до 50 символов
        btn_label = f"📥 {idx}. {title}"[:50]
        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(
                text=btn_label,
                callback_data=f"dl_{url_hash}"   # 3 + 32 = 35 байт
            )
        ])

    await status_msg.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

# ─── Скачивание и отправка трека ──────────────────────────────────────────────

@dp.callback_query(F.data.startswith("dl_"))
async def download_selected_track(callback: types.CallbackQuery):
    url_hash = callback.data[3:]
    track_data = url_cache.get(url_hash)

    if not track_data:
        await callback.answer("❌ Ссылка устарела. Сделайте поиск заново.", show_alert=True)
        return

    await callback.message.edit_text("⏳ Скачиваю трек с SoundCloud...")

    title = track_data["title"]
    safe_title = "".join(c for c in title if c not in r'\/:*?"<>|')
    file_path = os.path.join(DOWNLOAD_DIR, f"{url_hash}.mp3")

    try:
        success = await download_track(track_data["url"], file_path)

        if not success or not os.path.exists(file_path):
            await callback.message.edit_text("❌ Не удалось скачать трек. Попробуйте другой.")
            return

        await callback.message.edit_text("⚡ Отправляю файл в Telegram...")

        audio_file = types.FSInputFile(file_path, filename=f"{safe_title}.mp3")

        partner_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(
                text="🎧 Слушать без интернета (90 дней бесплатно)",
                url="https://music.yandex.ru"   # ← замените на партнёрскую ссылку
            )
        ]])

        await callback.message.answer_audio(
            audio=audio_file,
            title=safe_title,
            caption="✅ Готово! Приятного прослушивания 🎧",
            reply_markup=partner_keyboard,
        )
        await callback.message.delete()

    except asyncio.TimeoutError:
        await callback.message.edit_text("⏱ Превышено время загрузки.")
    except Exception as e:
        await callback.message.edit_text("⚠️ Не удалось отправить трек.")
        print(f"Ошибка скачивания: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# ─── Инлайн-режим ─────────────────────────────────────────────────────────────

@dp.inline_query()
async def inline_search(inline_query: types.InlineQuery):
    query = inline_query.query.strip()
    if not query:
        await inline_query.answer([], cache_time=1)
        return

    try:
        tracks = await search_soundcloud(query, limit=5)
    except Exception:
        await inline_query.answer([], cache_time=1)
        return

    results = []
    for idx, track in enumerate(tracks):
        title = f"{track['uploader']} — {track['title']}"
        results.append(
            types.InlineQueryResultArticle(
                id=f"inline_{idx}_{hashlib.md5(track['url'].encode()).hexdigest()}",
                title=title,
                description="🎵 Нажмите, чтобы поделиться треком",
                input_message_content=types.InputTextMessageContent(
                    message_text=f"🎵 *{title}*\n\n[Слушать на SoundCloud]({track['url']})",
                    parse_mode="Markdown"
                )
            )
        )

    await inline_query.answer(results, cache_time=1)

# ─── Запуск бота ──────────────────────────────────────────────────────────────

async def run_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    print("🚀 Бот запущен!")
    await dp.start_polling(bot, handle_signals=False)

def start_bot_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while True:
        try:
            loop.run_until_complete(run_bot())
        except TelegramConflictError:
            print("⚠️ Конфликт polling — жду 5 секунд...")
            import time; time.sleep(5)
        except Exception as e:
            print(f"Системная ошибка: {e}")
            import time; time.sleep(5)

def ensure_bot_running():
    global _BOT_STARTED
    with _BOT_LOCK:
        if not _BOT_STARTED:
            _BOT_STARTED = True
            thread = threading.Thread(target=start_bot_thread, daemon=True, name="TelegramBot")
            thread.start()
            return True
        return False

# ─── Streamlit UI ─────────────────────────────────────────────────────────────

st.title("🎵 SoundCloud Музыкальный Бот")
st.write("Бот работает в Telegram 24/7 и ищет треки на SoundCloud.")

just_started = ensure_bot_running()
st.success("✅ Бот запущен!" if just_started else "✅ Бот уже работает.")
