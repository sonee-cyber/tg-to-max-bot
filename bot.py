import os, tempfile, logging, time
import requests
import telebot
from telebot.types import Message

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID")

bot = telebot.TeleBot(TG_BOT_TOKEN, threaded=False)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- СКАЧИВАНИЕ ЛЮБОГО РАЗМЕРА ДО 2ГБ ---

def download_tg_file(file_id: str) -> str:
    # 1. получаем file_path напрямую через Bot API
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
    
    # 2. качаем стримингом
    url = f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file_path}"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.close()
    
    with requests.get(url, stream=True, timeout=180) as resp:
        resp.raise_for_status()
        with open(tmp.name, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024*1024): # по 1мб
                if chunk:
                    f.write(chunk)
    
    logger.info(f"Скачано {tmp.name} - {os.path.getsize(tmp.name)} байт")
    return tmp.name

# --- ОТПРАВКА В МАКС ---
def send_to_max(file_path=None, caption=None):
    # твоя функция отправки в MAX как была, не меняй
    # тут должен быть твой код отправки через MAX API
    pass

# --- ТВОЙ СТАРЫЙ ОБРАБОТЧИК КАНАЛА ---
# Оставь его, только внутри где было bot.get_file / bot.download_file
# замени на download_tg_file(file_id)

@bot.channel_post_handler(content_types=['photo', 'video', 'document', 'animation', 'text'])
def handle_channel_post(message: Message):
    try:
        file_id = None
        if message.content_type == 'photo':
            file_id = message.photo[-1].file_id
        elif message.content_type in ['video', 'document', 'animation']:
            file_id = message.document.file_id if message.document else message.video.file_id if message.video else message.animation.file_id

        if file_id:
            local_path = download_tg_file(file_id) # <--- ВОТ ЭТОТ ВЫЗОВ
            send_to_max(file_path=local_path, caption=message.caption)
            os.remove(local_path)
        else:
            send_to_max(caption=message.text or message.caption)
            
    except Exception as e:
        logger.exception(e)

bot.infinity_polling()