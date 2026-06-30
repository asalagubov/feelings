"""Загрузка конфигурации из .env."""

import os

from dotenv import load_dotenv

# Подгружаем переменные окружения из файла .env в корне проекта.
load_dotenv()

# Токен бота берём только из окружения — в коде его держать нельзя.
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Переменная окружения BOT_TOKEN не задана. "
        "Скопируй .env.example в .env и впиши токен от @BotFather."
    )

# Путь к файлу базы данных SQLite (можно переопределить через .env).
DB_PATH = os.getenv("DB_PATH", "mood.db")

# Час, в который отправляется ежедневный дайджест (0–23). По умолчанию 23:00.
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "23"))
