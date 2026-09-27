import os, tempfile, logging, time, requests, telebot, threading, asyncio
from telebot.types import Message

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
logger.info(f"ENV: TG={'set' if TG_BOT_TOKEN else 'MISS'} MAX={'set' if MAX_BOT_TOKEN else 'MISS'} LOCAL={LOCAL_API} API_ID={'set' if API_ID else 'MISSING'}")

if not TG_BOT_TOKEN:
    raise SystemExit("TG_BOT_TOKEN missing")

bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)

album_buffer = {}
album_lock = threading.Lock()

def download_via_telethon_sync(chat_id, message_id):
    if not API_ID or not API_HASH:
        logger.warning("No API_ID/HASH for telethon")
        return None
    async def _run():
        from telethon import TelegramClient
        # use temp session file per message to avoid lock
        sess_name = f"/tmp/bot_sess_{message_id}_{int(time.time())}"
        client = TelegramClient(sess_name, int(API_ID), API_HASH)
        try:
            await client.start(bot_token=TG_BOT_TOKEN)
            logger.info(f"Telethon started, getting entity {chat_id}")
            # for private channel, need to get dialogs first to cache?
            # Try get_entity directly
            try:
                entity = await client.get_entity(chat_id)
            except Exception as e:
                logger.warning(f"get_entity {chat_id} failed: {e}, trying get_dialogs")
                # populate dialogs
                async for d in client.iter_dialogs():
                    if d.id == chat_id or getattr(d.entity, 'id', None) == abs(chat_id):
                        entity = d.entity
                        break
                else:
                    # try with PeerChannel
                    from telethon.tl.types import PeerChannel
                    # -1004241800990 -> channel id 4241800990
                    raw_id = int(str(chat_id).replace("-100", ""))
                    entity = await client.get_entity(PeerChannel(raw_id))
            
            logger.info(f"Got entity {entity}, getting msg {message_id}")
            msg = await client.get_messages(entity, ids=message_id)
            if not msg:
                logger.warning(f"Telethon: msg {message_id} not found")
                return None
            if not msg.media:
                logger.warning(f"Telethon: msg {message_id} has no media")
                return None
            
            tmp_dir = tempfile.gettempdir()
            # keep extension
            ext = ".mp4"
            if hasattr(msg.file, 'name') and msg.file.name:
                ext = os.path.splitext(msg.file.name)[1] or ".mp4"
            tmp_path = os.path.join(tmp_dir, f"tele_{message_id}{ext}")
            logger.info(f"Telethon downloading to {tmp_path}")
            path = await client.download_media(msg, file=tmp_path)
            logger.info(f"Telethon downloaded: {path} size={os.path.getsize(path) if path and os.path.exists(path) else 0}")
            return path
        finally:
            try:
                await client.disconnect()
            except:
                pass
            # cleanup session files
            try:
                for f in [sess_name + ".session", sess_name + ".session-journal"]:
                    if os.path.exists(f):
                        os.remove(f)
            except:
                pass

    # run in fresh loop to avoid "different loop" error
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_run())
        loop.close()
        return result
    except Exception as e:
        logger.exception(f"Telethon download failed: {e}")
        return None

def download_tg_file(file_id: str, chat_id=None, message_id=None):
    # 1. Try Telethon first for any file if we have chat_id - it bypasses 20MB limit and 404
    if chat_id and message_id:
        logger.info(f"Trying Telethon direct download for msg {message_id} in {chat_id}")
        path = download_via_telethon_sync(chat_id, message_id)
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        logger.warning("Telethon failed, falling back to Bot API")

    try:
        r = requests.get(f"{LOCAL_API}/bot{TG_BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=60)
        data = r.json()
        if not data.get("ok"):
            logger.warning(f"getFile failed: {data}")
            return None
        fp = data["result"]["file_path"]
        logger.info(f"getFile returned: {fp}")
        ext = os.path.splitext(fp)[-1] or ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp.close()

        candidates = []
        norm = fp.lstrip("/")
        if norm.startswith("var/lib/telegram-bot-api/"):
            tmp_norm = norm[len("var/lib/telegram-bot-api/"):]
            if "/" in tmp_norm and ":" in tmp_norm.split("/")[0]:
                candidates.append(tmp_norm.split("/", 1)[1])
            candidates.append(tmp_norm)
        candidates += [fp.lstrip("/"), fp]
        
        bases = [LOCAL_API.rstrip("/")]
        for attempt in range(2):
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
            time.sleep(2)
        return None
    except Exception as e:
        logger.exception(f"download_tg_file error: {e}")
        return None

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
            # MAX часто не принимает .MOV как video -> пробуем как file
            preferred_type = "image" if ext in [".jpg",".jpeg",".png",".webp"] else "video" if ext in [".mp4",".avi",".mkv"] else "file"
            # для .mov сразу пробуем file, но оставим fallback
            if ext == ".mov":
                preferred_type = "file"
            
            types_to_try = [preferred_type]
            if preferred_type == "file" and ext in [".mov", ".mp4"]:
                types_to_try.append("video")
            if preferred_type == "video":
                types_to_try.append("file")
            
            uploaded = False
            last_err = None
            for up_type in types_to_try:
                try:
                    logger.info(f"Uploading {path} as {up_type} (ext={ext} size={os.path.getsize(path)})")
                    r = requests.post(f"{base}/uploads", params={"type": up_type}, headers=headers, timeout=30)
                    r.raise_for_status()
                    upload_url = r.json().get("url")
                    if not upload_url:
                        raise RuntimeError(f"No upload url: {r.text}")
                    with open(path, "rb") as f:
                        r2 = requests.post(upload_url, files={"data": f}, timeout=300)
                        # логируем тело ошибки если 400
                        if r2.status_code >= 400:
                            logger.warning(f"Upload {up_type} failed {r2.status_code}: {r2.text[:500]}")
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
                    logger.info(f"Uploaded {path} as {up_type} token={token[:20]}...")
                    uploaded = True
                    break
                except Exception as e:
                    last_err = e
                    logger.warning(f"Upload as {up_type} failed: {e}, trying next")
                    continue
            
            if not uploaded:
                raise last_err or RuntimeError("All upload types failed")
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