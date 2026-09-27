import os, tempfile, logging, time, requests, telebot, threading, asyncio, subprocess, shutil
from telebot.types import Message

def convert_to_mp4_if_needed(src_path):
    """Для твоих 1 мин 1080p 30fps ~30МБ .MOV HEVC -> H264 mp4 чтобы MAX принял как video с плеером"""
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in [".mov", ".avi", ".mkv", ".mp4", ".webm"]:
        return src_path
    size_mb = os.path.getsize(src_path) / (1024*1024)
    if size_mb > 80:
        crf = "28"
        extra_vf = ["-vf", "scale=-2:720"]
    else:
        crf = "23"
        extra_vf = []
    
    dst_path = os.path.splitext(src_path)[0] + "_h264.mp4"
    ffmpeg_exe = "ffmpeg"
    try:
        try:
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            logger.info(f"Using imageio-ffmpeg: {ffmpeg_exe}")
        except:
            pass
        # ВАЖНО: -pix_fmt yuv420p -profile:v high для совместимости с MAX плеером
        # иначе HEVC из телеги дает yuv420p10le и MAX не ест
        cmd = [ffmpeg_exe, "-y", "-i", src_path, "-c:v", "libx264", "-preset", "fast", "-crf", crf, "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"] + extra_vf + [dst_path]
        logger.info(f"Converting {src_path} -> {dst_path} via ffmpeg...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and os.path.exists(dst_path) and os.path.getsize(dst_path) > 1000:
            orig = os.path.getsize(src_path)
            new = os.path.getsize(dst_path)
            logger.info(f"Converted OK: {orig} -> {new} bytes ({orig/new:.1f}x)")
            # удаляем оригинал чтобы не забивать диск
            try:
                os.remove(src_path)
            except:
                pass
            return dst_path
        else:
            logger.warning(f"ffmpeg failed code={result.returncode}: {result.stderr[:2000]}")
            # удаляем битый dst
            try:
                if os.path.exists(dst_path):
                    os.remove(dst_path)
            except:
                pass
            return src_path
    except Exception as e:
        logger.warning(f"Convert error (ffmpeg not available?): {e}")
        return src_path

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
            # определяем расширение правильно для фото и видео
            ext = ".mp4"
            if hasattr(msg.file, 'name') and msg.file.name:
                ext = os.path.splitext(msg.file.name)[1] or ".mp4"
            elif hasattr(msg.file, 'ext') and msg.file.ext:
                ext = "." + msg.file.ext.lstrip(".")
            elif hasattr(msg.file, 'mime_type') and msg.file.mime_type:
                mime = msg.file.mime_type.lower()
                if "jpeg" in mime or "jpg" in mime:
                    ext = ".jpg"
                elif "png" in mime:
                    ext = ".png"
                elif "webp" in mime:
                    ext = ".webp"
                elif "mp4" in mime or "quicktime" in mime or "mov" in mime:
                    ext = ".mp4" if "mp4" in mime else ".mov"
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
            # конвертим MOV -> mp4 H264 для MAX video
            path = convert_to_mp4_if_needed(path)
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
            # для видео - ТОЛЬКО как video (никаких файлов, как просишь)
            if ext in [".jpg",".jpeg",".png",".webp"]:
                types_to_try = ["image"]
            elif ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
                types_to_try = ["video"]  # только video, без fallback на file
            else:
                types_to_try = ["file"]
            
            uploaded = False
            last_err = None
            # для CDN важно имя файла с маленькой буквы и правильное расширение
            base_name = os.path.basename(path)
            # .MOV -> .mp4 для совместимости CDN omub.okcdn.ru
            if ext == ".mov":
                base_name = os.path.splitext(base_name)[0] + ".mp4"
            
            for up_type in types_to_try:
                for field_name in ["data", "file"]:
                    try:
                        mime = "video/mp4" if up_type == "video" else "application/octet-stream"
                        logger.info(f"Uploading {path} as {up_type} field={field_name} name={base_name} mime={mime} size={os.path.getsize(path)}")
                        r = requests.post(f"{base}/uploads", params={"type": up_type}, headers=headers, timeout=30)
                        r.raise_for_status()
                        first_json = r.json()
                        upload_url = first_json.get("url")
                        first_token = first_json.get("token")  # для video токен тут!
                        if not upload_url:
                            raise RuntimeError(f"No upload url: {r.text}")
                        logger.info(f"First response {up_type}: url={upload_url[:100]}... token={str(first_token)[:20]}...")
                        with open(path, "rb") as f:
                            files = {field_name: (base_name, f, mime)}
                            r2 = requests.post(upload_url, files=files, timeout=300)
                            logger.info(f"Upload response {up_type}/{field_name}: status={r2.status_code} text={r2.text[:2000]}")
                            if r2.status_code >= 400:
                                logger.warning(f"Upload {up_type}/{field_name} failed {r2.status_code}: {r2.text[:800]}")
                            r2.raise_for_status()
                            text = r2.text.strip()
                            # для video токен из первого запроса, а второй отвечает XML <retval>1</retval>
                            if up_type == "video":
                                if "<retval>1</retval>" in text or "<retval>" in text:
                                    token = first_token
                                    if not token:
                                        raise RuntimeError(f"No token in first response for video: {first_json}")
                                    attachments.append({"type": "video", "payload": {"token": token}})
                                    logger.info(f"Uploaded {path} as video token={token[:20]}... (from first response)")
                                    uploaded = True
                                    break
                                # если вдруг JSON
                                try:
                                    j2 = r2.json()
                                    token = j2.get("token") or first_token
                                except:
                                    token = first_token
                                if token:
                                    attachments.append({"type": "video", "payload": {"token": token}})
                                    uploaded = True
                                    break
                            # для file/image токен из второго ответа
                            if "<retval>" in text:
                                raise RuntimeError(f"Unexpected XML response for {up_type}: {text}")
                            try:
                                j = r2.json()
                            except Exception as je:
                                logger.warning(f"JSON parse failed for {up_type}/{field_name}: {je} raw={text[:2000]}")
                                raise
                            
                            token = j.get("token") if j else None
                            if not token and j and "photos" in j:
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
                        logger.warning(f"Upload as {up_type}/{field_name} failed: {e}")
                        continue
                if uploaded:
                    break
            
            if not uploaded:
                raise last_err or RuntimeError("All upload types failed")
        if not attachments:
            return
        
        # собираем все токены в один payload - поддерживаем миксы фото+видео
        payload = {}
        if caption:
            payload["text"] = caption
        payload["attachments"] = attachments  # до 10 вложений (фото+видео) в одном сообщении
        if not payload.get("text"):
            payload["text"] = " "

        logger.info(f"Sending to MAX chat {MAX_CHAT_ID}: {len(attachments)} attachments ({', '.join([a['type'] for a in attachments])}) caption={caption[:50] if caption else ''}")

        # ретраи - MAX долго транскодит видео 25МБ, до 90 сек
        for attempt in range(20):
            r3 = requests.post(f"{base}/messages", params={"chat_id": int(MAX_CHAT_ID)}, headers=headers, json=payload, timeout=30)
            if r3.status_code >= 400:
                txt = r3.text.lower()
                logger.warning(f"MAX send failed {r3.status_code}: {r3.text[:2000]} attempt={attempt}")
                if any(x in txt for x in ["not.ready", "not.processed", "not.owner", "service.unavailable", "attachment.video", "attachment.movie", "process.attachment"]) or r3.status_code in [404, 502,503,504]:
                    wait = 10
                    logger.info(f"Video not ready yet, waiting {wait}s... ({attempt+1}/20)")
                    time.sleep(wait)
                    continue
            r3.raise_for_status()
            logger.info(f"Ушло в MAX: {len(attachments)} вложений ({', '.join([a['type'] for a in attachments])}) | {caption[:100] if caption else ''}")
            return
        raise RuntimeError(f"Не удалось отправить в MAX после всех попыток")
    except Exception as e:
        logger.exception(f"Ошибка MAX: {e}")

def flush_album(mgid):
    with album_lock:
        data = album_buffer.pop(mgid, None)
    if not data:
        return
    # теперь в data хранятся file_id, а не пути - качаем только сейчас, после сбора всего альбома
    file_items = data.get("items", [])
    caption = data.get("caption", "")
    logger.info(f"Flushing album {mgid}: {len(file_items)} items caption={caption[:100] if caption else ''}")
    paths = []
    try:
        for item in file_items:
            fid = item.get("file_id")
            cid = item.get("chat_id")
            mid = item.get("message_id")
            if not fid:
                continue
            try:
                p = download_tg_file(fid, cid, mid)
                if p and os.path.exists(p):
                    paths.append(p)
                    logger.info(f"Album {mgid} downloaded {p} size={os.path.getsize(p)}")
            except Exception as e:
                logger.warning(f"Album {mgid} download failed for {mid}: {e}")
        logger.info(f"Album {mgid} ready to send: {len(paths)} files")
        if paths:
            send_to_max_multi(paths, caption)
    finally:
        for p in paths:
            try: os.remove(p)
            except: pass

@bot.channel_post_handler(content_types=['photo','video','document','animation','video_note','voice','text'])
def handle_channel_post(message: Message):
    logger.info(f"Пост: type={message.content_type} id={message.message_id} chat={message.chat.id} has_video={bool(message.video)} has_video_note={bool(getattr(message, 'video_note', None))} mgid={getattr(message, 'media_group_id', None)}")
    try:
        mgid = getattr(message, "media_group_id", None)
        file_id = None
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
        elif message.video:
            file_id = message.video.file_id
        elif getattr(message, 'video_note', None):
            file_id = message.video_note.file_id
        elif getattr(message, 'voice', None):
            file_id = message.voice.file_id
        elif message.document:
            file_id = message.document.file_id
        elif message.animation:
            file_id = message.animation.file_id

        if mgid:
            # для альбома НЕ качаем сразу, а только сохраняем file_id - иначе 10 видео по 45 сек скачивания сломают таймер
            with album_lock:
                if mgid not in album_buffer:
                    album_buffer[mgid] = {"items": [], "caption": "", "timer": None}
                if file_id:
                    album_buffer[mgid]["items"].append({
                        "file_id": file_id,
                        "chat_id": message.chat.id,
                        "message_id": message.message_id
                    })
                if message.caption:
                    album_buffer[mgid]["caption"] = message.caption
                if message.text:
                    album_buffer[mgid]["caption"] = message.text
                if album_buffer[mgid]["timer"]:
                    album_buffer[mgid]["timer"].cancel()
                # для 10 видео таймер 12 сек - достаточно чтобы ТГ прислал все 10 частей альбома (они приходят за 1-2 сек)
                # скачивание начнется только после этого
                t = threading.Timer(12.0, flush_album, args=[mgid])
                album_buffer[mgid]["timer"] = t
                t.start()
                logger.info(f"Album {mgid} buffered: {len(album_buffer[mgid]['items'])} items, timer reset to 12s")
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