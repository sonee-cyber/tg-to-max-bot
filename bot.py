import os, tempfile, logging, time, requests, telebot, threading
from telebot.types import Message
from telebot.apihelper import ApiTelegramException

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

album_buffer = {}
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
    if isinstance(file_paths, str):
        file_paths = [file_paths]
    if not file_paths:
        file_paths = []

    attachments = []
    try:
        for path in file_paths:
            if not path or not os.path.exists(path):
                continue
            ext = os.path.splitext(path)[1].lower()
            up_type = "image" if ext in [".jpg",".jpeg",".png",".webp"] else "video" if ext in [".mp4",".mov",".avi",".mkv"] else "file"
            att_type = up_type

            r = requests.post("https://botapi.max.ru/uploads", params={"access_token": MAX_BOT_TOKEN, "type": up_type}, timeout=30)
            r.raise_for_status()
            upload_url = r.json().get("url")
            if not upload_url:
                raise RuntimeError(r.text)

            with open(path, "rb") as f:
                r2 = requests.post(upload_url, files={"data": f}, timeout=120)
                r2.raise_for_status()
                j = r2.json()

            token = j.get("token")
            if not token and "photos" in j:
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
        logger.info(f"Ушло в MAX одним сообщением: {len(attachments)} файлов | {caption}")
    except Exception as e:
        logger.exception(f"Ошибка MAX: {e}")

def flush_album(mgid):
    with album_lock:
        data = album_buffer.pop(mgid, None)
    if not data:
        return
    try:
        send_to_max_multi(data["paths"], data["caption"])
    finally:
        for p in data["paths"]:
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

        if mgid:
            if not file_id:
                return
            local_path = download_tg_file(file_id)
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

        local_path = download_tg_file(file_id) if file_id else None
        try:
            send_to_max_multi(local_path, message.caption or message.text)
        finally:
            if local_path and os.path.exists(local_path):
                try: os.remove(local_path)
                except: pass
    except Exception as e:
        logger.exception(f"handle error: {e}")

if __name__ == "__main__":
    logger.info(f"Bot starting via {LOCAL_API}")
    fails_409 = 0
    # Сносим вебхук один раз при старте
    try:
        bot.delete_webhook(drop_pending_updates=True)
        time.sleep(2)
    except:
        pass

    while True:
        try:
            updates = bot.get_updates(offset=bot.last_update_id+1 if hasattr(bot, 'last_update_id') else 0, timeout=30, long_polling_timeout=30, allowed_updates=["channel_post"])
            if updates:
                bot.process_new_updates(updates)
                fails_409 = 0
        except ApiTelegramException as e:
            if "409" in str(e):
                fails_409 += 1
                wait = min(30 * fails_409, 300)
                logger.warning(f"409 Conflict - где-то еще запущен бот с этим токеном. Жду {wait}с перед повтором. Проверь что нет второго контейнера в Railway и бота на компе.")
                time.sleep(wait)
                try:
                    bot.delete_webhook(drop_pending_updates=True)
                except:
                    pass
            else:
                logger.exception("get_updates error")
                time.sleep(5)
        except Exception as e:
            logger.exception("Loop error")
            time.sleep(5)