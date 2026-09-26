import os, time, logging, tempfile, threading
import telebot
from dotenv import load_dotenv
from telebot.apihelper import ApiTelegramException
import formatter, max_api

load_dotenv()
TG_BOT_TOKEN=os.getenv("TG_BOT_TOKEN")
TG_CHANNEL_ID=int(os.getenv("TG_CHANNEL_ID","0") or 0)
MAX_CHAT_ID=os.getenv("MAX_CHAT_ID")
if not TG_BOT_TOKEN or not TG_CHANNEL_ID or not MAX_CHAT_ID:
    raise RuntimeError("Не заданы TG_BOT_TOKEN / TG_CHANNEL_ID / MAX_CHAT_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger=logging.getLogger(__name__)
bot=telebot.TeleBot(TG_BOT_TOKEN, threaded=False)

ALBUM_DELAY=2.5
album_buffer={}
album_lock=threading.Lock()

class FileTooBig(Exception): pass

def get_text_and_entities(msg):
    if msg.content_type=='text': return msg.text or "", msg.entities
    else: return msg.caption or "", msg.caption_entities

def download_tg_file(file_id: str) -> str:
    try:
        file_info=bot.get_file(file_id)
    except ApiTelegramException as e:
        if "file is too big" in str(e).lower():
            raise FileTooBig(str(e))
        raise
    downloaded=bot.download_file(file_info.file_path)
    suffix=os.path.splitext(file_info.file_path)[-1] or ".tmp"
    tmp=tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(downloaded); tmp.close()
    return tmp.name

def process_album(messages):
    if not messages: return
    # текст берем из первого с подписью
    cap=""
    for m in messages:
        t,ents=get_text_and_entities(m)
        if t:
            cap=formatter.format_telegram_to_max(t,ents)
            break
    photo_paths=[]; video_paths=[]; tmp_all=[]
    for m in messages:
        try:
            if m.content_type=='photo':
                fid=m.photo[-1].file_id
                p=download_tg_file(fid); photo_paths.append(p); tmp_all.append(p)
            elif m.content_type=='video':
                fid=m.video.file_id
                p=download_tg_file(fid); video_paths.append(p); tmp_all.append(p)
        except FileTooBig:
            logger.warning("Видео из альбома >20МБ, пропускаю")
        except Exception as e:
            logger.exception(f"Ошибка скачивания из альбома: {e}")
    try:
        if photo_paths and not video_paths:
            max_api.send_photos(MAX_CHAT_ID, photo_paths, cap)
            logger.info(f"Альбом {len(photo_paths)} фото отправлен в МАКС")
        elif video_paths and not photo_paths:
            if len(video_paths)==1:
                max_api.send_video(MAX_CHAT_ID, video_paths[0], cap)
            else:
                max_api.send_videos(MAX_CHAT_ID, video_paths, cap)
        elif photo_paths or video_paths:
            # смешанный - сначала фото
            if photo_paths:
                max_api.send_photos(MAX_CHAT_ID, photo_paths, cap)
            for vp in video_paths:
                max_api.send_video(MAX_CHAT_ID, vp, "")
    finally:
        for p in tmp_all:
            try: os.remove(p)
            except: pass

def album_watcher():
    while True:
        time.sleep(1)
        now=time.time()
        to_flush=[]
        with album_lock:
            for mgid,data in list(album_buffer.items()):
                if now-data["last_update"]>ALBUM_DELAY:
                    to_flush.append(mgid)
        for mgid in to_flush:
            with album_lock:
                data=album_buffer.pop(mgid,None)
            if data:
                process_album(data["messages"])

@bot.channel_post_handler(content_types=['text','photo','video','document','animation','audio','voice'])
def handle_channel_post(message):
    if message.chat.id!=TG_CHANNEL_ID: return
    mgid=getattr(message,'media_group_id',None)
    if mgid:
        with album_lock:
            if mgid not in album_buffer:
                album_buffer[mgid]={"messages":[],"last_update":time.time()}
            album_buffer[mgid]["messages"].append(message)
            album_buffer[mgid]["last_update"]=time.time()
            logger.info(f"Буфер альбома {mgid}: {len(album_buffer[mgid]['messages'])}")
        return

    try:
        text,ents=get_text_and_entities(message)
        formatted=formatter.format_telegram_to_max(text,ents)
        logger.info(f"🔥 ПОЙМАЛ ПОСТ {message.chat.id}: type={message.content_type} caption={text[:100]}")

        if message.content_type=='text':
            max_api.send_text(MAX_CHAT_ID, formatted)
        elif message.content_type=='photo':
            file_id=message.photo[-1].file_id
            path=download_tg_file(file_id)
            try: max_api.send_photo(MAX_CHAT_ID, path, formatted)
            finally: os.remove(path)
        elif message.content_type=='video':
            try:
                file_id=message.video.file_id
                path=download_tg_file(file_id)
                try: max_api.send_video(MAX_CHAT_ID, path, formatted)
                finally: os.remove(path)
            except FileTooBig:
                max_api.send_text(MAX_CHAT_ID, formatted + "\n\n⚠️ Видео >20МБ — бот ТГ не может скачать. Смотри оригинал в ТГ-канале.")
                logger.warning("Видео too big, отправил заглушку")
        elif message.content_type in ('document','animation','audio','voice'):
            file_id=(message.document or message.animation or message.audio or message.voice).file_id
            path=download_tg_file(file_id)
            try: max_api.send_document(MAX_CHAT_ID, path, formatted)
            finally: os.remove(path)
    except Exception as e:
        logger.exception(f"Ошибка при пересылке: {e}")

if __name__=="__main__":
    try: bot.remove_webhook(); time.sleep(3)
    except Exception as e: logger.warning(f"webhook: {e}")
    threading.Thread(target=album_watcher, daemon=True).start()
    while True:
        try:
            logger.info(f"Бот запущен. Жду {TG_CHANNEL_ID}...")
            bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True, allowed_updates=["channel_post","message"])
        except ApiTelegramException as e:
            if "409" in str(e): time.sleep(10); continue
            time.sleep(5)
        except Exception as e:
            logger.exception(f"Падение polling: {e}"); time.sleep(5)