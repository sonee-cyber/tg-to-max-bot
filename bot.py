import os, tempfile, logging, time, requests, telebot, threading
from telebot.types import Message

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")
LOCAL_API = os.getenv("BOT_API_URL", "https://api.telegram.org").rstrip("/")

if "railway.internal" in LOCAL_API:
    telebot.apihelper.API_URL = LOCAL_API + "/bot{0}/{1}"
    telebot.apihelper.FILE_URL = LOCAL_API + "/file/bot{0}/{1}"

bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

try:
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(3)
except:
    pass

# --- буфер для альбомов ---
album_buffer = {} # media_group_id -> {"paths": [], "caption": "", "timer": Timer}
album_lock = threading.Lock()

def download_tg_file(file_id: str) -> str:
    r = requests.get(f"{LOCAL_API}/bot{TG_BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=60)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    fp = data["result"]["file_path"]
    ext = os.path.splitext(fp)[-1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.close()
    url = f"{LOCAL_API}/file/bot{TG_BOT_TOKEN}/{fp}"
    with requests.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(tmp.name, "wb") as f:
            for ch in resp.iter_content(1024*1024):
                if ch: f.write(ch)
    return tmp.name

def send_to_max_multi(file_paths, caption=None):
    if not file_paths:
        file_paths = []
    if isinstance(file_paths, str):
        file_paths = [file_paths]

    attachments = []
    try:
        for path in file_paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in [".jpg",".jpeg",".png",".webp"]:
                up_type, att_type = "image", "image"
            elif ext in [".mp4",".mov",".avi",".mkv"]:
                up_type, att_type = "video", "video"
            else:
                up_type, att_type = "file", "file"

            r = requests.post("https://botapi.max.ru/uploads", params={"access_token": MAX_BOT_TOKEN, "type": up_type}, timeout=30)
            r.raise_for_status()
            upload_url = r.json().get("url")
            if not upload_url:
                raise RuntimeError(r.text)

            with open(path, "rb") as f:
                r2 = requests.post(upload_url, files={"data": f}, timeout=120)
                r2.raise_for_status()
                j = r2.json()

            token = None
            if "token" in j:
                token = j["token"]
            elif "photos" in j:
                for v in j["photos"].values():
                    if isinstance(v, dict) and "token" in v:
                        token = v["token"]
                        break
            if not token:
                raise RuntimeError(f"No token: {j}")

            attachments.append({"type": att_type, "payload": {"token": token}})

        payload = {"text": caption or ""}
        if attachments:
            payload["attachments"] = attachments

        r3 = requests.post("https://botapi.max.ru/messages", params={"access_token": MAX_BOT_TOKEN, "chat_id": int(MAX_CHAT_ID)}, json=payload, timeout=30)
        r3.raise_for_status()
        logger.info(f"Ушло в MAX одним сообщением: {len(attachments)} файлов, caption={caption}")
    except Exception as e:
        logger.exception(f"Ошибка MAX: {e}")

def flush_album(mgid):
    with album_lock:
        data = album_buffer.pop(mgid, None)
    if not data:
        return
    paths = data["paths"]
    caption = data["caption"]
    try:
        send_to_max_multi(paths, caption)
    finally:
        for p in paths:
            try: os.remove(p)
            except: pass

@bot.channel_post_handler(content_types=['photo','video','document','animation','text'])
def handle_channel_post(message: Message):
    try:
        mgid = getattr(message, "media_group_id", None)

        file_id = None
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
        elif message.video:
            file_id = message.video.file_id
        elif message.document:
            file_id = message.document.file_id
        elif message.animation:
            file_id = message.animation.file_id

        # Альбом - копим
        if mgid:
            local_path = download_tg_file(file_id) if file_id else None
            if not local_path:
                return
            with album_lock:
                if mgid not in album_buffer:
                    album_buffer[mgid] = {"paths": [], "caption": "", "timer": None}
                album_buffer[mgid]["paths"].append(local_path)
                if message.caption:
                    album_buffer[mgid]["caption"] = message.caption
                if album_buffer[mgid]["timer"]:
                    album_buffer[mgid]["timer"].cancel()
                t = threading.Timer(2.5, flush_album, args=[mgid])
                album_buffer[mgid]["timer"] = t
                t.start()
            return

        # Одиночное сообщение
        local_path = download_tg_file(file_id) if file_id else None
        try:
            send_to_max_multi(local_path, message.caption or message.text)
        finally:
            if local_path and os.path.exists(local_path):
                try: os.remove(local_path)
                except: pass

    except Exception as e:
        logger.exception(f"Ошибка handle: {e}")

if __name__ == "__main__":
    logger.info(f"Bot starting via {LOCAL_API}")
    while True:
        try:
            bot.infinity_polling(skip_pending=False, timeout=30, long_polling_timeout=30)
        except Exception as e:
            logger.exception(f"Polling error: {e}")
            time.sleep(30 if "409" in str(e) else 5)