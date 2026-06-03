import os
import time
import asyncio
import hashlib
import threading
import subprocess
import yt_dlp
import streamlit as st
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramConflictError

# ─── Запуск Tor ──────────────────────────────────────────────────────────────

def ensure_tor_running():
    """Запускает Tor если ещё не запущен. Возвращает True если успешно."""
    try:
        # Проверяем, запущен ли уже Tor
        result = subprocess.run(["pgrep", "-x", "tor"], capture_output=True)
        if result.returncode == 0:
            return True  # уже работает

        # Запускаем Tor в фоне
        subprocess.Popen(
            ["tor", "--SocksPort", "9050", "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Ждём пока Tor поднимется (обычно 5-15 сек)
        for _ in range(30):
            time.sleep(1)
            try:
                r = subprocess.run(
                    ["curl", "--socks5", "127.0.0.1:9050", "--max-time", "3",
                     "-s", "https://check.torproject.org/api/ip"],
                    capture_output=True, timeout=5
                )
                if b"IsTor" in r.stdout:
                    print("✅ Tor запущен успешно")
                    return True
            except Exception:
                pass
        print("⚠️ Tor не поднялся за 30 секунд")
        return False
    except FileNotFoundError:
        print("❌ Tor не установлен (нет в packages.txt?)")
        return False
    except Exception as e:
        print(f"❌ Ошибка запуска Tor: {e}")
        return False


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

# ─── SoundCloud: поиск через yt-dlp, fallback — sclib ────────────────────────

# Общие yt-dlp опции — без Tor (SoundCloud блокирует Tor-ноды)
YDL_BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    # Реалистичный браузерный User-Agent
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://soundcloud.com/",
    },
    # Немного случайной задержки — меньше шансов получить бан
    "sleep_interval": 1,
    "max_sleep_interval": 3,
}


def _search_ytdlp(query: str, limit: int = 5) -> list[dict]:
    """Ищет треки на SoundCloud через yt-dlp."""
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
    """Поиск через yt-dlp."""
    loop = asyncio.get_event_loop()
    ytdlp_error = None

    try:
        results = await loop.run_in_executor(None, _search_ytdlp, query, limit)
        if results:
            return results
    except Exception as e:
        ytdlp_error = e
        print(f"yt-dlp поиск не удался: {e}")

    # Если yt-dlp вернул пустой список или упал — пробрасываем ошибку
    if ytdlp_error:
        raise RuntimeError(f"yt-dlp: {ytdlp_error}")

    return []


def _download_track_ytdlp(url: str, file_path: str) -> bool:
    """Скачивает трек через yt-dlp в MP3."""
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
        print(f"yt-dlp скачивание не удалось: {e}")
        return False


async def download_track(url: str, file_path: str) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download_track_ytdlp, url, file_path)

# ─── Команда /start ───────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer(
        "🎵 *Привет! Я музыкальный бот.*\n\n"
        "Отправь мне название песни или имя исполнителя — "
        "найду трек на SoundCloud и пришлю MP3!\n\n"
        "Пример: _Travis Scott Goosebumps_",
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
        # Показываем точную ошибку прямо в Telegram
        err_text = str(e)[:500]
        await status_msg.edit_text(
            f"⚠️ Ошибка при поиске:\n\n<code>{err_text}</code>",
            parse_mode="HTML"
        )
        print(f"Ошибка поиска: {e}")
        return

    if not tracks:
        await status_msg.edit_text(
            "❌ Ничего не найдено.\n\n"
            "Попробуйте запрос на английском языке, например:\n"
            "<i>Travis Scott Goosebumps</i>",
            parse_mode="HTML"
        )
        return

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
    text = "🎶 <b>Результаты поиска на SoundCloud:</b>\n\n"

    for idx, track in enumerate(tracks, 1):
        title = f"{track['uploader']} — {track['title']}"
        duration = track.get("duration")
        dur_str = f"{int(duration)//60}:{int(duration)%60:02d}" if duration else ""
        # Каждый трек — кликабельная ссылка с длительностью как на фото
        text += f"{dur_str} · <a href=\"{track['url']}\">{title}</a>\n"

        url_hash = hashlib.md5(track["url"].encode()).hexdigest()
        url_cache[url_hash] = {"url": track["url"], "title": title, "track_title": track["title"], "performer": track["uploader"]}

        keyboard.inline_keyboard.append([
            types.InlineKeyboardButton(
                text=f"📥 Скачать: {title[:35]}",
                callback_data=f"dl_{url_hash}"
            )
        ])

    await status_msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)

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
    track_title = track_data.get("track_title", title)
    performer = track_data.get("performer", "")
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
                url="https://music.yandex.ru"
            )
        ]])

        safe_track_title = "".join(c for c in track_title if c not in r'\/:*?"<>|')
        safe_performer = "".join(c for c in performer if c not in r'\/:*?"<>|')

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
            f"⚠️ Не удалось отправить трек:\n\n<code>{str(e)[:300]}</code>",
            parse_mode="HTML"
        )
        print(f"Ошибка скачивания: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


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
