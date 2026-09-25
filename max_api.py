import os
import logging
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://platform-api.max.ru"
TOKEN = os.getenv("MAX_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("MAX_BOT_TOKEN не задан")

HEADERS = {"Authorization": TOKEN}

def _get_upload_url(upload_type: str) -> str:
    r = requests.post(f"{BASE_URL}/uploads?type={upload_type}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["url"]

def _upload(file_path: str, upload_type: str):
    url = _get_upload_url(upload_type)
    with open(file_path, "rb") as f:
        files = {"data": (os.path.basename(file_path), f)}
        r = requests.post(url, files=files, timeout=60)
        r.raise_for_status()
        data = r.json()
        logger.info(f"MAX upload raw: {str(data)[:200]}...")
        return data

def _send(chat_id: str, text: str = "", attachments=None):
    params = {"chat_id": chat_id}
    payload = {}
    if text:
        payload["text"] = text[:4000]
        payload["format"] = "markdown"
    if attachments:
        payload["attachments"] = attachments

    r = requests.post(f"{BASE_URL}/messages", params=params, headers=HEADERS, json=payload, timeout=30)
    if r.status_code != 200:
        logger.error(f"MAX API ошибка {r.status_code}: {r.text}")
    r.raise_for_status()
    return r.json()

def send_text(chat_id: str, text: str):
    return _send(chat_id, text=text)

def send_photo(chat_id: str, photo_path: str, caption: str = ""):
    data = _upload(photo_path, "image")
    # data = {"photos": {"id": {"token": "..."}}} - так возвращает MAX для фото
    if "photos" in data:
        payload = {"photos": data["photos"]}
    else:
        payload = {"token": data.get("token")}
    att = [{"type": "image", "payload": payload}]
    return _send(chat_id, text=caption, attachments=att)

def send_video(chat_id: str, video_path: str, caption: str = ""):
    data = _upload(video_path, "video")
    token = data.get("token")
    att = [{"type": "video", "payload": {"token": token}}]
    return _send(chat_id, text=caption, attachments=att)

def send_document(chat_id: str, doc_path: str, caption: str = ""):
    data = _upload(doc_path, "file")
    token = data.get("token")
    att = [{"type": "file", "payload": {"token": token}}]
    return _send(chat_id, text=caption, attachments=att)