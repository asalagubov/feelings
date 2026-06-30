"""Обработчики команды /start и настройки расписания.

Сценарий: /start → выбор частоты → выбор временного окна (пресет или ручной
ввод) → сохранение настроек в таблицу users.
"""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import database
from bot import texts
from bot.keyboards import frequency_keyboard, time_window_keyboard

router = Router()


class SetupStates(StatesGroup):
    """Состояния настройки расписания."""

    choosing_frequency = State()     # ждём выбор частоты (после /start или /settings)
    choosing_window = State()        # ждём выбор временного окна
    writing_custom_window = State()  # ждём ручной ввод окна текстом


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> int:
    """Меняет текст сообщения-мастера на следующий шаг и возвращает его id.

    Если кнопку нажали на сообщении старше 48 ч, Telegram присылает
    InaccessibleMessage (без метода edit_text) — тогда шлём новое сообщение,
    чтобы хендлер не падал. Возвращаемый id нужен для дальнейшего «Готово».
    """
    if isinstance(callback.message, Message):
        msg = await callback.message.edit_text(text, reply_markup=reply_markup)
        return msg.message_id if isinstance(msg, Message) else callback.message.message_id
    msg = await callback.bot.send_message(
        callback.from_user.id, text, reply_markup=reply_markup
    )
    return msg.message_id


async def _delete_message_safely(bot, chat_id: int, message_id: int | None) -> None:
    """Удаляет сообщение, молча игнорируя ошибки (старое/нет прав/уже удалено)."""
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:  # noqa: BLE001
        pass


async def _start_wizard(message: Message, state: FSMContext, intro_text: str) -> None:
    """Запускает мастер настройки одним «стоящим» сообщением.

    Саму команду здесь НЕ трогаем: /start удалять нельзя — это ломает кнопку
    START (после очистки чата она бесконечно пересылает /start); а команду
    /settings уже убрал cmd_settings до вызова. Здесь убираем лишь предыдущее
    СВОЁ сообщение настройки (незавершённый мастер или прошлое «Готово») и
    показываем свежий выбор частоты.

    id предыдущего мастера берём из pinned_messages (переживает рестарт и не
    требует строки users) и из FSM — что окажется доступным.
    """
    data = await state.get_data()
    fsm_prev = data.get("setup_msg_id")
    await state.clear()

    user_id = message.from_user.id
    # Убираем предыдущее «стоящее» сообщение настройки (своё, не команду юзера).
    db_prev = await database.get_pinned_msg(user_id, "settings")
    for prev_id in {db_prev, fsm_prev} - {None}:
        await _delete_message_safely(message.bot, message.chat.id, prev_id)

    # Показываем свежий мастер; помечаем состояние и запоминаем его id (в БД —
    # чтобы повторный /start убрал брошенный мастер даже после рестарта).
    sent = await message.answer(intro_text, reply_markup=frequency_keyboard())
    await state.set_state(SetupStates.choosing_frequency)
    await state.update_data(setup_msg_id=sent.message_id, frequency=None)
    await database.set_pinned_msg(user_id, "settings", sent.message_id)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Приветствие и выбор частоты напоминаний."""
    await _start_wizard(message, state, texts.GREETING)


@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext) -> None:
    """Перенастройка расписания в любой момент: показываем текущее и даём
    выбрать заново (тот же сценарий частота → окно)."""
    # Команду /settings убираем (безопасно — у неё нет кнопки START, в отличие
    # от /start). Удаляем до показа мастера, чтобы в чате остался только он.
    await _delete_message_safely(message.bot, message.chat.id, message.message_id)
    user = await database.get_user(message.from_user.id)
    if user is None:
        # Ещё не настраивался — отправляем к /start. Если был брошенный мастер,
        # его уберёт следующий /start (id лежит в pinned).
        await state.clear()
        await message.answer(texts.NO_SETTINGS)
        return
    intro = texts.SETTINGS_INTRO.format(
        frequency=user["frequency_hours"],
        start=user["start_hour"],
        end=user["end_hour"],
    )
    await _start_wizard(message, state, intro)


@router.callback_query(SetupStates.choosing_frequency, F.data.startswith("freq:"))
async def choose_frequency(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь выбрал частоту — переходим к выбору времени."""
    frequency = int(callback.data.split(":", 1)[1])
    await state.update_data(frequency=frequency)
    await state.set_state(SetupStates.choosing_window)
    await _safe_edit(callback, texts.CHOOSE_WINDOW, reply_markup=time_window_keyboard())
    await callback.answer()


