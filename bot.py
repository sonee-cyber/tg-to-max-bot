import os, tempfile, logging, time, requests, telebot
from telebot.types import Message

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
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
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
            for ch in resp.iter_content(1024*1024):
                if ch: f.write(ch)
    return tmp.name

def send_to_max(file_path=None, caption=None):
    if not MAX_BOT_TOKEN or not MAX_CHAT_ID:
        logger.error("MAX токен/чат не задан")
        return
    try:
        # 1. Определяем тип загрузки
        ext = os.path.splitext(file_path)[1].lower() if file_path else ""
        if ext in [".jpg",".jpeg",".png",".webp"]:
            up_type = "image"
            att_type = "image"
        elif ext in [".mp4",".mov",".avi",".mkv"]:
            up_type = "video"
            att_type = "video"
        elif ext in [".mp3",".ogg",".wav",".m4a"]:
            up_type = "audio"
            att_type = "audio"
        else:
            up_type = "file"
            att_type = "file"

        attachments = []
        if file_path:
            # 2. Получаем URL для загрузки
            r = requests.post("https://botapi.max.ru/uploads", params={"access_token": MAX_BOT_TOKEN, "type": up_type}, timeout=30)
            r.raise_for_status()
            upload_url = r.json().get("url")
            if not upload_url:
                raise RuntimeError(f"No upload url: {r.text}")

            # 3. Заливаем файл на этот URL (поле называется data)
            with open(file_path, "rb") as f:
                r2 = requests.post(upload_url, files={"data": f}, timeout=120)
                r2.raise_for_status()
                j = r2.json()

            token = None
            if isinstance(j, dict):
                if "token" in j:
                    token = j["token"]
                elif "photos" in j and isinstance(j["photos"], dict):
                    # для картинок приходит {"photos": {"id": {"token": "..."}}}
                    for v in j["photos"].values():
                        if isinstance(v, dict) and "token" in v:
                            token = v["token"]
                            break
            if not token:
                raise RuntimeError(f"Нет token в ответе загрузки: {j}")

            attachments = [{"type": att_type, "payload": {"token": token}}]

        # 4. Отправляем сообщение в MAX
        payload = {"text": caption or ""}
        if attachments:
            payload["attachments"] = attachments

        r3 = requests.post("https://botapi.max.ru/messages", params={"access_token": MAX_BOT_TOKEN, "chat_id": int(MAX_CHAT_ID)}, json=payload, timeout=30)
        r3.raise_for_status()
        logger.info(f"Ушло в MAX: {caption or file_path}")

    except Exception as e:
        logger.exception(f"Ошибка MAX: {e}")

@bot.channel_post_handler(content_types=['photo','video','document','animation','text'])
def handle_channel_post(message: Message):
    local_path = None
    try:
        file_id = None
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
        elif message.video:
            file_id = message.video.file_id
        elif message.document:
            file_id = message.document.file_id
        elif message.animation:
            file_id = message.animation.file_id

        if file_id:
            local_path = download_tg_file(file_id)
            send_to_max(local_path, message.caption)
        else:
            send_to_max(None, message.text or message.caption)
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
            logger.exception(f"Polling error: {e}")
            time.sleep(30 if "409" in str(e) else 5)