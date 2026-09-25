import os
import logging
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://platform-api.max.ru"
TOKEN = os.getenv("MAX_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("MAX_BOT_TOKEN не задан")

HEADERS = {"Authorization": TOKEN}

def _upload(file_path: str, upload_type: str = "image") -> str:
    """
    3 шага по доке: POST /uploads?type=... -> получаем url -> POST файл туда -> получаем token
    """
    # 1. Получаем URL для загрузки
    r1 = requests.post(f"{BASE_URL}/uploads?type={upload_type}", headers=HEADERS, timeout=30)
    r1.raise_for_status()
    data1 = r1.json()
    upload_url = data1.get("url")
    if not upload_url:
        raise RuntimeError(f"Не пришел url для загрузки: {data1}")

    # 2. Заливаем файл на этот url
    with open(file_path, "rb") as f:
        files = {"data": (os.path.basename(file_path), f)}
        r2 = requests.post(upload_url, files=files, timeout=60)
        r2.raise_for_status()
        data2 = r2.json()
        token = data2.get("token") or data2.get("payload", {}).get("token")
        if not token:
            # бывает возвращает сразу в другом формате
            token = data2.get("id") or str(data2)
        logger.info(f"Файл загружен в MAX, token={str(token)[:20]}...")
        return token

def _send(chat_id: str, text: str = "", attachments=None):
    params = {"chat_id": chat_id}
    payload = {}
    if text:
        payload["text"] = text[:4000]  # лимит MAX
        payload["format"] = "markdown"
    if attachments:
        payload["attachments"] = attachments

    r = requests.post(f"{BASE_URL}/messages", params=params, headers=HEADERS, json=payload, timeout=30)
    if r.status_code != 200:
        logger.error(f"MAX API ошибка {r.status_code}: {r.text}")
    r.raise_for_status()
    logger.info(f"Отправлено в MAX chat {chat_id}: {text[:100]}")
    return r.json()

def send_text(chat_id: str, text: str):
    return _send(chat_id, text=text)

def send_photo(chat_id: str, photo_path: str, caption: str = ""):
    token = _upload(photo_path, "image")
    att = [{"type": "image", "payload": {"token": token}}]
    return _send(chat_id, text=caption, attachments=att)

def send_video(chat_id: str, video_path: str, caption: str = ""):
    token = _upload(video_path, "video")
    att = [{"type": "video", "payload": {"token": token}}]
    return _send(chat_id, text=caption, attachments=att)

def send_document(chat_id: str, doc_path: str, caption: str = ""):
    token = _upload(doc_path, "file")
    att = [{"type": "file", "payload": {"token": token}}]
    return _send(chat_id, text=caption, attachments=att)