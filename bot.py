import os
import tempfile
import logging
import time
import requests
import telebot
import asyncio
from telebot.types import Message
from maxapi import Bot as MaxBot

# --- Переменные из Railway ---
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")
LOCAL_API = os.getenv("BOT_API_URL", "https://api.telegram.org").rstrip("/")

if not TG_BOT_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN не задан")

# Если используешь свой bot-api сервер для больших файлов
if "railway.internal" in LOCAL_API:
    telebot.apihelper.API_URL = LOCAL_API + "/bot{0}/{1}"
    telebot.apihelper.FILE_URL = LOCAL_API + "/file/bot{0}/{1}"

bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Удаляем webhook чтобы не было конфликта 409
try:
    logger.info("Удаляю webhook...")
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(3)
except Exception as e:
    logger.warning(f"delete_webhook: {e}")

def download_tg_file(file_id: str) -> str:
    r = requests.get(f"{LOCAL_API}/bot{TG_BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=60)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"getFile failed: {data}")
    file_path = data["result"]["file_path"]
    ext = os.path.splitext(file_path)[-1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.close()
    url = f"{LOCAL_API}/file/bot{TG_BOT_TOKEN}/{file_path}"
    with requests.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(tmp.name, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024*1024):
                if chunk:
                    f.write(chunk)
    logger.info(f"Скачано {tmp.name} - {os.path.getsize(tmp.name)} байт")
    return tmp.name

def send_to_max(file_path=None, caption=None):
    if not MAX_BOT_TOKEN or not MAX_CHAT_ID:
        logger.error("MAX_BOT_TOKEN или MAX_CHAT_ID не задан")
        return

    async def _send():
        mbot = MaxBot(token=MAX_BOT_TOKEN)
        try:
            if file_path and os.path.exists(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                if ext in [".jpg", ".jpeg", ".png", ".webp"]:
                    await mbot.send_image(file_path, chat_id=int(MAX_CHAT_ID), text=caption or "")
                else:
                    await mbot.send_file(file_path, chat_id=int(MAX_CHAT_ID), text=caption or "")
            else:
                await mbot.send_message(chat_id=int(MAX_CHAT_ID), text=caption or "")
            logger.info(f"Ушло в MAX: {caption or file_path}")
        finally:
            try:
                await mbot.close()
            except:
                pass

    # Фикс для ошибки Event loop is closed - каждый раз новый loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_send())
    except Exception as e:
        logger.exception(f"Ошибка MAX: {e}")
    finally:
        try:
            loop.close()
        except:
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
            send_to_max(local_path, message.caption)
        else:
            send_to_max(None, message.text or message.caption)

    except Exception as e:
        logger.exception(f"Ошибка в handle_channel_post: {e}")
    finally:
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except:
                pass

if __name__ == "__main__":
    logger.info(f"Bot starting via {LOCAL_API}...")
    while True:
        try:
            bot.infinity_polling(skip_pending=False, long_polling_timeout=30, timeout=30)
        except Exception as e:
            logger.exception(f"Polling error: {e}")
            time.sleep(30 if "409" in str(e) else 5)