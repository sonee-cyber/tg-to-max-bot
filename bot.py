"""
Telegram → МАКС зеркало канала.

Запуск:
    cp .env.example .env   # заполни токены
    pip install -r requirements.txt
    python bot.py
"""
import logging
import os
import io

import telebot
import requests
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
# Вспомогательные функции
# ──────────────────────────────────────────────

def is_from_channel(message: telebot.types.Message) -> bool:
    """Проверяем, что сообщение из нашего канала."""
    chat = message.chat
    if chat.type == "channel" and chat.id == TG_CHANNEL_ID:
        return True
    # Пересланное из канала (forward_from_chat)
    fwd = message.forward_from_chat
    if fwd and fwd.id == TG_CHANNEL_ID:
        return True
    return False


def get_caption(message: telebot.types.Message) -> str:
    """Достать текст/подпись с форматированием."""
    raw = message.caption or message.text or ""
    entities = message.caption_entities or message.entities or []
    if entities:
        return tg_entities_to_max_markdown(raw, entities)
    return raw


def download_tg_file(file_id: str) -> bytes:
    """Скачать файл из Telegram."""
    file_info = bot.get_file(file_id)
    url = f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file_info.file_path}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def pick_best_photo(photos) -> str:
    """Выбрать фото наилучшего качества (последний элемент в массиве)."""
    return photos[-1].file_id


# ──────────────────────────────────────────────
# Обработчики сообщений
# ──────────────────────────────────────────────

def handle_text(message: telebot.types.Message):
    text = get_caption(message)
    if not text:
        return
    text = truncate(text)
    result = max_api.send_message(MAX_CHAT_ID, text)
    logger.info(f"Текст отправлен в МАКС: {result}")


def handle_photo(message: telebot.types.Message):
    caption = truncate(get_caption(message))
    file_id = pick_best_photo(message.photo)
    try:
        photo_bytes = download_tg_file(file_id)
        result = max_api.upload_and_send_photo(MAX_CHAT_ID, photo_bytes, caption)
        logger.info(f"Фото отправлено в МАКС: {result}")
    except Exception as e:
        logger.error(f"Ошибка отправки фото: {e}")
        # Фолбэк: отправить только текст
        if caption:
            max_api.send_message(MAX_CHAT_ID, caption)


def handle_video(message: telebot.types.Message):
    caption = truncate(get_caption(message))
    file_id = message.video.file_id
    file_size = message.video.file_size or 0

    # MAX ограничивает размер файла; для больших видео шлём только текст со ссылкой
    MAX_VIDEO_BYTES = 50 * 1024 * 1024  # 50 MB
    if file_size > MAX_VIDEO_BYTES:
        logger.warning(f"Видео слишком большое ({file_size} байт), пропускаем загрузку")
        if caption:
            max_api.send_message(MAX_CHAT_ID, f"🎥 {caption}")
        return

    try:
        video_bytes = download_tg_file(file_id)
        result = max_api.upload_and_send_video(MAX_CHAT_ID, video_bytes, caption)
        logger.info(f"Видео отправлено в МАКС: {result}")
    except Exception as e:
        logger.error(f"Ошибка отправки видео: {e}")
        if caption:
            max_api.send_message(MAX_CHAT_ID, f"🎥 {caption}")


def handle_document(message: telebot.types.Message):
    caption = truncate(get_caption(message))
    doc = message.document
    try:
        file_bytes = download_tg_file(doc.file_id)
        result = max_api.upload_and_send_file(MAX_CHAT_ID, file_bytes, doc.file_name or "file", caption)
        logger.info(f"Файл отправлен в МАКС: {result}")
    except Exception as e:
        logger.error(f"Ошибка отправки файла: {e}")
        if caption:
            max_api.send_message(MAX_CHAT_ID, caption)


def handle_animation(message: telebot.types.Message):
    """GIF/анимация — отправляем как файл."""
    caption = truncate(get_caption(message))
    anim = message.animation
    try:
        file_bytes = download_tg_file(anim.file_id)
        result = max_api.upload_and_send_file(MAX_CHAT_ID, file_bytes, "animation.gif", caption)
        logger.info(f"GIF отправлен в МАКС: {result}")
    except Exception as e:
        logger.error(f"Ошибка отправки GIF: {e}")
        if caption:
            max_api.send_message(MAX_CHAT_ID, caption)


# ──────────────────────────────────────────────
# Главный обработчик
# ──────────────────────────────────────────────

@bot.channel_post_handler(func=lambda m: m.chat.id == TG_CHANNEL_ID)
def on_channel_post(message: telebot.types.Message):
    """Любое сообщение из вашего канала."""
    logger.info(f"Новый пост из канала {message.chat.title}: content_type={message.content_type}")

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
        # Для остальных типов (audio, voice, sticker...) — шлём только текст
        caption = get_caption(message)
        if caption:
            max_api.send_message(MAX_CHAT_ID, truncate(caption))
        logger.info(f"Тип {message.content_type} не поддерживается полностью, отправлен только текст")


# ──────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Бот запущен. Ожидаю посты из канала...")
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
