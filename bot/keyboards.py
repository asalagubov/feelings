"""Inline-клавиатуры бота: частота, временное окно, эмоции, шкала силы."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.texts import (
    EMOTIONS,
    SKIP_CODE,
    SKIP_LABEL,
    TONES,
    emotion_label,
    tone_button_label,
)

# Готовые пресеты временных окон: (подпись, начало, конец).
TIME_WINDOW_PRESETS = [
    ("8:00–22:00", 8, 22),
    ("9:00–21:00", 9, 21),
    ("10:00–23:00", 10, 23),
]


def frequency_keyboard() -> InlineKeyboardMarkup:
    """Выбор частоты напоминаний: раз в 1/2/3/4 часа."""
    builder = InlineKeyboardBuilder()
    for hours in (1, 2, 3, 4):
        builder.button(text=f"Раз в {hours} ч.", callback_data=f"freq:{hours}")
    builder.adjust(2)  # по 2 кнопки в ряд
    return builder.as_markup()


def time_window_keyboard() -> InlineKeyboardMarkup:
    """Выбор временного окна: пресеты + кнопка ручного ввода."""
    builder = InlineKeyboardBuilder()
    for label, start, end in TIME_WINDOW_PRESETS:
        builder.button(text=label, callback_data=f"window:{start}-{end}")
    builder.button(text="✏️ Ввести вручную", callback_data="window:custom")
    builder.adjust(1)  # по одной кнопке в ряд — так подписи читаются лучше
    return builder.as_markup()


def emotions_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора эмоции (по 3 кнопки в ряд) + «Пропустить»."""
    builder = InlineKeyboardBuilder()
    for emotion in EMOTIONS:
        builder.button(
            text=emotion_label(emotion),
            callback_data=f"emotion:{emotion['code']}",
        )
    builder.adjust(3)  # по 3 кнопки в ряд

    # «Пропустить» — отдельной строкой во всю ширину.
    skip_button = InlineKeyboardButton(text=SKIP_LABEL, callback_data=f"emotion:{SKIP_CODE}")
    builder.row(skip_button)
    return builder.as_markup()


def intensity_keyboard() -> InlineKeyboardMarkup:
    """Шкала силы 1–10: два ряда по 5 кнопок."""
    builder = InlineKeyboardBuilder()
    for value in range(1, 11):
        builder.button(text=str(value), callback_data=f"intensity:{value}")
    builder.adjust(5, 5)  # два ряда по 5
    return builder.as_markup()


def tone_keyboard() -> InlineKeyboardMarkup:
    """Оттенок для «Не знаю»: приятное / нейтральное / неприятное / смешанное."""
    builder = InlineKeyboardBuilder()
    for tone in TONES:
        builder.button(
            text=tone_button_label(tone), callback_data=f"tone:{tone['code']}"
        )
    builder.adjust(2, 2)  # два ряда по две кнопки
    return builder.as_markup()
