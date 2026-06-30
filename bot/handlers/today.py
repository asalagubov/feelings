"""Команда /today — показать сегодняшний итог в любой момент."""

from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot import database
from bot import texts
from bot.handlers.common import show_single_command_output
from bot.scheduler import build_digest

router = Router()


@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    """Присылает дайджест за сегодня прямо сейчас (превью, не дожидаясь итога).

    Повторные вызовы не копятся: предыдущее превью и команду убираем. Превью
    добавляем в «сообщения дня», чтобы вечером оно ушло вместе с остальным и в
    чате осталась только финальная сводка.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    entries = await database.get_entries_for_day(message.from_user.id, today)
    text = build_digest(entries, today) if entries else texts.TODAY_EMPTY
    await show_single_command_output(
        message, "today", text, add_to_day=True, day=today
    )
