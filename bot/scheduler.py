"""Планировщик: ежечасные вопросы и «Итог дня» в конце окна каждого юзера."""

import html
import logging
from collections import Counter
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot import database
from bot import texts
from bot.keyboards import emotions_keyboard
from bot.timeutil import now_in, today_in, user_tz_name

logger = logging.getLogger(__name__)

RU_MONTHS_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _window(user: dict) -> tuple[int, int]:
    start = max(1, min(23, user["start_hour"]))
    end = max(start, min(23, user["end_hour"]))
    return start, end


def _last_slot(user: dict) -> int:
    start, end = _window(user)
    freq = max(1, user["frequency_hours"])
    return start + ((end - start) // freq) * freq


def _fallback_hour(user: dict) -> int:
    return _last_slot(user) + 1


def _is_time_to_ask(user: dict, hour: int) -> bool:
    start, end = _window(user)
    freq = max(1, user["frequency_hours"])
    if not (start <= hour <= end):
        return False
    return (hour - start) % freq == 0


async def _expire_previous_question(bot: Bot, user_id: int) -> None:
    fresh = await database.get_user(user_id)
    last_id = fresh.get("last_question_msg_id") if fresh else None
    if not last_id:
        return
    try:
        await bot.edit_message_text(
            texts.QUESTION_EXPIRED, chat_id=user_id, message_id=last_id
        )
    except Exception as exc:
        logger.debug("Не удалось погасить старый вопрос %s: %s", user_id, exc)
    await database.set_last_question(user_id, None)


async def send_reminders(bot: Bot) -> None:
    for user in await database.get_all_users():
        local = now_in(user_tz_name(user))
        if not _is_time_to_ask(user, local.hour):
            continue
        user_id = user["user_id"]
        today = local.strftime("%Y-%m-%d")
        await _expire_previous_question(bot, user_id)
        try:
            sent = await bot.send_message(
                user_id, texts.QUESTION_EMOTION, reply_markup=emotions_keyboard()
            )
        except TelegramForbiddenError:
            logger.info("Пользователь %s заблокировал бота", user_id)
            continue
        except Exception as exc:
            logger.warning("Не удалось отправить вопрос %s: %s", user_id, exc)
            continue
        await database.set_last_question(user_id, sent.message_id)
        await database.add_day_message(user_id, sent.message_id, today)


def _raz(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "раз"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "раза"
    return "раз"


def _bare_name(emotion: str) -> str:
    parts = emotion.split(" ", 1)
    return parts[1] if len(parts) > 1 else emotion


def _summary_line(counts: Counter) -> str:
    ordered = counts.most_common()
    top_name, top_n = ordered[0]
    distinct = len(ordered)
    second_n = ordered[1][1] if distinct > 1 else 0

    leader = (top_n >= 3 and top_n > second_n) or (distinct == 1 and top_n >= 2)
    if leader:
        return f"Сегодня чаще всего — {top_name.lower()} ({top_n} {_raz(top_n)})."
    if distinct == 1:
        return f"Сегодня — {top_name.lower()}."

    parts = [name.lower() if n == 1 else f"{name.lower()} ({n})" for name, n in ordered]
    listed = ", ".join(parts)
    if all(n == 1 for _, n in ordered):
        return f"Сегодня чаще всего: {listed} — по разу каждое."
    return f"Сегодня: {listed}."


def build_digest(entries: list[dict], day: str | None = None) -> str:
    counts = Counter(_bare_name(entry["emotion"]) for entry in entries)

    if day:
        d = datetime.strptime(day, "%Y-%m-%d")
        header = f"<b>Итог дня, {d.day} {RU_MONTHS_GEN[d.month]}</b> 🌱"
    else:
        header = "<b>Итог дня</b> 🌱"

    lines = [header, "", f"<i>{_summary_line(counts)}</i>", ""]

    for entry in entries:
        time_str = datetime.fromisoformat(entry["timestamp"]).strftime("%H:%M")
        name = _bare_name(entry["emotion"])
        if entry["intensity"] is not None:
            head = f"{name} {entry['intensity']}/10"
        elif entry["tone"]:
            head = f"{name} — {entry['tone']}"
        else:
            head = name
        reason = html.escape(entry["reason"]) if entry["reason"] else None
        line = f"{time_str} · {head}" + (f" — {reason}" if reason else "")
        lines.append(line)

    return "\n".join(lines)


async def _deliver_digest(
    bot: Bot, user_id: int, text: str, edit_msg_id: int | None
) -> int | None:
    if edit_msg_id:
        try:
            await bot.edit_message_text(text, chat_id=user_id, message_id=edit_msg_id)
            return edit_msg_id
        except TelegramForbiddenError:
            return 0
        except Exception as exc:
            logger.debug("Не удалось превратить ответ в итог %s: %s", user_id, exc)
    try:
        sent = await bot.send_message(user_id, text)
        return sent.message_id
    except TelegramForbiddenError:
        return 0
    except Exception as exc:
        logger.warning("Не удалось отправить итог %s: %s", user_id, exc)
        return None


async def _flush_day(
    bot: Bot, user_id: int, day: str | None, *, edit_msg_id: int | None = None
) -> bool:
    grouped = await database.peek_day_messages_grouped(user_id)
    msg_ids = grouped.get(day, [])
    entries = await database.get_entries_for_day(user_id, day) if day else []

    keep_id: int | None = None
    if entries:
        if await database.try_claim_digest(user_id, day):
            delivered = await _deliver_digest(
                bot, user_id, build_digest(entries, day), edit_msg_id
            )
            if delivered is None:
                await database.release_digest(user_id, day)
                return False
            keep_id = delivered or None

    for mid in msg_ids:
        if keep_id and mid == keep_id:
            continue
        try:
            await bot.delete_message(user_id, mid)
        except Exception as exc:
            logger.debug("Не удалось удалить %s/%s: %s", user_id, mid, exc)
    await database.delete_day_messages(user_id, day)
    await database.set_last_question(user_id, None)
    await database.set_pinned_msg(user_id, "today", None)
    return True


async def flush_day_after_answer(
    bot: Bot, user: dict | None, day: str, hour: int, edit_msg_id: int | None
) -> bool:
    if user is None or hour < _last_slot(user):
        return False
    return await _flush_day(bot, user["user_id"], day, edit_msg_id=edit_msg_id)


async def run_digest_sweep(bot: Bot) -> None:
    for user_id in await database.get_users_with_day_messages():
        user = await database.get_user(user_id)
        tz = user_tz_name(user)
        today = today_in(tz)
        now_hour = now_in(tz).hour
        grouped = await database.peek_day_messages_grouped(user_id)
        for day in sorted(grouped, key=lambda d: d or ""):
            if day is None:
                await _flush_day(bot, user_id, None)
            elif day < today:
                await _flush_day(bot, user_id, day)
            elif day == today and user is not None and now_hour >= _fallback_hour(user):
                await _flush_day(bot, user_id, day)


async def hourly_tick(bot: Bot) -> None:
    await run_digest_sweep(bot)
    await send_reminders(bot)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        hourly_tick,
        trigger=CronTrigger(minute=0),
        args=[bot],
        id="hourly_tick",
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
    )
    return scheduler


async def send_timezone_question(bot: Bot, user_id: int) -> None:
    old = await database.get_pinned_msg(user_id, "tz")
    if old:
        try:
            await bot.delete_message(user_id, old)
        except Exception as exc:
            logger.debug("Не убрать прошлый вопрос о времени %s: %s", user_id, exc)
    try:
        sent = await bot.send_message(user_id, texts.TZ_QUESTION)
    except TelegramForbiddenError:
        logger.info("Пользователь %s заблокировал бота", user_id)
        return
    except Exception as exc:
        logger.warning("Не удалось спросить время %s: %s", user_id, exc)
        return
    await database.set_pinned_msg(user_id, "tz", sent.message_id)


async def prompt_missing_timezones(bot: Bot) -> None:
    for user_id in await database.users_without_timezone():
        if await database.get_pinned_msg(user_id, "tz"):
            continue
        await send_timezone_question(bot, user_id)
