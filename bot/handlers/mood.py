"""FSM-сценарий записи настроения: эмоция → сила → причина → сохранение."""

import html
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import database
from bot import texts
from bot.keyboards import intensity_keyboard, tone_keyboard
from bot.texts import (
    DONTKNOW_CODE,
    EMOTIONS_BY_CODE,
    SKIP_CODE,
    TONES_BY_CODE,
    emotion_label,
)

router = Router()


class MoodStates(StatesGroup):
    """Состояния опроса о настроении."""

    choosing_intensity = State()  # ждём выбор силы 1–10 (обычная эмоция)
    choosing_tone = State()       # ждём выбор оттенка (сценарий «Не знаю»)
    writing_reason = State()      # ждём текст с причиной


async def _edit_or_send(callback: CallbackQuery, text: str, reply_markup=None) -> int:
    """Меняет текст сообщения-вопроса на следующий шаг и возвращает его id.

    Если кнопку нажали на сообщении старше 48 ч, Telegram присылает
    InaccessibleMessage (без метода edit_text) — тогда просто шлём новое
    сообщение, чтобы хендлер не падал. Возвращаемый id нужен, чтобы потом
    превратить это же сообщение в карточку-итог.
    """
    if isinstance(callback.message, Message):
        msg = await callback.message.edit_text(text, reply_markup=reply_markup)
        # edit_text для обычных (не inline) сообщений возвращает Message;
        # на всякий случай подстрахуемся id исходного сообщения.
        return msg.message_id if isinstance(msg, Message) else callback.message.message_id
    msg = await callback.bot.send_message(
        callback.from_user.id, text, reply_markup=reply_markup
    )
    return msg.message_id


def _format_card(time_str: str, emotion: str, intensity, tone, reason) -> str:
    """Компактная карточка-итог записи (заменяет переписку в чате).

    Пример: «🌿 14:00 · 😕 Растерянность · 7/10
              «Много задач на работе»»
    """
    measure = f"{intensity}/10" if intensity is not None else (tone or "")
    head = " · ".join(part for part in (f"🌿 {time_str}", emotion, measure) if part)
    if reason:
        # reason — произвольный текст пользователя, сообщение в режиме HTML.
        return f"{head}\n«{html.escape(reason)}»"
    return head


@router.callback_query(F.data.startswith("emotion:"))
async def choose_emotion(callback: CallbackQuery, state: FSMContext) -> None:
    """Шаг 1. Пользователь выбрал эмоцию (или нажал «Пропустить»).

    Обработчик намеренно без фильтра состояния: вопрос приходит из планировщика,
    когда пользователь ни в каком состоянии не находится.
    """
    code = callback.data.split(":", 1)[1]

    # Пользователь начал отвечать на этот вопрос — он больше не «висит»,
    # и планировщику не нужно гасить его при следующем напоминании.
    await database.set_last_question(callback.from_user.id, None)

    # Краевой случай: «Пропустить» — ничего не сохраняем.
    if code == SKIP_CODE:
        await state.clear()
        await _edit_or_send(callback, texts.SKIP_CONFIRM)
        await callback.answer()
        return

    emotion = EMOTIONS_BY_CODE.get(code)
    if emotion is None:
        # Защита от устаревших/битых кнопок.
        await callback.answer("Не получилось распознать чувство 🤔", show_alert=True)
        return

    # Начинаем НОВУЮ запись с чистого листа: set_data затирает возможные
    # «зависшие» intensity/tone от предыдущего незавершённого опроса
    # (иначе у «Не знаю» могла бы остаться чужая сила, и наоборот).
    await state.set_data({"emotion": emotion_label(emotion)})

    if code == DONTKNOW_CODE:
        # Особый сценарий: вместо силы 1–10 спрашиваем оттенок переживания.
        await state.set_state(MoodStates.choosing_tone)
        await _edit_or_send(callback, texts.QUESTION_TONE, reply_markup=tone_keyboard())
    else:
        # Обычная эмоция: переходим к выбору силы.
        await state.set_state(MoodStates.choosing_intensity)
        await _edit_or_send(
            callback, texts.QUESTION_INTENSITY, reply_markup=intensity_keyboard()
        )
    await callback.answer()


@router.callback_query(MoodStates.choosing_intensity, F.data.startswith("intensity:"))
async def choose_intensity(callback: CallbackQuery, state: FSMContext) -> None:
    """Шаг 2 (обычная эмоция). Выбрана сила — спрашиваем причину."""
    intensity = int(callback.data.split(":", 1)[1])
    await state.set_state(MoodStates.writing_reason)
    # Запоминаем id сообщения-вопроса — позже превратим его в карточку-итог.
    question_msg_id = await _edit_or_send(callback, texts.QUESTION_REASON)
    await state.update_data(intensity=intensity, question_msg_id=question_msg_id)
    await callback.answer()


