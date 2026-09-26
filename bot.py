import os, tempfile, logging, time, requests, telebot, asyncio
from telebot.types import Message
from maxapi import Bot as MaxBot

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")
LOCAL_API = os.getenv("BOT_API_URL", "https://api.telegram.org").rstrip("/")

if "railway.internal" in LOCAL_API:
    telebot.apihelper.API_URL = LOCAL_API + "/bot{0}/{1}"
    telebot.apihelper.FILE_URL = LOCAL_API + "/file/bot{0}/{1}"

bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)
MAX_BOT = MaxBot(token=MAX_BOT_TOKEN) if MAX_BOT_TOKEN else None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    logger.info("Удаляю webhook...")
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(3)
except Exception as e:
    logger.warning(e)

def download_tg_file(file_id: str) -> str:
    r = requests.get(f"{LOCAL_API}/bot{TG_BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=60)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    file_path = data["result"]["file_path"]
    ext = os.path.splitext(file_path)[-1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.close()
    url = f"{LOCAL_API}/file/bot{TG_BOT_TOKEN}/{file_path}"
    with requests.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(tmp.name, "wb") as f:
            for chunk in resp.iter_content(1024*1024):
                if chunk: f.write(chunk)
    return tmp.name

def send_to_max(file_path=None, caption=None):
    if not MAX_BOT or not MAX_CHAT_ID:
        return
    async def _send():
        if file_path and os.path.exists(file_path):
            ext = os.path.splitext(file_path)[1].lower()
            if ext in [".jpg",".jpeg",".png",".webp"]:
                await MAX_BOT.send_image(file_path, chat_id=int(MAX_CHAT_ID), text=caption or "")
            else:
                await MAX_BOT.send_file(file_path, chat_id=int(MAX_CHAT_ID), text=caption or "")
        else:
            await MAX_BOT.send_message(chat_id=int(MAX_CHAT_ID), text=caption or "")
        logger.info(f"Ушло в MAX: {caption}")

    try:
        asyncio.run(_send())
    except Exception as e:
        logger.exception(f"Ошибка MAX: {e}")

@bot.channel_post_handler(content_types=['photo','video','document','animation','text'])
def handle_channel_post(message: Message):
    local_path = None
    try:
        file_id = None
        if message.content_type == 'photo': file_id = message.photo[-1].file_id
        elif message.video: file_id = message.video.file_id
        elif message.document: file_id = message.document.file_id
        elif message.animation: file_id = message.animation.file_id

        if file_id:
            local_path = download_tg_file(file_id)
            send_to_max(local_path, message.caption)
        else:
            send_to_max(None, message.text or message.caption)
    finally:
        if local_path and os.path.exists(local_path):
            try: os.remove(local_path)
            except: pass

if __name__ == "__main__":
    logger.info("Bot starting...")
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except Exception as e:
            time.sleep(30 if "409" in str(e) else 5)