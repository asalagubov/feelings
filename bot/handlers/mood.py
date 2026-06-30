"""FSM-сценарий записи настроения: эмоция → сила → причина → сохранение."""

import html
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import database
from bot import scheduler
from bot import texts
from bot.keyboards import intensity_keyboard, tone_keyboard
from bot.texts import (
    DONTKNOW_CODE,
    EMOTIONS_BY_CODE,
    SKIP_CODE,
    TONES_BY_CODE,
    emotion_label,
)
from bot.timeutil import user_tz_name

router = Router()


class MoodStates(StatesGroup):
    choosing_intensity = State()
    choosing_tone = State()
    writing_reason = State()


async def _edit_or_send(callback: CallbackQuery, text: str, reply_markup=None) -> int:
    if isinstance(callback.message, Message):
        msg = await callback.message.edit_text(text, reply_markup=reply_markup)
        return msg.message_id if isinstance(msg, Message) else callback.message.message_id
    msg = await callback.bot.send_message(
        callback.from_user.id, text, reply_markup=reply_markup
    )
    return msg.message_id


def _format_card(time_str: str, emotion: str, intensity, tone, reason) -> str:
    measure = f"{intensity}/10" if intensity is not None else (tone or "")
    head = " · ".join(part for part in (f"🌿 {time_str}", emotion, measure) if part)
    if reason:
        return f"{head}\n«{html.escape(reason)}»"
    return head


@router.callback_query(F.data.startswith("emotion:"))
async def choose_emotion(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.split(":", 1)[1]

    await database.set_last_question(callback.from_user.id, None)

    if code == SKIP_CODE:
        await state.clear()
        await _edit_or_send(callback, texts.SKIP_CONFIRM)
        await callback.answer()
        return

    emotion = EMOTIONS_BY_CODE.get(code)
    if emotion is None:
        await callback.answer("Не получилось распознать чувство 🤔", show_alert=True)
        return

    await state.set_data({"emotion": emotion_label(emotion)})

    if code == DONTKNOW_CODE:
        await state.set_state(MoodStates.choosing_tone)
        await _edit_or_send(callback, texts.QUESTION_TONE, reply_markup=tone_keyboard())
    else:
        await state.set_state(MoodStates.choosing_intensity)
        await _edit_or_send(
            callback, texts.QUESTION_INTENSITY, reply_markup=intensity_keyboard()
        )
    await callback.answer()


@router.callback_query(MoodStates.choosing_intensity, F.data.startswith("intensity:"))
async def choose_intensity(callback: CallbackQuery, state: FSMContext) -> None:
    intensity = int(callback.data.split(":", 1)[1])
    await state.set_state(MoodStates.writing_reason)
    question_msg_id = await _edit_or_send(callback, texts.QUESTION_REASON)
    await state.update_data(intensity=intensity, question_msg_id=question_msg_id)
    await callback.answer()


@router.callback_query(MoodStates.choosing_tone, F.data.startswith("tone:"))
async def choose_tone(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.split(":", 1)[1]
    tone = TONES_BY_CODE.get(code)
    if tone is None:
        await callback.answer("Не получилось распознать оттенок 🤔", show_alert=True)
        return
    await state.set_state(MoodStates.writing_reason)
    question_msg_id = await _edit_or_send(callback, texts.QUESTION_REASON)
    await state.update_data(tone=tone["name"], question_msg_id=question_msg_id)
    await callback.answer()


@router.message(MoodStates.writing_reason, F.text)
async def write_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    emotion = data.get("emotion")
    intensity = data.get("intensity")
    tone = data.get("tone")

    if emotion is None or (intensity is None and tone is None):
        await state.clear()
        await message.answer(texts.NO_SETTINGS)
        return

    reason = message.text.strip()

    if reason.startswith("/"):
        await message.answer(
            "Это похоже на команду 🙂 Напиши причину обычной фразой "
            "или нажми /start, чтобы начать заново."
        )
        return

    reason_value = reason or None
    user = await database.get_user(message.from_user.id)
    tz_name = user_tz_name(user)
    timestamp = await database.add_entry(
        message.from_user.id, emotion, intensity, reason_value, tone, tz_name
    )
    await state.clear()

    question_msg_id = data.get("question_msg_id")
    today = timestamp[:10]
    answer_hour = int(timestamp[11:13])

    if await scheduler.flush_day_after_answer(
        message.bot, user, today, answer_hour, question_msg_id
    ):
        try:
            await message.delete()
        except Exception:
            pass
        return

    time_str = datetime.fromisoformat(timestamp).strftime("%H:%M")
    card = _format_card(time_str, emotion, intensity, tone, reason_value)

    edited = False
    if question_msg_id:
        try:
            await message.bot.edit_message_text(
                card, chat_id=message.chat.id, message_id=question_msg_id
            )
            edited = True
        except Exception:
            edited = False
    if not edited:
        sent = await message.answer(card)
        await database.add_day_message(
            message.from_user.id, sent.message_id, timestamp[:10]
        )

    try:
        await message.delete()
    except Exception:
        pass


@router.message(MoodStates.writing_reason)
async def write_reason_not_text(message: Message) -> None:
    await message.answer("Напиши, пожалуйста, причину короткой фразой 🙂")


@router.callback_query(F.data.startswith("intensity:") | F.data.startswith("tone:"))
async def stale_step_button(callback: CallbackQuery) -> None:
    await callback.answer("Этот вопрос уже неактуален 🙂")
