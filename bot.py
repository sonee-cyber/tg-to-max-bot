import os
import time
import logging
import tempfile
import telebot
from dotenv import load_dotenv
from telebot.apihelper import ApiTelegramException

import formatter
import max_api

load_dotenv()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHANNEL_ID = int(os.getenv("TG_CHANNEL_ID", "0") or 0)
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")

if not TG_BOT_TOKEN or not TG_CHANNEL_ID or not MAX_CHAT_ID:
    raise RuntimeError("Не заданы TG_BOT_TOKEN / TG_CHANNEL_ID / MAX_CHAT_ID в Variables")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# threaded=False важно для Railway - иначе 409 чаще ловится
bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)

def get_text_and_entities(msg):
    """Достает текст и entities и из text и из caption"""
    if msg.content_type == 'text':
        return msg.text or "", msg.entities
    else:
        return msg.caption or "", msg.caption_entities

def download_tg_file(file_id: str) -> str:
    """Скачивает файл из ТГ в /tmp и возвращает путь"""
    file_info = bot.get_file(file_id)
    downloaded = bot.download_file(file_info.file_path)
    suffix = os.path.splitext(file_info.file_path)[-1] or ".tmp"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(downloaded)
    tmp.close()
    return tmp.name

@bot.channel_post_handler(content_types=['text', 'photo', 'video', 'document', 'animation', 'audio', 'voice'])
def handle_channel_post(message):
    if message.chat.id != TG_CHANNEL_ID:
        logger.info(f"Игнор пост из {message.chat.id}, жду {TG_CHANNEL_ID}")
        return

    try:
        text, entities = get_text_and_entities(message)
        formatted_text = formatter.format_telegram_to_max(text, entities)

        logger.info(f"🔥 ПОЙМАЛ ПОСТ из канала {message.chat.id}: type={message.content_type} caption={text[:100]}")

        if message.content_type == 'text':
            max_api.send_text(MAX_CHAT_ID, formatted_text)
            logger.info("Текст отправлен в МАКС")

        elif message.content_type == 'photo':
            # берем самое большое фото
            file_id = message.photo[-1].file_id
            path = download_tg_file(file_id)
            max_api.send_photo(MAX_CHAT_ID, path, formatted_text)
            os.remove(path)
            logger.info(f"Фото отправлено в МАКС: {path}")

        elif message.content_type == 'video':
            file_id = message.video.file_id
            path = download_tg_file(file_id)
            max_api.send_video(MAX_CHAT_ID, path, formatted_text)
            os.remove(path)
            logger.info("Видео отправлено в МАКС")

        elif message.content_type in ('document', 'animation', 'audio', 'voice'):
            file_id = (message.document or message.animation or message.audio or message.voice).file_id
            path = download_tg_file(file_id)
            max_api.send_document(MAX_CHAT_ID, path, formatted_text)
            os.remove(path)
            logger.info("Документ отправлен в МАКС")

    except Exception as e:
        logger.exception(f"Ошибка при пересылке поста: {e}")

# На случай если бот добавят в канал как админа и пишут обычные сообщения
@bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'animation', 'audio', 'voice'])
def handle_message(message):
    # это для теста в личке боту, в канале работает channel_post_handler
    if str(message.chat.id) == str(TG_CHANNEL_ID):
        handle_channel_post(message)

if __name__ == "__main__":
    # Лечит 409 Conflict
    try:
        logger.info("Удаляю вебхук...")
        bot.remove_webhook()
        time.sleep(3)
    except Exception as e:
        logger.warning(f"Не смог удалить вебхук: {e}")

    while True:
        try:
            logger.info(f"Бот запущен. Жду посты из канала {TG_CHANNEL_ID}...")
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=True,
                allowed_updates=["channel_post", "message"]
            )
        except ApiTelegramException as e:
            if "409" in str(e):
                logger.warning(f"409 Conflict - другой инстанс еще жив, жду 10 сек: {e}")
                time.sleep(10)
                continue
            else:
                logger.exception(f"Telegram API ошибка: {e}")
                time.sleep(5)
        except Exception as e:
            logger.exception(f"Падение polling, перезапуск через 5 сек: {e}")
            time.sleep(5)