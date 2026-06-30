"""Общие помощники для хендлеров команд."""

from aiogram.types import Message

from bot import database


async def show_single_command_output(
    message: Message,
    kind: str,
    text: str,
    *,
    add_to_day: bool = False,
    day: str | None = None,
) -> Message:
    user_id = message.from_user.id

    old_id = await database.get_pinned_msg(user_id, kind)
    if old_id:
        try:
            await message.bot.delete_message(message.chat.id, old_id)
        except Exception:
            pass

    try:
        await message.delete()
    except Exception:
        pass

    sent = await message.answer(text)
    await database.set_pinned_msg(user_id, kind, sent.message_id)
    if add_to_day and day:
        await database.add_day_message(user_id, sent.message_id, day)
    return sent
