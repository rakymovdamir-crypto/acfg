import os
import asyncio
import hashlib
import yt_dlp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramConflictError

# ─── Конфигурация ────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Ошибка: переменная BOT_TOKEN не задана!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Кэш треков: { url_hash: { "url": "...", "title": "...", ... } }
url_cache: dict[str, dict] = {}

# ─── yt-dlp настройки ────────────────────────────────────────────────────────
YDL_BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://soundcloud.com/",
    },
    "sleep_interval": 1,
    "max_sleep_interval": 3,
}

# ─── Поиск треков ─────────────────────────────────────────────────────────────

def _search_ytdlp(query: str, limit: int = 5) -> list[dict]:
    ydl_opts = {
        **YDL_BASE_OPTS,
        "extract_flat": True,
        "playlist_items": f"1-{limit}",
    }
    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
        for entry in (info.get("entries") or []):
            if not entry:
                continue
            url = entry.get("webpage_url") or entry.get("url")
            if not url:
                continue
            results.append({
                "title": entry.get("title", "Без названия"),
                "uploader": entry.get("uploader") or entry.get("channel", "Неизвестен"),
                "url": url,
                "duration": entry.get("duration"),
            })
    return results


async def search_soundcloud(query: str, limit: int = 5) -> list[dict]:
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, _search_ytdlp, query, limit)
        return results
    except Exception as e:
        raise RuntimeError(str(e))

# ─── Скачивание треков ────────────────────────────────────────────────────────

def _download_track_ytdlp(url: str, file_path: str) -> bool:
    ydl_opts = {
        **YDL_BASE_OPTS,
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
        base = file_path.replace(".mp3", "")
        for ext in ["mp3", "m4a", "opus", "webm"]:
            candidate = f"{base}.{ext}"
            if os.path.exists(candidate):
                if candidate != file_path:
                    os.rename(candidate, file_path)
                return True
        return False
    except Exception as e:
        print(f"Ошибка скачивания: {e}")
        return False


async def download_track(url: str, file_path: str) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download_track_ytdlp, url, file_path)

# ─── Команда /start ───────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "🎵 <b>Привет! Я музыкальный бот.</b>\n\n"
        "Отправь мне название песни или имя исполнителя — "
        "найду трек на SoundCloud и пришлю MP3!\n\n"
        "Пример: <i>Travis Scott Goosebumps</i>",
        parse_mode="HTML"
    )

# ─── Обработка текстовых сообщений — поиск ───────────────────────────────────

@dp.message()
async def search_and_show_list(message: types.Message):
    query = message.text.strip()
    if not query:
        return

    status_msg = await message.answer("🔍 Ищу трек на SoundCloud...")

    try:
        tracks = await search_soundcloud(query, limit=5)
    except Exception as e:
        await status_msg.edit_text(
            f"⚠️ Ошибка при поиске:\n\n<code>{str(e)[:500]}</code>",
            parse_mode="HTML"
        )
        return

    if not tracks:
        await status_msg.edit_text(
            "❌ Ничего не найдено.\n\n"
            "Попробуйте запрос на английском, например:\n"
            "<i>Travis Scott Goosebumps</i>",
            parse_mode="HTML"
        )
        return

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
    text = "🎶 <b>Результаты поиска на SoundCloud:</b>\n\n"

    for track in tracks:
        title = f"{track['uploader']} — {track['title']}"
        duration = track.get("duration")
        dur_str = f"{int(duration)//60}:{int(duration)%60:02d}" if duration else ""
        text += f"{dur_str} · <a href=\"{track['url']}\">{title}</a>\n"

        url_hash = hashlib.md5(track["url"].encode()).hexdigest()
        url_cache[url_hash] = {
            "url": track["url"],
            "title": title,
            "track_title": track["title"],
            "performer": track["uploader"],
        }

        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(
                text=f"📥 Скачать: {title[:35]}",
                callback_data=f"dl_{url_hash}"
            )
        ])

    await status_msg.edit_text(
        text, reply_markup=keyboard,
        parse_mode="HTML", disable_web_page_preview=True
    )

# ─── Скачивание по кнопке ─────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("dl_"))
async def download_selected_track(callback: types.CallbackQuery):
    url_hash = callback.data[3:]
    track_data = url_cache.get(url_hash)

    if not track_data:
        await callback.answer("❌ Ссылка устарела. Сделайте поиск заново.", show_alert=True)
        return

    await callback.message.edit_text("⏳ Скачиваю трек с SoundCloud...")

    track_title = track_data.get("track_title", track_data["title"])
    performer = track_data.get("performer", "")
    safe_track_title = "".join(c for c in track_title if c not in r'\/:*?"<>|')
    safe_performer = "".join(c for c in performer if c not in r'\/:*?"<>|')
    file_path = os.path.join(DOWNLOAD_DIR, f"{url_hash}.mp3")

    try:
        success = await download_track(track_data["url"], file_path)

        if not success or not os.path.exists(file_path):
            await callback.message.edit_text("❌ Не удалось скачать трек. Попробуйте другой.")
            return

        await callback.message.edit_text("⚡ Отправляю файл в Telegram...")

        audio_file = types.FSInputFile(file_path, filename=f"{safe_track_title}.mp3")

        partner_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(
                text="🎧 Слушать без интернета (90 дней бесплатно)",
                url="https://music.yandex.ru"
            )
        ]])

        await callback.message.answer_audio(
            audio=audio_file,
            title=safe_track_title,
            performer=safe_performer,
            caption="✅ Готово! Приятного прослушивания 🎧",
            reply_markup=partner_keyboard,
        )
        await callback.message.delete()

    except asyncio.TimeoutError:
        await callback.message.edit_text("⏱ Превышено время загрузки.")
    except Exception as e:
        await callback.message.edit_text(
            f"⚠️ Ошибка:\n\n<code>{str(e)[:300]}</code>",
            parse_mode="HTML"
        )
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# ─── Запуск ───────────────────────────────────────────────────────────────────

async def main():
    print("🚀 Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
