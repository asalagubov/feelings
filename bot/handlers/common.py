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
    """Показывает вывод команды как «одиночное» сообщение.

    Убирает предыдущий такой же вывод бота и саму команду пользователя, затем
    шлёт свежий результат — чтобы повторные вызовы /today и /emotions не
    копились в чате. Удалять команды /today и /emotions безопасно: они не
    порождают кнопку START (в отличие от /start, который трогать нельзя).

    add_to_day=True добавляет вывод в «сообщения дня», чтобы он убрался при
    вечернем дайджесте (используется для /today — это превью итога дня).
    """
    user_id = message.from_user.id

    # Убираем предыдущий вывод этой же команды (это сообщение БОТА).
    old_id = await database.get_pinned_msg(user_id, kind)
    if old_id:
        try:
            await message.bot.delete_message(message.chat.id, old_id)
        except Exception:  # noqa: BLE001 — старое/нет прав — не критично
            pass

    # Убираем саму команду пользователя («/today», «/emotions»).
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass

    sent = await message.answer(text)
    await database.set_pinned_msg(user_id, kind, sent.message_id)
    if add_to_day and day:
        await database.add_day_message(user_id, sent.message_id, day)
    return sent
