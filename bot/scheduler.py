"""Планировщик APScheduler: ежечасная рассылка вопросов и дайджест в конце дня."""

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
from bot.config import DIGEST_HOUR
from bot.keyboards import emotions_keyboard

logger = logging.getLogger(__name__)

# Месяцы в родительном падеже для человеческой даты в шапке («30 июня»).
RU_MONTHS_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _is_time_to_ask(user: dict, hour: int) -> bool:
    """Определяет, пора ли спрашивать пользователя в указанный час.

    Шлём, если час попадает в окно [start_hour, end_hour] и кратен частоте,
    отсчитывая от начала окна.
    """
    start = user["start_hour"]
    end = user["end_hour"]
    frequency = user["frequency_hours"]
    if not (start <= hour <= end):
        return False
    return (hour - start) % frequency == 0


async def _expire_previous_question(bot: Bot, user_id: int) -> None:
    """Гасит предыдущий неотвеченный вопрос: убирает кнопки и помечает
    «пропущено», чтобы отвечать можно было только на актуальный вопрос."""
    # Перечитываем свежее значение, чтобы не затереть вопрос, на который
    # пользователь как раз начал отвечать (тогда id уже сброшен в NULL).
    fresh = await database.get_user(user_id)
    last_id = fresh.get("last_question_msg_id") if fresh else None
    if not last_id:
        return
    try:
        # edit_message_text без reply_markup убирает inline-клавиатуру.
        await bot.edit_message_text(
            texts.QUESTION_EXPIRED, chat_id=user_id, message_id=last_id
        )
    except Exception as exc:  # noqa: BLE001 — старое сообщение могло удалиться/устареть
        logger.debug("Не удалось погасить старый вопрос %s: %s", user_id, exc)
    await database.set_last_question(user_id, None)


async def send_reminders(bot: Bot) -> None:
    """Ежечасная задача: рассылает Вопрос 1 тем, кому сейчас пора."""
    now = datetime.now()
    hour = now.hour
    # В час дайджеста вопросы не шлём: иначе свежий вопрос мог бы попасть под
    # вечернюю уборку (его удалят или, наоборот, оставят «висеть») — гонка
    # между задачами reminders и digest. Час дайджеста отдаём только сводке.
    if hour == DIGEST_HOUR:
        return
    today = now.strftime("%Y-%m-%d")
    users = await database.get_all_users()
    for user in users:
        if not _is_time_to_ask(user, hour):
            continue
        user_id = user["user_id"]
        # Сначала гасим прошлый неотвеченный вопрос, потом шлём новый.
        await _expire_previous_question(bot, user_id)
        try:
            sent = await bot.send_message(
                user_id, texts.QUESTION_EMOTION, reply_markup=emotions_keyboard()
            )
        except TelegramForbiddenError:
            # Пользователь заблокировал бота — просто пропускаем.
            logger.info("Пользователь %s заблокировал бота", user_id)
            continue
        except Exception as exc:  # noqa: BLE001 — не хотим ронять рассылку из-за одного
            logger.warning("Не удалось отправить вопрос %s: %s", user_id, exc)
            continue
        # Запоминаем новый вопрос: для гашения (если не ответят) и для уборки
        # всех сообщений опроса при вечернем дайджесте.
        await database.set_last_question(user_id, sent.message_id)
        await database.add_day_message(user_id, sent.message_id, today)


def _raz(n: int) -> str:
    """Склонение слова «раз»: 1 раз, 2–4 раза, 5+ раз."""
    if n % 10 == 1 and n % 100 != 11:
        return "раз"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "раза"
    return "раз"


def _bare_name(emotion: str) -> str:
    """Из «😶 Не знаю» достаёт название без эмодзи — «Не знаю»."""
    parts = emotion.split(" ", 1)
    return parts[1] if len(parts) > 1 else emotion


