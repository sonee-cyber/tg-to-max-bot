"""
Обёртка над MAX Bot API (botapi.max.ru)
Документация: https://dev.max.ru/docs/
"""
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

MAX_API_BASE = "https://botapi.max.ru"


class MaxAPI:
    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.params = {"access_token": token}  # type: ignore

    def _post(self, method: str, **kwargs) -> dict:
        url = f"{MAX_API_BASE}/{method}"
        try:
            resp = self.session.post(url, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"MAX API error [{method}]: {e}")
            return {}

    def _get(self, method: str, **kwargs) -> dict:
        url = f"{MAX_API_BASE}/{method}"
        try:
            resp = self.session.get(url, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"MAX API error [{method}]: {e}")
            return {}

    def send_message(self, chat_id: str, text: str, link: Optional[dict] = None) -> dict:
        """Отправить текстовое сообщение в MAX."""
        payload = {
            "text": text,
        }
        if link:
            payload["link"] = link

        return self._post("messages", params={"chat_id": chat_id}, json=payload)

    def send_photo(self, chat_id: str, photo_url: str, caption: str = "") -> dict:
        """Отправить фото по URL."""
        payload = {
            "attachments": [
                {
                    "type": "image",
                    "payload": {"url": photo_url},
                }
            ],
        }
        if caption:
            payload["text"] = caption

        return self._post("messages", params={"chat_id": chat_id}, json=payload)

    def upload_and_send_photo(self, chat_id: str, photo_bytes: bytes, caption: str = "") -> dict:
        """Загрузить фото и отправить в MAX."""
        # Шаг 1: получить URL для загрузки
        upload_info = self._get("uploads", params={"type": "image"})
        upload_url = upload_info.get("url")
        if not upload_url:
            logger.error("Не удалось получить URL для загрузки фото")
            return {}

        # Шаг 2: загрузить файл
        try:
            upload_resp = requests.post(upload_url, files={"data": ("photo.jpg", photo_bytes, "image/jpeg")})
            upload_resp.raise_for_status()
            token = upload_resp.json().get("token")
        except requests.RequestException as e:
            logger.error(f"Ошибка загрузки фото: {e}")
            return {}

        # Шаг 3: отправить
        payload = {
            "attachments": [
                {
                    "type": "image",
                    "payload": {"token": token},
                }
            ],
        }
        if caption:
            payload["text"] = caption

        return self._post("messages", params={"chat_id": chat_id}, json=payload)

    def upload_and_send_video(self, chat_id: str, video_bytes: bytes, caption: str = "") -> dict:
        """Загрузить видео и отправить."""
        upload_info = self._get("uploads", params={"type": "video"})
        upload_url = upload_info.get("url")
        if not upload_url:
            return {}

        try:
            upload_resp = requests.post(upload_url, files={"data": ("video.mp4", video_bytes, "video/mp4")})
            upload_resp.raise_for_status()
            token = upload_resp.json().get("token")
        except requests.RequestException as e:
            logger.error(f"Ошибка загрузки видео: {e}")
            return {}

        payload = {
            "attachments": [{"type": "video", "payload": {"token": token}}],
        }
        if caption:
            payload["text"] = caption

        return self._post("messages", params={"chat_id": chat_id}, json=payload)

    def upload_and_send_file(self, chat_id: str, file_bytes: bytes, filename: str, caption: str = "") -> dict:
        """Загрузить файл и отправить."""
        upload_info = self._get("uploads", params={"type": "file"})
        upload_url = upload_info.get("url")
        if not upload_url:
            return {}

        try:
            upload_resp = requests.post(upload_url, files={"data": (filename, file_bytes, "application/octet-stream")})
            upload_resp.raise_for_status()
            token = upload_resp.json().get("token")
        except requests.RequestException as e:
            logger.error(f"Ошибка загрузки файла: {e}")
            return {}

        payload = {
            "attachments": [{"type": "file", "payload": {"token": token}}],
        }
        if caption:
            payload["text"] = caption

        return self._post("messages", params={"chat_id": chat_id}, json=payload)
