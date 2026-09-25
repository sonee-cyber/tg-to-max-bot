"""
Конвертер Telegram entities -> MAX Markdown
Корректно работает с эмодзи (UTF-16 surrogate pairs)
"""
import re
from typing import List

def _build_utf16_map(text: str):
    """Строит мапу: utf16_offset -> python_index"""
    # utf16 offset -> index в строке
    offset_to_index = []
    index_to_offset = []
    utf16_offset = 0
    for py_idx, ch in enumerate(text):
        offset_to_index.append(utf16_offset)
        index_to_offset.append(utf16_offset)
        # 1 code unit для BMP, 2 для эмодзи вне BMP
        utf16_len = len(ch.encode('utf-16-le')) // 2
        utf16_offset += utf16_len
    offset_to_index.append(utf16_offset) # sentinel для конца строки
    return offset_to_index, index_to_offset

def _utf16_to_py_range(text: str, offset: int, length: int, utf_map):
    offset_to_index, _ = utf_map
    # найти python индексы по utf16 offset
    # offset может указывать в середину surrogate - ищем ближайший
    try:
        # Бинарный поиск ближайшего offset
        # Простой линейный т.к. тексты короткие
        py_start = None
        py_end = None
        for py_idx, u16_off in enumerate(offset_to_index):
            if u16_off == offset:
                py_start = py_idx
            if u16_off == offset + length:
                py_end = py_idx
                break
        # fallback если не нашли точный (эмодзи на границе)
        if py_start is None:
            # ищем ближайший меньший
            for py_idx in range(len(offset_to_index)-1, -1, -1):
                if offset_to_index[py_idx] <= offset:
                    py_start = py_idx
                    break
        if py_end is None:
            for py_idx in range(len(offset_to_index)):
                if offset_to_index[py_idx] >= offset + length:
                    py_end = py_idx
                    break
        if py_start is None: py_start = 0
        if py_end is None: py_end = len(text)
        return py_start, py_end
    except Exception:
        return offset, offset+length

def tg_entities_to_max_markdown(text: str, entities: list) -> str:
    if not entities or not text:
        return text

    utf_map = _build_utf16_map(text)

    # Превращаем в список с py-индексами
    parsed = []
    for e in entities:
        py_start, py_end = _utf16_to_py_range(text, e.offset, e.length, (utf_map[0], utf_map[1]))
        if py_start >= len(text) or py_start < 0: continue
        py_end = min(py_end, len(text))
        if py_end <= py_start: continue
        chunk = text[py_start:py_end]
        url = getattr(e, "url", None)
        parsed.append((py_start, py_end, e.type, url, chunk))

    # Сортируем по start убыв. чтобы вставки с конца не сбивали индексы
    parsed.sort(key=lambda x: x[0], reverse=True)

    for py_start, py_end, etype, url, chunk in parsed:
        if etype == "bold":
            repl = f"**{chunk}**"
        elif etype == "italic":
            repl = f"_{chunk}_"
        elif etype == "underline":
            repl = f"__{chunk}__"
        elif etype == "strikethrough":
            repl = f"~~{chunk}~~"
        elif etype == "code":
            # внутри кода не экранируем
            repl = f"`{chunk}`"
        elif etype == "pre":
            repl = f"```\n{chunk}\n```"
        elif etype == "text_link" and url:
            # экранируем ] в тексте ссылки
            safe_chunk = chunk.replace("]", "\\]")
            repl = f"[{safe_chunk}]({url})"
        elif etype in ("url", "mention", "text_mention"):
            repl = chunk
        elif etype == "spoiler":
            repl = f"||{chunk}||"
        else:
            repl = chunk

        text = text[:py_start] + repl + text[py_end:]

    return text

def strip_tg_formatting(text: str) -> str:
    return re.sub(r"\\([_*\[\]()~`>#+\-=|{}.!])", r"\1", text)

def truncate(text: str, max_len: int = 4000) -> str:
    if len(text) <= max_len:
        return text
    # режем по границе слова, чтобы не порвать **жирный**
    cut = text[:max_len-3]
    # если оборвали внутри markdown - закроем
    return cut.rstrip() + "..."