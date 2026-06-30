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
from bot.handlers import emotions_help, mood, start, today
from bot.scheduler import setup_scheduler


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    # Готовим базу данных (создаём таблицы при первом запуске).
    await init_db()

    # Бот с HTML-разметкой по умолчанию (нужно для <b>...</b> в сообщениях).
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Хранилище FSM в памяти — достаточно для MVP.
    dp = Dispatcher(storage=MemoryStorage())

    # Порядок важен: команды (start, emotions, today) идут перед mood,
    # чтобы они не «съедались» обработчиком причины (writing_reason).
    dp.include_routers(start.router, emotions_help.router, today.router, mood.router)

    # Планировщик: ежечасные вопросы + ежедневный дайджест.
    scheduler = setup_scheduler(bot)
    scheduler.start()

    logging.info("Бот запущен. Останавливать — Ctrl+C.")
    # Меню команд (кнопка «/» в Telegram). Не критично для работы — если
    # Telegram на миг недоступен, не роняем старт бота.
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Начать / настроить"),
                BotCommand(command="today", description="Итог за сегодня"),
                BotCommand(command="settings", description="Изменить расписание"),
                BotCommand(command="emotions", description="Словарик чувств"),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logging.warning("Не удалось установить меню команд: %s", exc)

    try:
        # Снимаем накопившиеся апдейты и стартуем long polling.
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
