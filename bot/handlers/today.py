"""Команда /today — показать сегодняшний итог в любой момент."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot import database
from bot import texts
from bot.handlers.common import show_single_command_output
from bot.scheduler import build_digest
from bot.timeutil import today_in, user_tz_name

router = Router()


@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    user = await database.get_user(message.from_user.id)
    today = today_in(user_tz_name(user))
    entries = await database.get_entries_for_day(message.from_user.id, today)
    text = build_digest(entries, today) if entries else texts.TODAY_EMPTY
    await show_single_command_output(
        message, "today", text, add_to_day=True, day=today
    )