@router.callback_query(SetupStates.choosing_window, F.data == "window:custom")
async def choose_window_custom(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь хочет ввести окно вручную."""
    # Запоминаем id сообщения-настройки, чтобы потом превратить его в «Готово».
    setup_msg_id = await _safe_edit(callback, texts.CUSTOM_WINDOW_PROMPT)
    await state.update_data(setup_msg_id=setup_msg_id)
    await state.set_state(SetupStates.writing_custom_window)
    await callback.answer()


@router.callback_query(SetupStates.choosing_window, F.data.startswith("window:"))
async def choose_window_preset(callback: CallbackQuery, state: FSMContext) -> None:
    """Пользователь выбрал готовый пресет временного окна."""
    # callback.data вида "window:8-22"
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
    """Разбор ручного ввода окна в формате ЧЧ-ЧЧ.

    Команды (текст с «/») сюда не попадают — они уходят своим обработчикам
    (/emotions, /today и т. д.), а не «съедаются» как ввод окна."""
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


# --- Вспомогательные функции ----------------------------------------------

def _parse_window(raw: str) -> tuple[int, int] | None:
    """Парсит строку «ЧЧ-ЧЧ». Возвращает (start, end) или None при ошибке."""
    parts = raw.strip().replace("–", "-").split("-")  # на случай длинного тире
    if len(parts) != 2:
        return None
    try:
        start_hour = int(parts[0])
        end_hour = int(parts[1])
    except ValueError:
        return None
    # Часы в допустимом диапазоне и начало строго раньше конца.
    if 0 <= start_hour <= 23 and 0 <= end_hour <= 23 and start_hour < end_hour:
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
    """Сохраняет настройки и сводит всё к одному аккуратному подтверждению:
    сообщение-настройку превращаем в «Готово», ручной ввод окна удаляем."""
    data = await state.get_data()
    frequency = data.get("frequency")
    if frequency is None:
        # Краевой случай (гонка/перетёртые данные): дошли без выбора частоты —
        # тихо выходим, не пугая ложным «расписание не настроено».
        await state.clear()
        return

    await database.upsert_user(user_id, frequency, start_hour, end_hour)
    await state.clear()

    text = texts.SETUP_DONE.format(frequency=frequency, start=start_hour, end=end_hour)
    # Превращаем то же сообщение мастера в «Готово» (его id закреплён как
    # pinned "settings" — предыдущее настройка убрала при старте мастера).
    final_id = None
    if setup_msg_id:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=setup_msg_id)
            final_id = setup_msg_id
        except Exception as exc:  # noqa: BLE001 — сообщение могло устареть (>48 ч)
            # Гонка двойного тапа: второй edit идентичным текстом даёт «message
            # is not modified» — значит «Готово» уже стоит, считаем это успехом
            # и НЕ шлём дубль.
            if "not modified" in str(exc).lower():
                final_id = setup_msg_id
            else:
                final_id = None
    if final_id is None:
        # Не смогли отредактировать мастер — убираем его и шлём «Готово» заново.
        await _delete_message_safely(bot, chat_id, setup_msg_id)
        sent = await bot.send_message(chat_id, text)
        final_id = sent.message_id

    await database.set_pinned_msg(user_id, "settings", final_id)

    # Удаляем ручной ввод окна («10-22»), чтобы не оставлять лишнее.
    if user_msg is not None:
        try:
            await user_msg.delete()
        except Exception:  # noqa: BLE001 — нет прав/старое — не критично
            pass


@router.callback_query(F.data.startswith("freq:") | F.data.startswith("window:"))
async def stale_setup_button(callback: CallbackQuery) -> None:
    """Заглушка для кнопок мастера настройки, нажатых не в своём состоянии.

    Зарегистрирована последней: в нужном состоянии срабатывают основные
    хендлеры выше, а сюда попадают только «устаревшие» нажатия (например,
    второй тап по пресету после сохранения или старый мастер, поверх которого
    уже пошёл опрос о чувствах). Без этого на кнопке бесконечно крутятся
    «часики», а раньше такие нажатия ещё и портили состояние опроса."""
    await callback.answer("Эта настройка уже неактуальна — открой /settings 🙂")
