# Telegram → МАКС: зеркало канала

## Что делает бот

Автоматически копирует все посты из вашего Telegram-канала в канал/чат МАКС:
- Текст с форматированием (жирный, курсив, код, ссылки)
- Фото
- Видео (до 50 MB)
- Документы/файлы
- GIF-анимации

## Быстрый старт

### 1. Создать Telegram-бота

1. Напишите `@BotFather` в Telegram → `/newbot`
2. Скопируйте токен
3. Добавьте бота в ваш канал как **администратора** (права: чтение сообщений)

### 2. Создать МАКС-бота

1. Откройте МАКС, найдите `@maxbot`
2. Создайте бота, скопируйте токен
3. Добавьте бота в ваш канал МАКС как **администратора**

### 3. Узнать ID каналов

**Telegram channel ID:**
- Перешлите любое сообщение из канала боту `@username_to_id_bot`
- Или используйте: `https://api.telegram.org/bot<TOKEN>/getUpdates`

**MAX chat ID:**
- Напишите боту в MAX, затем вызовите:
  ```
  GET https://botapi.max.ru/chats?access_token=<MAX_TOKEN>
  ```

### 4. Настроить и запустить

```bash
cd tg-to-max-bot
cp .env.example .env
# Заполни .env своими токенами и ID

pip install -r requirements.txt
python bot.py
```

## Структура .env

```
TG_BOT_TOKEN=123456789:AAF...
TG_CHANNEL_ID=-1001234567890
MAX_BOT_TOKEN=ваш_токен_макс
MAX_CHAT_ID=id_канала_в_максе
```

## Работа как сервис (systemd)

```ini
# /etc/systemd/system/tg-max-bot.service
[Unit]
Description=Telegram to MAX mirror bot
After=network.target

[Service]
WorkingDirectory=/path/to/tg-to-max-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
EnvironmentFile=/path/to/tg-to-max-bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable tg-max-bot
systemctl start tg-max-bot
```