def _summary_line(counts: Counter) -> str:
    """Живая строка-сводка вместо «турнирной таблицы» с медалями.

    Если есть явный лидер (≥3 раза и больше остальных) — называем его.
    Иначе перечисляем чувства; когда все по разу — так и пишем.
    """
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
    """Формирует текст дайджеста за день: спокойная шапка с датой, живая
    строка-сводка и чистый хронологический список без эмодзи и лишних тире.

    day (YYYY-MM-DD), если передан, показывается в шапке словами — это важно
    для сводки «вдогонку» за пропущенный день, чтобы было видно, какой день.
    """
    counts = Counter(_bare_name(entry["emotion"]) for entry in entries)

    if day:
        d = datetime.strptime(day, "%Y-%m-%d")
        header = f"<b>Итог дня, {d.day} {RU_MONTHS_GEN[d.month]}</b> 🌱"
    else:
        header = "<b>Итог дня</b> 🌱"

    # Шапка → пустая строка → тихая (курсивом) сводка → пустая строка → записи.
    lines = [header, "", f"<i>{_summary_line(counts)}</i>", ""]

    for entry in entries:
        time_str = datetime.fromisoformat(entry["timestamp"]).strftime("%H:%M")
        name = _bare_name(entry["emotion"])
        # Обычная эмоция — «Название N/10»; «Не знаю» — «Не знаю — оттенок».
        if entry["intensity"] is not None:
            head = f"{name} {entry['intensity']}/10"
        elif entry["tone"]:
            head = f"{name} — {entry['tone']}"
        else:
            head = name
        # Причина — произвольный текст пользователя, экранируем для HTML.
        reason = html.escape(entry["reason"]) if entry["reason"] else None
        line = f"{time_str} · {head}" + (f" — {reason}" if reason else "")
        lines.append(line)

    return "\n".join(lines)


async def send_daily_digest(bot: Bot) -> None:
    """Ежедневная задача: по каждому накопленному дню шлёт итог и убирает
    сообщения опроса.

    Обрабатываем сообщения, сгруппированные по дню (а не «всё за сегодня»):
    так если бот был офлайн в час сводки, пропущенный день не теряется — его
    сводка придёт «вдогонку», а карточки уберутся. Это же корректно работает
    при DIGEST_HOUR = 0 (сводка приходит на следующий день, но за нужную дату).

    Порядок важен и атомарен к сбою: сначала успешно шлём дайджест, и только
    ПОТОМ удаляем карточки и строки day_messages этого дня. При временной ошибке
    отправки строки дня остаются — сводка повторится при следующем запуске, а не
    пропадёт вместе с уже стёртыми карточками. Ходим по всем, у кого есть
    сообщения дня (не только по настроенным), чтобы не оставить сирот.
    """
    for user_id in await database.get_users_with_day_messages():
        grouped = await database.peek_day_messages_grouped(user_id)
        await database.set_last_question(user_id, None)

        for day in sorted(grouped, key=lambda d: d or ""):
            # 1. Если за день есть записи — сначала шлём дайджест. Только при
            #    успехе (или если бот заблокирован) переходим к уборке дня.
            if day:
                entries = await database.get_entries_for_day(user_id, day)
                if entries:
                    try:
                        await bot.send_message(user_id, build_digest(entries, day))
                    except TelegramForbiddenError:
                        # Заблокировали бота — день всё равно считаем обработанным
                        # (повтор не поможет), чистим строки ниже.
                        logger.info("Пользователь %s заблокировал бота", user_id)
                    except Exception as exc:  # noqa: BLE001 — временная ошибка сети/Telegram
                        logger.warning("Не удалось отправить дайджест %s: %s", user_id, exc)
                        continue  # НЕ удаляем день — сводка придёт «вдогонку»

            # 2. Дайджест доставлен (или его не нужно слать) — убираем карточки
            #    опроса этого дня и стираем строки day_messages.
            for message_id in grouped[day]:
                try:
                    await bot.delete_message(user_id, message_id)
                except Exception as exc:  # noqa: BLE001 — могло устареть/удалиться/блок
                    logger.debug("Не удалось удалить %s/%s: %s", user_id, message_id, exc)
            await database.delete_day_messages(user_id, day)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Создаёт и настраивает планировщик с двумя задачами."""
    scheduler = AsyncIOScheduler()

    # Каждый час в :00 проверяем, кому пора задать вопрос.
    scheduler.add_job(
        send_reminders,
        trigger=CronTrigger(minute=0),
        args=[bot],
        id="hourly_reminders",
        replace_existing=True,
    )

    # Ежедневно в DIGEST_HOUR:00 шлём дайджест.
    scheduler.add_job(
        send_daily_digest,
        trigger=CronTrigger(hour=DIGEST_HOUR, minute=0),
        args=[bot],
        id="daily_digest",
        replace_existing=True,
    )

    return scheduler
