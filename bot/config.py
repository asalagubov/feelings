"""Загрузка конфигурации из .env."""

import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Переменная окружения BOT_TOKEN не задана. "
        "Скопируй .env.example в .env и впиши токен от @BotFather."
    )

DB_PATH = os.getenv("DB_PATH", "mood.db")

DEFAULT_TZ = os.getenv("DEFAULT_TZ", "Europe/Moscow")
