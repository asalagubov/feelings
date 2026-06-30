"""Шаг «который час у тебя сейчас»: по тексту определяем и сохраняем таймзону."""

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import Message

from bot import database
from bot import texts
from bot.timeutil import resolve_timezone, zone_label

router = Router()


def _parse_time(raw: str) -> tuple[int, int | None] | None:
    s = raw.strip().replace(".", ":").replace(" ", "")
    if ":" in s:
        parts = s.split(":")
        if len(parts) != 2:
            return None
        hh, mm = parts
        if not (hh.isdigit() and mm.isdigit()):
            return None
        hour, minute = int(hh), int(mm)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
        return None
    if not s.isdigit():
        return None
    hour = int(s)
    if 0 <= hour <= 23:
        return hour, None
    return None


@router.message(F.text, ~F.text.startswith("/"))
async def maybe_set_timezone(message: Message) -> None:
    user_id = message.from_user.id
    pending = await database.get_pinned_msg(user_id, "tz")
    if not pending:
        return
    parsed = _parse_time(message.text or "")
    if parsed is None:
        await message.answer(texts.TZ_BAD_INPUT)
        return
    hour, minute = parsed
    zone = resolve_timezone(hour, minute, datetime.now(timezone.utc))
    await database.set_user_timezone(user_id, zone)
    await database.set_pinned_msg(user_id, "tz", None)
    try:
        await message.bot.delete_message(user_id, pending)
    except Exception:
        pass
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(texts.TZ_SAVED.format(label=zone_label(zone)))
