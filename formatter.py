"""
Конвертер форматирования Telegram → MAX.

Telegram entities: bold, italic, underline, strikethrough, code, pre,
                   text_link, mention, url, spoiler
MAX поддерживает Markdown: **bold**, _italic_, `code`, [text](url)
"""
import re
from typing import Optional


def tg_entities_to_max_markdown(text: str, entities: list) -> str:
    """
    Конвертирует текст с Telegram entities в MAX Markdown.
    entities — список объектов telebot.types.MessageEntity
    """
    if not entities:
        return text

    # Работаем с байтами для корректного индексирования UTF-16
    # (Telegram считает offset/length в UTF-16 code units)
    text_utf16 = text.encode("utf-16-le")

    # Собираем список (offset, length, type, url) и сортируем по offset
    parts = []
    for e in entities:
        offset_bytes = e.offset * 2
        length_bytes = e.length * 2
        chunk = text_utf16[offset_bytes: offset_bytes + length_bytes].decode("utf-16-le")
        url = getattr(e, "url", None)
        parts.append((e.offset, e.length, e.type, url, chunk))

    # Строим результат с маркерами форматирования
    result = []
    prev = 0
    # Сортируем по offset
    parts.sort(key=lambda x: x[0])

    for offset, length, etype, url, chunk in parts:
        # Текст до текущей entity
        result.append(text[prev: offset])
        prev = offset + length

        if etype == "bold":
            result.append(f"**{chunk}**")
        elif etype == "italic":
            result.append(f"_{chunk}_")
        elif etype == "underline":
            result.append(f"__{chunk}__")
        elif etype == "strikethrough":
            result.append(f"~~{chunk}~~")
        elif etype == "code":
            result.append(f"`{chunk}`")
        elif etype == "pre":
            result.append(f"```\n{chunk}\n```")
        elif etype == "text_link":
            result.append(f"[{chunk}]({url})")
        elif etype == "url":
            # URL уже в тексте — оставляем как есть
            result.append(chunk)
        elif etype == "mention":
            # @username — оставляем как есть
            result.append(chunk)
        else:
            result.append(chunk)

    # Остаток текста
    result.append(text[prev:])
    return "".join(result)


def strip_tg_formatting(text: str) -> str:
    """Убрать Markdown-символы Telegram из текста (если entities недоступны)."""
    # Убираем типичные Telegram MarkdownV2 escape-символы
    return re.sub(r"\\([_*\[\]()~`>#+\-=|{}.!])", r"\1", text)


def truncate(text: str, max_len: int = 4000) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
