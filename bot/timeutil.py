"""Работа со временем в таймзоне конкретного пользователя."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bot.config import DEFAULT_TZ


def get_zone(tz_name: str | None) -> ZoneInfo:
    for candidate in (tz_name, DEFAULT_TZ):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def user_tz_name(user: dict | None) -> str:
    if user is None:
        return DEFAULT_TZ
    return user.get("timezone") or DEFAULT_TZ


def now_in(tz_name: str | None) -> datetime:
    return datetime.now(get_zone(tz_name))


def local_naive_now(tz_name: str | None) -> datetime:
    return now_in(tz_name).replace(tzinfo=None)


def today_in(tz_name: str | None) -> str:
    return now_in(tz_name).strftime("%Y-%m-%d")


CANDIDATE_ZONES = [
    "Europe/Moscow",
    "Europe/Berlin",
    "Europe/London",
    "Europe/Kaliningrad",
    "Europe/Samara",
    "Asia/Tbilisi",
    "Asia/Yerevan",
    "Asia/Dubai",
    "Asia/Yekaterinburg",
    "Asia/Omsk",
    "Asia/Krasnoyarsk",
    "Asia/Irkutsk",
    "Asia/Vladivostok",
    "Asia/Almaty",
    "America/New_York",
    "America/Los_Angeles",
]

ZONE_LABELS = {
    "Europe/Moscow": "Москва",
    "Europe/Berlin": "Германия",
    "Europe/London": "Лондон",
    "Europe/Kaliningrad": "Калининград",
    "Europe/Samara": "Самара",
    "Asia/Tbilisi": "Тбилиси",
    "Asia/Yerevan": "Ереван",
    "Asia/Dubai": "Дубай",
    "Asia/Yekaterinburg": "Екатеринбург",
    "Asia/Almaty": "Алматы",
    "America/New_York": "Нью-Йорк",
    "America/Los_Angeles": "Лос-Анджелес",
}


def _zone_offset_minutes(name: str, now_utc: datetime) -> int:
    return int(now_utc.astimezone(ZoneInfo(name)).utcoffset().total_seconds() // 60)


def resolve_timezone(reported_hour: int, reported_minute: int | None, now_utc: datetime) -> str:
    # Ввели только час → берём минуты из текущего UTC: у целочасовых зон минуты
    # юзера и UTC совпадают, поэтому смещение получится ровным.
    minute = now_utc.minute if reported_minute is None else reported_minute
    reported = reported_hour * 60 + minute
    utc_minutes = now_utc.hour * 60 + now_utc.minute
    diff = ((reported - utc_minutes + 720) % 1440) - 720
    offset = round(diff / 15) * 15
    for name in CANDIDATE_ZONES:
        if _zone_offset_minutes(name, now_utc) == offset:
            return name
    if offset % 60 == 0:
        hours = offset // 60
        if hours == 0:
            return "UTC"
        return f"Etc/GMT{'-' if hours > 0 else '+'}{abs(hours)}"
    return DEFAULT_TZ


def zone_label(name: str) -> str:
    if name in ZONE_LABELS:
        return ZONE_LABELS[name]
    if name in ("UTC", "Etc/UTC"):
        return "UTC"
    if name.startswith("Etc/GMT"):
        sign, num = name[7], name[8:]
        shown = f"+{num}" if sign == "-" else f"−{num}"
        return f"UTC{shown}"
    return name
