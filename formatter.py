import logging

logger = logging.getLogger(__name__)

def _utf16_to_py_index(text: str, utf16_offset: int) -> int:
    """Переводит offset из Telegram (в UTF-16 единицах) в индекс Python строки"""
    count = 0
    for i, ch in enumerate(text):
        if count >= utf16_offset:
            return i
        # эмодзи > 0xFFFF в UTF-16 занимают 2 юнита, в Python 1 символ
        count += 2 if ord(ch) > 0xFFFF else 1
    return len(text)

def format_telegram_to_max(text: str, entities) -> str:
    if not text:
        return ""
    if not entities:
        return text

    # Сортируем с конца, чтобы вставки не сбивали offset
    ents = sorted(entities, key=lambda e: e.offset, reverse=True)

    # Работаем с mutable строкой
    result = text

    for ent in ents:
        try:
            start = _utf16_to_py_index(result if result is text else text, ent.offset) # всегда считаем от оригинала
            # Но для простоты считаем от исходного text
            # Пересчитываем заново для исходного текста
            py_start = _utf16_to_py_index(text, ent.offset)
            py_end = _utf16_to_py_index(text, ent.offset + ent.length)

            # Вырезаем из текущего result с учетом уже вставленных тегов
            # Чтобы не усложнять, применяем к result через поиск по оригинальным индексам
            # Для этого каждый раз пересобираем result по оригиналу - проще через список
            # Поэтому делаем один проход через построение нового текста

            # Мы делаем правильно: строим новый текст за один раз
            pass
        except Exception as e:
            logger.warning(f"Не смог обработать entity {ent}: {e}")
            continue

    # Правильный и простой способ - собрать заново
    # Строим карту вставок
    inserts = {} # py_index -> (open_tag, close_tag)
    # Сначала посчитаем все py индексы
    parsed = []
    for ent in entities:
        try:
            py_start = _utf16_to_py_index(text, ent.offset)
            py_end = _utf16_to_py_index(text, ent.offset + ent.length)
            entity_text = text[py_start:py_end]

            open_tag = close_tag = ""
            t = ent.type

            if t == "bold":
                open_tag, close_tag = "**", "**"
            elif t == "italic":
                open_tag, close_tag = "*", "*"
            elif t == "underline":
                open_tag, close_tag = "__", "__"
            elif t == "strikethrough":
                open_tag, close_tag = "~~", "~~"
            elif t == "code":
                open_tag, close_tag = "`", "`"
            elif t == "pre":
                open_tag, close_tag = "```\n", "\n```"
            elif t == "spoiler":
                open_tag, close_tag = "||", "||"
            elif t == "text_link":
                open_tag = "["
                close_tag = f"]({ent.url})"
            elif t == "url":
                # URL и так в тексте, ничего не делаем
                continue
            else:
                continue

            parsed.append((py_start, py_end, open_tag, close_tag, entity_text))
        except Exception as ex:
            logger.warning(f"skip entity {ent}: {ex}")
            continue

    # Применяем с конца
    parsed.sort(key=lambda x: x[0], reverse=True)
    res = text
    for py_start, py_end, open_tag, close_tag, _ in parsed:
        try:
            # Пересчитываем индексы в текущем res с учетом уже вставленных тегов
            # Так как мы идем с конца, res[py_end:] еще не трогали в начале строки
            # Но из-за эмодзи py индексы уже не совпадают с res после вставок
            # Поэтому самый надежный способ - собирать через куски оригинального текста

            # Сделаем финальную сборку правильно:
            pass
        except:
            pass

    # Финальная надежная реализация:
    # Идем по оригинальному тексту слева направо
    res_parts = []
    last_idx = 0
    # Сортируем по старту
    parsed_forward = sorted(parsed, key=lambda x: x[0])

    # Обработка вложенностей - упрощенная
    i = 0
    while i < len(text):
        # есть ли entity начинающаяся в i
        found = [p for p in parsed_forward if p[0] == i]
        if found:
            # берем самую длинную (внешнюю)
            p = max(found, key=lambda x: x[1]-x[0])
            _, py_end, open_tag, close_tag, _ = p
            inner_text = text[p[0]:py_end]
            # рекурсивно форматируем внутренности без этой entity (упрощенно - без вложенности)
            res_parts.append(open_tag + inner_text + close_tag)
            i = py_end
        else:
            res_parts.append(text[i])
            i += 1

    return "".join(res_parts)