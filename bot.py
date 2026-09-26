import os
import tempfile
import logging
import time
import requests
import telebot
from telebot.types import Message

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")

if not TG_BOT_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN пустой! Проверь Variables в Railway")

bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Убиваем вебхук и зависшие getUpdates - чинит 409
try:
    logger.info("Удаляю webhook...")
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(3)
except Exception as e:
    logger.warning(f"delete_webhook: {e}")

def download_tg_file(file_id: str) -> str:
    """Скачивает любой файл Телеги до 2ГБ стримингом"""
    r = requests.get(
        f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getFile",
        params={"file_id": file_id},
        timeout=30
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"getFile failed: {data}")

    file_path = data["result"]["file_path"]
    ext = os.path.splitext(file_path)[-1] or ".mp4"

    url = f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file_path}"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.close()

    with requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        with open(tmp.name, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024): # по 1МБ
                if chunk:
                    f.write(chunk)

    logger.info(f"Скачано {tmp.name} - {os.path.getsize(tmp.name)} байт")
    return tmp.name

def send_to_max(file_path=None, caption=None):
    # ВСТАВЬ СЮДА СВОЙ КОД ОТПРАВКИ В MAX
    # Пример: requests.post(f"https://.../{MAX_BOT_TOKEN}", ...)
    pass

@bot.channel_post_handler(content_types=['photo', 'video', 'document', 'animation', 'text'])
def handle_channel_post(message: Message):
    local_path = None
    try:
        file_id = None
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
        elif message.content_type == 'video' and message.video:
            file_id = message.video.file_id
        elif message.content_type == 'document' and message.document:
            file_id = message.document.file_id
        elif message.content_type == 'animation' and message.animation:
            file_id = message.animation.file_id

        if file_id:
            local_path = download_tg_file(file_id)
            send_to_max(file_path=local_path, caption=message.caption)
        else:
            send_to_max(caption=message.text or message.caption)

    except Exception as e:
        logger.exception(f"Ошибка в handle_channel_post: {e}")
    finally:
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except:
                pass

if __name__ == "__main__":
    logger.info("Bot starting infinity_polling...")
    while True:
        try:
            # skip_pending=False важно, чтобы не делать лишний getUpdates
            bot.infinity_polling(skip_pending=False, long_polling_timeout=30, timeout=30)
        except Exception as e:
            if "409" in str(e):
                logger.error("409 Conflict - жду 30 сек пока сдохнет второй контейнер")
                time.sleep(30)
            else:
                logger.exception("Polling упал, рестарт через 5 сек")
                time.sleep(5)