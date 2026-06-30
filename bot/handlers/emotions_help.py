"""Команда /emotions — справочник всех эмоций одним сообщением."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.handlers.common import show_single_command_output
from bot.texts import build_emotions_help

router = Router()


@router.message(Command("emotions"))
async def cmd_emotions(message: Message) -> None:
    """Присылает справочник чувств. Доступна в любой момент.

    Повторные вызовы не копятся: предыдущий словарик и команду убираем,
    оставляя в чате одну актуальную карточку-справочник."""
    await show_single_command_output(message, "emotions", build_emotions_help())
