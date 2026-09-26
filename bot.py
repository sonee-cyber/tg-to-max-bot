import os, tempfile, logging, time
import requests
import telebot
from telebot.types import Message

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")

if not TG_BOT_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN пустой! Проверь Variables в Railway")

bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ВАЖНО: Убиваем все старые getUpdates / webhook перед стартом
# Это чинит твою ошибку 409
try:
    logger.info("Удаляю webhook...")
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(3) # даем Телеге время отпустить соединение
except Exception as e:
    logger.warning(f"delete_webhook не сработал: {e}")

# --- СКАЧИВАНИЕ ЛЮБОГО РАЗМЕРА ДО 2ГБ ---
def download_tg_file(file_id: str) -> str:
    r = requests.get(
        f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getFile",
        params={"file_id": file_id},
        timeout=30
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"getFile failed: {data}")
    
    file_path = data["result"]["file_path"]
    ext = os.path.splitext(file_path)[-1] or ".mp4"
    
    url = f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file_path}"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.close()
    
    with requests.get(url, stream=True, timeout=180) as resp:
        resp.raise_for_status()
        with open(tmp.name, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024*1024):
                if chunk:
                    f.write(chunk)
    
    logger.info(f"Скачано {tmp.name} - {os.path.getsize(tmp.name)} байт")
    return tmp.name

# --- ОТПРАВКА В МАКС ---
def send_to_max(file_path=None, caption=None):
    # твоя функция отправки в MAX как была
    pass

@bot.channel_post_handler(content_types=['photo', 'video', 'document', 'animation', 'text'])
def handle_channel_post(message: Message):
    try:
        file_id = None
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
        elif message.content_type in ['video', 'document', 'animation']:
            if message.document:
                file_id = message.document.file_id
            elif message.video:
                file_id = message.video.file_id
            elif message.animation:
                file_id = message.animation.file_id

        if file_id:
            local_path = download_tg_file(file_id)
            send_to_max(file_path=local_path, caption=message.caption)
            if os.path.exists(local_path):
                os.remove(local_path)
        else:
            send_to_max(caption=message.text or message.caption)
            
    except Exception as e:
        logger.exception(e)

if __name__ == "__main__":
    logger.info("Bot starting infinity_polling...")
    # Автоперезапуск если что-то упадет
    while True:
        try:
            bot.infinity_polling(
                skip_pending=True,
                long_polling_timeout=20,
                timeout=30,
                allowed_updates=[] # слушаем все
            )
        except Exception as e:
            logger.exception(f"Polling упал, перезапуск через 5 сек: {e}")
            time.sleep(5)