@router.callback_query(MoodStates.choosing_tone, F.data.startswith("tone:"))
async def choose_tone(callback: CallbackQuery, state: FSMContext) -> None:
    """Шаг 2 (сценарий «Не знаю»). Выбран оттенок — спрашиваем причину."""
    code = callback.data.split(":", 1)[1]
    tone = TONES_BY_CODE.get(code)
    if tone is None:
        await callback.answer("Не получилось распознать оттенок 🤔", show_alert=True)
        return
    # Сохраняем название оттенка (например, «неприятное») — оно попадёт в дайджест.
    await state.set_state(MoodStates.writing_reason)
    question_msg_id = await _edit_or_send(callback, texts.QUESTION_REASON)
    await state.update_data(tone=tone["name"], question_msg_id=question_msg_id)
    await callback.answer()


@router.message(MoodStates.writing_reason, F.text)
async def write_reason(message: Message, state: FSMContext) -> None:
    """Шаг 3. Пользователь прислал причину текстом — сохраняем запись."""
    data = await state.get_data()
    emotion = data.get("emotion")
    intensity = data.get("intensity")  # None для сценария «Не знаю»
    tone = data.get("tone")            # None для обычной эмоции

    # Краевой случай: данных нет (состояние «потерялось») — мягко выходим.
    # Для валидной записи должна быть эмоция и ровно один из «измерителей»:
    # сила (обычная эмоция) или оттенок («Не знаю»).
    if emotion is None or (intensity is None and tone is None):
        await state.clear()
        await message.answer(texts.NO_SETTINGS)
        return

    reason = message.text.strip()

    # Краевой случай: пришла команда без обработчика (например, /help) — её не
    # должно «съесть» как причину. Команды с обработчиками (/start, /emotions)
    # сюда не доходят: их раньше перехватывают свои роутеры.
    if reason.startswith("/"):
        await message.answer(
            "Это похоже на команду 🙂 Напиши причину обычной фразой "
            "или нажми /start, чтобы начать заново."
        )
        return

    reason_value = reason or None
    timestamp = await database.add_entry(
        message.from_user.id, emotion, intensity, reason_value, tone
    )
    await state.clear()

    # Сводим всю переписку в одну компактную карточку: сообщение-вопрос
    # превращаем в карточку-итог, а текстовый ответ пользователя удаляем.
    time_str = datetime.fromisoformat(timestamp).strftime("%H:%M")
    card = _format_card(time_str, emotion, intensity, tone, reason_value)

    question_msg_id = data.get("question_msg_id")
    edited = False
    if question_msg_id:
        try:
            await message.bot.edit_message_text(
                card, chat_id=message.chat.id, message_id=question_msg_id
            )
            edited = True
        except Exception:  # noqa: BLE001 — сообщение могло устареть/удалиться
            edited = False
    if not edited:
        sent = await message.answer(card)
        # Вопрос уже убрали (например, вечерний дайджест прошёл, пока человек
        # писал причину) — карточка ушла новым сообщением. Добавим её в
        # «сообщения дня», чтобы она не осталась сиротой и убралась со
        # следующей сводкой.
        await database.add_day_message(
            message.from_user.id, sent.message_id, timestamp[:10]
        )

    # Убираем реплику пользователя, чтобы в чате осталась только карточка.
    try:
        await message.delete()
    except Exception:  # noqa: BLE001 — нет прав/слишком старое — не критично
        pass


@router.message(MoodStates.writing_reason)
async def write_reason_not_text(message: Message) -> None:
    """Если на шаге причины прислали не текст (стикер, фото) — просим текст."""
    await message.answer("Напиши, пожалуйста, причину короткой фразой 🙂")


@router.callback_query(F.data.startswith("intensity:") | F.data.startswith("tone:"))
async def stale_step_button(callback: CallbackQuery) -> None:
    """Заглушка для кнопок прошлого вопроса (силы/оттенка), нажатых не в том
    состоянии. Зарегистрирована последней — в нужном состоянии срабатывают
    основные хендлеры выше, а сюда попадают только «устаревшие» нажатия.
    Без этого у пользователя бесконечно крутятся «часики» на кнопке.
    """
    await callback.answer("Этот вопрос уже неактуален 🙂")
