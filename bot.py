import os, tempfile, logging, time, requests, telebot
from telebot.types import Message
from maxbot import MaxBot, PhotoAttachmentRequest, PhotoAttachmentPayload, VideoAttachmentRequest, VideoAttachmentPayload, FileAttachmentRequest, FileAttachmentPayload

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")
LOCAL_API = os.getenv("BOT_API_URL", "https://api.telegram.org").rstrip("/")

if not TG_BOT_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN пустой")

if "railway.internal" in LOCAL_API:
    telebot.apihelper.API_URL = LOCAL_API + "/bot{0}/{1}"
    telebot.apihelper.FILE_URL = LOCAL_API + "/file/bot{0}/{1}"

bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)
max_bot = MaxBot(MAX_BOT_TOKEN) if MAX_BOT_TOKEN else None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

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
        raise RuntimeError(data.get("description", str(data)))
    file_path = data["result"]["file_path"]
    ext = os.path.splitext(file_path)[-1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.close()
    file_url = f"{LOCAL_API}/file/bot{TG_BOT_TOKEN}/{file_path}"
    with requests.get(file_url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(tmp.name, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024*1024):
                if chunk: f.write(chunk)
    return tmp.name

def send_to_max(file_path=None, caption=None):
    if not max_bot or not MAX_CHAT_ID:
        logger.error("MAX токен или чат не задан")
        return
    try:
        atts = []
        if file_path and os.path.exists(file_path):
            ext = os.path.splitext(file_path)[1].lower()
            if ext in [".jpg",".jpeg",".png",".webp"]:
                up = max_bot.uploads.upload_photo_from_file(file_path)
                atts = [PhotoAttachmentRequest(payload=PhotoAttachmentPayload(token=up.token))]
            elif ext in [".mp4",".mov",".avi",".mkv"]:
                up = max_bot.uploads.upload_video_from_file(file_path)
                atts = [VideoAttachmentRequest(payload=VideoAttachmentPayload(token=up.token))]
            else:
                up = max_bot.uploads.upload_media_from_file(file_path)
                atts = [FileAttachmentRequest(payload=FileAttachmentPayload(token=up.token))]

        max_bot.messages.send(chat_id=int(MAX_CHAT_ID), text=caption or "", attachments=atts or None)
        logger.info(f"Ушло в MAX: {caption}")
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
            send_to_max(file_path=local_path, caption=message.caption)
        else:
            send_to_max(caption=message.text or message.caption)
    except Exception as e:
        logger.exception(f"Ошибка handle: {e}")
    finally:
        if local_path and os.path.exists(local_path):
            try: os.remove(local_path)
            except: pass

if __name__ == "__main__":
    logger.info(f"Bot starting via {LOCAL_API}")
    while True:
        try:
            bot.infinity_polling(skip_pending=False, long_polling_timeout=30, timeout=30)
        except Exception as e:
            if "409" in str(e): time.sleep(30)
            else: time.sleep(5)