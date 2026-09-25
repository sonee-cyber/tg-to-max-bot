"""
Telegram → МАКС зеркало канала.
"""
import logging
import os
import requests
import telebot
from dotenv import load_dotenv

from max_api import MaxAPI
from formatter import tg_entities_to_max_markdown, truncate

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHANNEL_ID = int(os.environ["TG_CHANNEL_ID"])
MAX_BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
MAX_CHAT_ID = os.environ["MAX_CHAT_ID"]

bot = telebot.TeleBot(TG_BOT_TOKEN, parse_mode=None)
max_api = MaxAPI(MAX_BOT_TOKEN)

# ──────────────────────────────────────────────
def get_caption(message: telebot.types.Message) -> str:
    raw = message.caption or message.text or ""
    entities = message.caption_entities or message.entities or []
    if entities:
        return tg_entities_to_max_markdown(raw, entities)
    return raw

def download_tg_file(file_id: str) -> bytes:
    file_info = bot.get_file(file_id)
    url = f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file_info.file_path}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content

def pick_best_photo(photos) -> str:
    return photos[-1].file_id

# ──────────────────────────────────────────────
def handle_text(message):
    text = truncate(get_caption(message))
    if not text: return
    result = max_api.send_message(MAX_CHAT_ID, text)
    logger.info(f"Текст отправлен в МАКС: {result}")

def handle_photo(message):
    caption = truncate(get_caption(message))
    file_id = pick_best_photo(message.photo)
    try:
        photo_bytes = download_tg_file(file_id)
        result = max_api.upload_and_send_photo(MAX_CHAT_ID, photo_bytes, caption)
        logger.info(f"Фото отправлено в МАКС: {result}")
    except Exception as e:
        logger.error(f"Ошибка фото: {e}")
        if caption:
            max_api.send_message(MAX_CHAT_ID, caption)

def handle_video(message):
    caption = truncate(get_caption(message))
    file_size = message.video.file_size or 0
    if file_size > 50 * 1024 * 1024:
        logger.warning(f"Видео большое {file_size}, шлю только текст")
        if caption:
            max_api.send_message(MAX_CHAT_ID, f"🎥 {caption}")
        return
    try:
        video_bytes = download_tg_file(message.video.file_id)
        result = max_api.upload_and_send_video(MAX_CHAT_ID, video_bytes, caption)
        logger.info(f"Видео отправлено в МАКС: {result}")
    except Exception as e:
        logger.error(f"Ошибка видео: {e}")
        if caption:
            max_api.send_message(MAX_CHAT_ID, f"🎥 {caption}")

def handle_document(message):
    caption = truncate(get_caption(message))
    try:
        file_bytes = download_tg_file(message.document.file_id)
        result = max_api.upload_and_send_file(MAX_CHAT_ID, file_bytes, message.document.file_name or "file", caption)
        logger.info(f"Файл отправлен в МАКС: {result}")
    except Exception as e:
        logger.error(f"Ошибка файла: {e}")
        if caption:
            max_api.send_message(MAX_CHAT_ID, caption)

def handle_animation(message):
    caption = truncate(get_caption(message))
    try:
        file_bytes = download_tg_file(message.animation.file_id)
        result = max_api.upload_and_send_file(MAX_CHAT_ID, file_bytes, "animation.gif", caption)
        logger.info(f"GIF отправлен в МАКС: {result}")
    except Exception as e:
        logger.error(f"Ошибка GIF: {e}")
        if caption:
            max_api.send_message(MAX_CHAT_ID, caption)

# ──────────────────────────────────────────────
# ГЛАВНЫЙ ОБРАБОТЧИК - вот тут был фикс
# ──────────────────────────────────────────────
@bot.channel_post_handler(
    content_types=['text','photo','video','document','animation','audio','voice','video_note','sticker'],
    func=lambda m: m.chat.id == TG_CHANNEL_ID
)
def on_channel_post(message: telebot.types.Message):
    logger.info(f"🔥 ПОЙМАЛ ПОСТ из канала {message.chat.id}: type={message.content_type} caption={message.caption or message.text}")

    try:
        if message.content_type == "text":
            handle_text(message)
        elif message.content_type == "photo":
            handle_photo(message)
        elif message.content_type == "video":
            handle_video(message)
        elif message.content_type == "document":
            handle_document(message)
        elif message.content_type == "animation":
            handle_animation(message)
        else:
            caption = get_caption(message)
            if caption:
                max_api.send_message(MAX_CHAT_ID, truncate(caption))
            logger.info(f"Тип {message.content_type} - отправлен только текст")
    except Exception as e:
        logger.exception(f"Ошибка в on_channel_post: {e}")

if __name__ == "__main__":
    logger.info(f"Бот запущен. Жду посты из канала {TG_CHANNEL_ID}...")
    # Чистим вебхук чтобы не было 409 Conflict
    bot.remove_webhook()
    bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)