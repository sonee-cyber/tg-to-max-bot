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

def download_tg_file(file_id: str):
    try:
        r = requests.get(f"{LOCAL_API}/bot{TG_BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=60)
        data = r.json()
        if not data.get("ok"):
            if "file is too big" in data.get("description","").lower():
                logger.warning(f"File {file_id} >20MB, нужен локальный Bot API. Пропускаю, чтобы не ломать альбом.")
                return None
            raise RuntimeError(data)
        fp = data["result"]["file_path"]
        logger.info(f"getFile returned file_path: {fp}")
        ext = os.path.splitext(fp)[-1] or ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp.close()

        # Собираем кандидатов для локального API, т.к. он возвращает абсолютный путь
        # вида /var/lib/telegram-bot-api/TOKEN/videos/file_0.MOV
        candidates = []
        candidates.append(fp)  # как есть
        candidates.append(fp.lstrip("/"))  # без ведущего /

        norm = fp.lstrip("/")
        if norm.startswith("var/lib/telegram-bot-api/"):
            norm = norm[len("var/lib/telegram-bot-api/"):]
            candidates.append(norm)  # TOKEN/videos/...
            # без префикса TOKEN/
            if "/" in norm and ":" in norm.split("/")[0]:
                candidates.append(norm.split("/", 1)[1])  # videos/...

        # Убираем дубликаты, сохраняя порядок
        seen = set()
        uniq = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                uniq.append(c)

        # Пробуем и 8081 (прямо bot-api) и 80 (nginx) — в aiogram образе nginx фиксит full-path
        bases = [LOCAL_API]
        if ":8081" in LOCAL_API:
            bases.append(LOCAL_API.replace(":8081", ""))
            bases.append(LOCAL_API.replace(":8081", ":80"))
        else:
            # если вдруг указан без порта — пробуем 8081 тоже
            bases.append(LOCAL_API.rstrip("/") + ":8081")

        last_exc = None
        for base in bases:
            for cand in uniq:
                url = f"{base}/file/bot{TG_BOT_TOKEN}/{cand}"
                # Чиним // кроме http://
                url = url.replace("://", "___PROTO___").replace("//", "/").replace("___PROTO___", "://")
                logger.info(f"Trying download: {url}")
                try:
                    with requests.get(url, stream=True, timeout=600) as resp:
                        resp.raise_for_status()
                        with open(tmp.name, "wb") as f:
                            for ch in resp.iter_content(1024*1024):
                                if ch: f.write(ch)
                    logger.info(f"Successfully downloaded via {url}")
                    return tmp.name
                except Exception as e:
                    last_exc = e
                    if "404" in str(e):
                        logger.warning(f"404 for {url}, пробую следующий вариант")
                        continue
                    logger.warning(f"Failed {url}: {e}")
                    continue

        if last_exc:
            raise last_exc
        return None
    except Exception as e:
        if "file is too big" in str(e).lower():
            logger.warning(f"File {file_id} too big - пропускаю")
            return None
        raise

def send_to_max_multi(file_paths, caption=None):
    if isinstance(file_paths, str):
        file_paths = [file_paths]
    file_paths = [p for p in (file_paths or []) if p and os.path.exists(p)]

    if not MAX_BOT_TOKEN or not MAX_CHAT_ID:
        return

    headers = {"Authorization": MAX_BOT_TOKEN.strip()}
    base = "https://platform-api.max.ru"

    attachments = []
    try:
        for path in file_paths:
            ext = os.path.splitext(path)[1].lower()
            up_type = "image" if ext in [".jpg",".jpeg",".png",".webp"] else "video" if ext in [".mp4",".mov",".avi",".mkv"] else "file"

            r = requests.post(f"{base}/uploads", params={"type": up_type}, headers=headers, timeout=30)
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
            attachments.append({"type": up_type, "payload": {"token": token}})

        payload = {"text": caption or ""}
        if attachments:
            payload["attachments"] = attachments

        if not payload["text"] and not attachments:
            return

        r3 = requests.post(f"{base}/messages", params={"chat_id": int(MAX_CHAT_ID)}, headers=headers, json=payload, timeout=30)
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
            local_path = None
            if file_id:
                try:
                    local_path = download_tg_file(file_id)
                except Exception as e:
                    logger.exception(f"Не удалось скачать файл из альбома {mgid}")
                    local_path = None

            with album_lock:
                if mgid not in album_buffer:
                    album_buffer[mgid] = {"paths": [], "caption": "", "timer": None}
                if local_path:
                    album_buffer[mgid]["paths"].append(local_path)
                if message.caption:
                    album_buffer[mgid]["caption"] = message.caption
                if message.text:
                    album_buffer[mgid]["caption"] = message.text
                if album_buffer[mgid]["timer"]:
                    album_buffer[mgid]["timer"].cancel()
                t = threading.Timer(3.5, flush_album, args=[mgid])
                album_buffer[mgid]["timer"] = t
                t.start()
            return

        local_path = None
        if file_id:
            try:
                local_path = download_tg_file(file_id)
            except Exception as e:
                logger.exception(f"Не удалось скачать файл")
                local_path = None

        text_to_send = message.caption or message.text or ""

        try:
            send_to_max_multi(local_path, text_to_send)
        finally:
            if local_path and os.path.exists(local_path):
                try: os.remove(local_path)
                except: pass
    except Exception as e:
        logger.exception(f"handle error: {e}")

if __name__ == "__main__":
    logger.info(f"Bot starting via {LOCAL_API}")
    try:
        bot.delete_webhook(drop_pending_updates=True)
        time.sleep(1)
    except:
        pass
    while True:
        try:
            updates = bot.get_updates(offset=bot.last_update_id+1 if hasattr(bot, 'last_update_id') else 0, timeout=30, long_polling_timeout=30, allowed_updates=["channel_post"])
            if updates:
                bot.process_new_updates(updates)
        except ApiTelegramException as e:
            if "409" in str(e):
                logger.warning("409 Conflict - жду 30с")
                time.sleep(30)
            else:
                time.sleep(5)
        except Exception as e:
            logger.exception("Loop error")
            time.sleep(5)