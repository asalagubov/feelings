"""Inline-клавиатуры бота: частота, временное окно, эмоции, шкала силы."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.texts import (
    DONTKNOW_CODE,
    EMOTIONS,
    EMOTIONS_BY_CODE,
    SKIP_CODE,
    SKIP_LABEL,
    TONES,
    emotion_label,
    tone_button_label,
)

TIME_WINDOW_PRESETS = [
    ("8:00–22:00", 8, 22),
    ("9:00–21:00", 9, 21),
    ("10:00–23:00", 10, 23),
]


def frequency_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for hours in (1, 2, 3, 4):
        builder.button(text=f"Раз в {hours} ч.", callback_data=f"freq:{hours}")
    builder.adjust(2)
    return builder.as_markup()


def time_window_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, start, end in TIME_WINDOW_PRESETS:
        builder.button(text=label, callback_data=f"window:{start}-{end}")
    builder.button(text="✏️ Ввести вручную", callback_data="window:custom")
    builder.adjust(1)
    return builder.as_markup()


def emotions_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for emotion in EMOTIONS:
        if emotion["code"] == DONTKNOW_CODE:
            continue
        builder.button(
            text=emotion_label(emotion),
            callback_data=f"emotion:{emotion['code']}",
        )
    builder.adjust(2)

    dontknow = EMOTIONS_BY_CODE[DONTKNOW_CODE]
    builder.row(
        InlineKeyboardButton(
            text=emotion_label(dontknow), callback_data=f"emotion:{DONTKNOW_CODE}"
        ),
        InlineKeyboardButton(text=SKIP_LABEL, callback_data=f"emotion:{SKIP_CODE}"),
    )
    return builder.as_markup()


def intensity_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value in range(1, 11):
        builder.button(text=str(value), callback_data=f"intensity:{value}")
    builder.adjust(5, 5)
    return builder.as_markup()


def tone_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tone in TONES:
        builder.button(
            text=tone_button_label(tone), callback_data=f"tone:{tone['code']}"
        )
    builder.adjust(2, 2)
    return builder.as_markup()
