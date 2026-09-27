import os, tempfile, logging, time, requests, telebot, threading, asyncio
from telebot.types import Message
from telebot.apihelper import ApiTelegramException

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")
LOCAL_API = os.getenv("BOT_API_URL", "https://api.telegram.org").rstrip("/")
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if "railway.internal" in LOCAL_API:
    telebot.apihelper.API_URL = LOCAL_API + "/bot{0}/{1}"
    telebot.apihelper.FILE_URL = LOCAL_API + "/file/bot{0}/{1}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.info(f"ENV: TG=set MAX=set LOCAL={LOCAL_API} API_ID={'set' if API_ID else 'MISSING'}")

if not TG_BOT_TOKEN:
    raise SystemExit("TG_BOT_TOKEN missing")

bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)

# Pyrogram client for big files (>20MB) - bypass Bot API file server
pyro_app = None
if API_ID and API_HASH:
    try:
        from pyrogram import Client
        pyro_app = Client("bot_session", api_id=int(API_ID), api_hash=API_HASH, bot_token=TG_BOT_TOKEN, in_memory=True)
        logger.info("Pyrogram client created for direct MTProto download")
    except Exception as e:
        logger.warning(f"Pyrogram not available: {e}")

album_buffer = {}
album_lock = threading.Lock()

async def download_via_pyrogram(chat_id, message_id):
    if not pyro_app:
        return None
    try:
        async with pyro_app:
            msg = await pyro_app.get_messages(chat_id, message_id)
            if not msg or (not msg.video and not msg.document and not msg.photo and not msg.animation):
                logger.warning(f"Pyrogram: no media in msg {message_id}")
                return None
            tmp_dir = tempfile.gettempdir()
            ext = ".mp4"
            if msg.video and msg.video.file_name:
                ext = os.path.splitext(msg.video.file_name)[1] or ".mp4"
            elif msg.document and msg.document.file_name:
                ext = os.path.splitext(msg.document.file_name)[1] or ".mp4"
            tmp_path = os.path.join(tmp_dir, f"pyro_{message_id}{ext}")
            path = await pyro_app.download_media(msg, file_name=tmp_path)
            logger.info(f"Pyrogram downloaded: {path} size={os.path.getsize(path) if path and os.path.exists(path) else 0}")
            return path
    except Exception as e:
        logger.exception(f"Pyrogram download failed: {e}")
        return None

def download_via_pyrogram_sync(chat_id, message_id):
    try:
        return asyncio.run(download_via_pyrogram(chat_id, message_id))
    except RuntimeError:
        # if loop already running
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(download_via_pyrogram(chat_id, message_id))

def download_tg_file(file_id: str, chat_id=None, message_id=None):
    # Try direct MTProto first for big files if we have chat_id
    if chat_id and message_id and pyro_app:
        logger.info(f"Trying Pyrogram direct download for msg {message_id} in {chat_id}")
        path = download_via_pyrogram_sync(chat_id, message_id)
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            return path

    try:
        r = requests.get(f"{LOCAL_API}/bot{TG_BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=60)
        data = r.json()
        if not data.get("ok"):
            if "file is too big" in data.get("description","").lower():
                # fallback to pyrogram
                if chat_id and message_id and pyro_app:
                    return download_via_pyrogram_sync(chat_id, message_id)
                logger.warning(f"File {file_id} >20MB and no pyrogram fallback")
                return None
            raise RuntimeError(data)
        fp = data["result"]["file_path"]
        logger.info(f"getFile returned: {fp}")
        ext = os.path.splitext(fp)[-1] or ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp.close()

        # Try local disk (if volume exists)
        for dp in [fp, "/" + fp.lstrip("/"), fp.lstrip("/")]:
            if os.path.exists(dp) and os.path.getsize(dp) > 0:
                import shutil
                shutil.copyfile(dp, tmp.name)
                logger.info(f"Copied from disk {dp}")
                return tmp.name

        # Try http from local api
        candidates = []
        norm = fp.lstrip("/")
        if norm.startswith("var/lib/telegram-bot-api/"):
            tmp_norm = norm[len("var/lib/telegram-bot-api/"):]
            if "/" in tmp_norm and ":" in tmp_norm.split("/")[0]:
                candidates.append(tmp_norm.split("/", 1)[1])
            candidates.append(tmp_norm)
        candidates += [fp.lstrip("/"), fp]
        
        bases = [LOCAL_API.rstrip("/")]
        for attempt in range(3):
            for base in bases:
                for cand in candidates:
                    url = f"{base}/file/bot{TG_BOT_TOKEN}/{cand}".replace(f"/bot{TG_BOT_TOKEN}//", f"/bot{TG_BOT_TOKEN}/")
                    logger.info(f"Trying download: {url}")
                    try:
                        with requests.get(url, stream=True, timeout=300) as resp:
                            resp.raise_for_status()
                            with open(tmp.name, "wb") as f:
                                for ch in resp.iter_content(1024*1024):
                                    if ch: f.write(ch)
                            if os.path.getsize(tmp.name) > 0:
                                logger.info(f"Success via {url}")
                                return tmp.name
                    except Exception as e:
                        logger.warning(f"Fail {url}: {e}")
                        continue
            time.sleep(3)

        # last resort pyrogram
        if chat_id and message_id and pyro_app:
            logger.info("All http failed, trying Pyrogram final")
            return download_via_pyrogram_sync(chat_id, message_id)

        return None
    except Exception as e:
        logger.exception(f"download_tg_file error: {e}")
        if chat_id and message_id and pyro_app:
            return download_via_pyrogram_sync(chat_id, message_id)
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
            with open(path, "rb") as f:
                r2 = requests.post(upload_url, files={"data": f}, timeout=180)
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
        logger.info(f"Ушло в MAX: {len(attachments)} файлов | {caption}")
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
    logger.info(f"Пост: type={message.content_type} id={message.message_id} chat={message.chat.id} has_video={bool(message.video)} mgid={getattr(message, 'media_group_id', None)}")
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
                    local_path = download_tg_file(file_id, message.chat.id, message.message_id)
                except Exception:
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
                local_path = download_tg_file(file_id, message.chat.id, message.message_id)
            except Exception as e:
                logger.exception(f"Не удалось скачать файл")

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
        except Exception as e:
            if "409" in str(e):
                time.sleep(30)
            else:
                time.sleep(5)