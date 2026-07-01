"""Обработчики команды /start и настройки расписания."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import database
from bot import scheduler
from bot import texts
from bot.keyboards import frequency_keyboard, time_window_keyboard

router = Router()


class SetupStates(StatesGroup):
    choosing_frequency = State()
    choosing_window = State()
    writing_custom_window = State()


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> int:
    if isinstance(callback.message, Message):
        msg = await callback.message.edit_text(text, reply_markup=reply_markup)
        return msg.message_id if isinstance(msg, Message) else callback.message.message_id
    msg = await callback.bot.send_message(
        callback.from_user.id, text, reply_markup=reply_markup
    )
    return msg.message_id


async def _delete_message_safely(bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def _start_wizard(message: Message, state: FSMContext, intro_text: str) -> None:
    data = await state.get_data()
    fsm_prev = data.get("setup_msg_id")
    await state.clear()

    user_id = message.from_user.id
    db_prev = await database.get_pinned_msg(user_id, "settings")
    for prev_id in {db_prev, fsm_prev} - {None}:
        await _delete_message_safely(message.bot, message.chat.id, prev_id)

    sent = await message.answer(intro_text, reply_markup=frequency_keyboard())
    await state.set_state(SetupStates.choosing_frequency)
    await state.update_data(setup_msg_id=sent.message_id, frequency=None)
    await database.set_pinned_msg(user_id, "settings", sent.message_id)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await _start_wizard(message, state, texts.GREETING)


@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext) -> None:
    await _delete_message_safely(message.bot, message.chat.id, message.message_id)
    user = await database.get_user(message.from_user.id)
    if user is None:
        await state.clear()
        await message.answer(texts.NO_SETTINGS)
        return
    intro = texts.SETTINGS_INTRO.format(
        frequency=user["frequency_hours"],
        start=user["start_hour"],
        end=user["end_hour"],
    )
    await _start_wizard(message, state, intro)


@router.message(Command("timezone"))
async def cmd_timezone(message: Message, state: FSMContext) -> None:
    await _delete_message_safely(message.bot, message.chat.id, message.message_id)
    user = await database.get_user(message.from_user.id)
    if user is None:
        await state.clear()
        await message.answer(texts.NO_SETTINGS)
        return
    await state.clear()
    await scheduler.send_timezone_question(message.bot, message.from_user.id)


@router.callback_query(SetupStates.choosing_frequency, F.data.startswith("freq:"))
async def choose_frequency(callback: CallbackQuery, state: FSMContext) -> None:
    frequency = int(callback.data.split(":", 1)[1])
    await state.update_data(frequency=frequency)
    await state.set_state(SetupStates.choosing_window)
    await _safe_edit(callback, texts.CHOOSE_WINDOW, reply_markup=time_window_keyboard())
    await callback.answer()


@router.callback_query(SetupStates.choosing_window, F.data == "window:custom")
async def choose_window_custom(callback: CallbackQuery, state: FSMContext) -> None:
    setup_msg_id = await _safe_edit(callback, texts.CUSTOM_WINDOW_PROMPT)
    await state.update_data(setup_msg_id=setup_msg_id)
    await state.set_state(SetupStates.writing_custom_window)
    await callback.answer()


@router.callback_query(SetupStates.choosing_window, F.data.startswith("window:"))
async def choose_window_preset(callback: CallbackQuery, state: FSMContext) -> None:
    start_str, end_str = callback.data.split(":", 1)[1].split("-")
    await _save_settings(
        callback.from_user.id,
        state,
        int(start_str),
        int(end_str),
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        setup_msg_id=callback.message.message_id,
        user_msg=None,
    )
    await callback.answer()


@router.message(
    StateFilter(SetupStates.writing_custom_window), F.text, ~F.text.startswith("/")
)
async def custom_window_input(message: Message, state: FSMContext) -> None:
    parsed = _parse_window(message.text or "")
    if parsed is None:
        await message.answer(texts.CUSTOM_WINDOW_ERROR)
        return
    start_hour, end_hour = parsed
    data = await state.get_data()
    await _save_settings(
        message.from_user.id,
        state,
        start_hour,
        end_hour,
        bot=message.bot,
        chat_id=message.chat.id,
        setup_msg_id=data.get("setup_msg_id"),
        user_msg=message,
    )


def _parse_window(raw: str) -> tuple[int, int] | None:
    parts = raw.strip().replace("–", "-").split("-")
    if len(parts) != 2:
        return None
    try:
        start_hour = int(parts[0])
        end_hour = int(parts[1])
    except ValueError:
        return None
    if 1 <= start_hour < end_hour <= 23:
        return start_hour, end_hour
    return None


async def _save_settings(
    user_id: int,
    state: FSMContext,
    start_hour: int,
    end_hour: int,
    *,
    bot,
    chat_id: int,
    setup_msg_id: int | None,
    user_msg: Message | None,
) -> None:
    data = await state.get_data()
    frequency = data.get("frequency")
    if frequency is None:
        await state.clear()
        return

    await database.upsert_user(user_id, frequency, start_hour, end_hour)
    await state.clear()

    text = texts.SETUP_DONE.format(frequency=frequency, start=start_hour, end=end_hour)
    final_id = None
    if setup_msg_id:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=setup_msg_id)
            final_id = setup_msg_id
        except Exception as exc:
            if "not modified" in str(exc).lower():
                final_id = setup_msg_id
            else:
                final_id = None
    if final_id is None:
        await _delete_message_safely(bot, chat_id, setup_msg_id)
        sent = await bot.send_message(chat_id, text)
        final_id = sent.message_id

    await database.set_pinned_msg(user_id, "settings", final_id)

    if user_msg is not None:
        try:
            await user_msg.delete()
        except Exception:
            pass

    user = await database.get_user(user_id)
    if user is not None and user.get("timezone") is None:
        await scheduler.send_timezone_question(bot, user_id)


@router.callback_query(F.data.startswith("freq:") | F.data.startswith("window:"))
async def stale_setup_button(callback: CallbackQuery) -> None:
    await callback.answer("Эта настройка уже неактуальна — открой /settings 🙂")
