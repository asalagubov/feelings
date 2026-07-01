"""Точка входа: запуск бота (polling) и планировщика напоминаний."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.config import BOT_TOKEN
from bot.database import init_db
from bot.handlers import emotions_help, mood, start, today, tz
from bot.scheduler import prompt_missing_timezones, run_digest_sweep, setup_scheduler


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    await init_db()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    dp = Dispatcher(storage=MemoryStorage())

    dp.include_routers(
        start.router, emotions_help.router, today.router, mood.router, tz.router
    )

    scheduler = setup_scheduler(bot)
    scheduler.start()

    try:
        await run_digest_sweep(bot)
    except Exception as exc:
        logging.warning("Догон итогов при старте не удался: %s", exc)

    try:
        await prompt_missing_timezones(bot)
    except Exception as exc:
        logging.warning("Не спросить время у юзеров без зоны: %s", exc)

    logging.info("Бот запущен. Останавливать — Ctrl+C.")
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Начать / настроить"),
                BotCommand(command="today", description="Итог за сегодня"),
                BotCommand(command="settings", description="Изменить расписание"),
                BotCommand(command="timezone", description="Часовой пояс (напр. в поездке)"),
                BotCommand(command="emotions", description="Словарик чувств"),
            ]
        )
    except Exception as exc:
        logging.warning("Не удалось установить меню команд: %s", exc)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
