"""
Обёртка над MAX Bot API (botapi.max.ru)
Документация: https://dev.max.ru/docs/
"""
import requests
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

MAX_API_BASE = "https://botapi.max.ru"
TIMEOUT = 30

class MaxAPI:
    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "tg-to-max-mirror/1.0"})

    def _request(self, method: str, http_method: str = "POST", **kwargs) -> Dict[str, Any]:
        url = f"{MAX_API_BASE}/{method}"
        # Явно добавляем access_token - надежнее чем session.params
        params = kwargs.pop("params", {}) or {}
        params["access_token"] = self.token

        try:
            if http_method == "GET":
                resp = self.session.get(url, params=params, timeout=TIMEOUT, **kwargs)
            else:
                resp = self.session.post(url, params=params, timeout=TIMEOUT, **kwargs)

            if not resp.ok:
                logger.error(f"MAX API error [{method}] {resp.status_code}: {resp.text[:500]}")
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except requests.RequestException as e:
            logger.error(f"MAX API error [{method}]: {e}")
            # покажем тело ответа если есть
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text[:500]}")
            return {}

    def send_message(self, chat_id: str, text: str, link: Optional[dict] = None) -> dict:
        payload = {"text": text, "format": "markdown"} # важно для форматирования из Телеги
        if link:
            payload["link"] = link
        return self._request("messages", http_method="POST", params={"chat_id": chat_id}, json=payload)

    def _get_upload_url(self, type_: str) -> Optional[str]:
        info = self._request("uploads", http_method="GET", params={"type": type_})
        url = info.get("url")
        if not url:
            logger.error(f"Не удалось получить URL для загрузки {type_}: {info}")
        return url

    def _upload_file(self, upload_url: str, file_bytes: bytes, filename: str, mime: str) -> Optional[str]:
        try:
            resp = requests.post(
                upload_url,
                files={"data": (filename, file_bytes, mime)},
                timeout=60
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token") or data.get("payload", {}).get("token")
            if not token:
                logger.error(f"Upload не вернул token: {data}")
            return token
        except requests.RequestException as e:
            logger.error(f"Ошибка загрузки файла {filename}: {e}")
            return None

    def upload_and_send_photo(self, chat_id: str, photo_bytes: bytes, caption: str = "") -> dict:
        upload_url = self._get_upload_url("image")
        if not upload_url: return {}
        token = self._upload_file(upload_url, photo_bytes, "photo.jpg", "image/jpeg")
        if not token: return {}

        payload = {"attachments": [{"type": "image", "payload": {"token": token}}]}
        if caption: payload["text"] = caption
        if caption: payload["format"] = "markdown"
        return self._request("messages", params={"chat_id": chat_id}, json=payload)

    def upload_and_send_video(self, chat_id: str, video_bytes: bytes, caption: str = "") -> dict:
        upload_url = self._get_upload_url("video")
        if not upload_url: return {}
        token = self._upload_file(upload_url, video_bytes, "video.mp4", "video/mp4")
        if not token: return {}

        payload = {"attachments": [{"type": "video", "payload": {"token": token}}]}
        if caption:
            payload["text"] = caption
            payload["format"] = "markdown"
        return self._request("messages", params={"chat_id": chat_id}, json=payload)

    def upload_and_send_file(self, chat_id: str, file_bytes: bytes, filename: str, caption: str = "") -> dict:
        upload_url = self._get_upload_url("file")
        if not upload_url: return {}
        token = self._upload_file(upload_url, file_bytes, filename, "application/octet-stream")
        if not token: return {}

        payload = {"attachments": [{"type": "file", "payload": {"token": token}}]}
        if caption:
            payload["text"] = caption
            payload["format"] = "markdown"
        return self._request("messages", params={"chat_id": chat_id}, json=payload)