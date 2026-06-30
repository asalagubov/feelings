"""Работа с базой данных SQLite через aiosqlite."""

import aiosqlite

from bot.config import DB_PATH
from bot.timeutil import local_naive_now


def _connect() -> aiosqlite.Connection:
    return aiosqlite.connect(DB_PATH, timeout=5.0)


async def init_db() -> None:
    async with _connect() as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id              INTEGER PRIMARY KEY,
                frequency_hours      INTEGER NOT NULL,
                start_hour           INTEGER NOT NULL,
                end_hour             INTEGER NOT NULL,
                last_question_msg_id INTEGER,  -- id последнего неотвеченного вопроса
                timezone             TEXT      -- IANA-зона юзера; NULL → DEFAULT_TZ
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL,
                timestamp TEXT    NOT NULL,
                emotion   TEXT    NOT NULL,
                intensity INTEGER,           -- NULL для «Не знаю» (силу не спрашиваем)
                reason    TEXT,
                tone      TEXT               -- оттенок для «Не знаю»: приятное/…/смешанное
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS day_messages (
                user_id    INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                day        TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pinned_messages (
                user_id    INTEGER NOT NULL,
                kind       TEXT    NOT NULL,
                message_id INTEGER,
                PRIMARY KEY (user_id, kind)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS digested_days (
                user_id INTEGER NOT NULL,
                day     TEXT    NOT NULL,
                PRIMARY KEY (user_id, day)
            )
            """
        )
        await _migrate_entries(db)
        await _migrate_users(db)
        await _migrate_day_messages(db)
        await db.commit()


async def _migrate_users(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(users)") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if "last_question_msg_id" not in columns:
        await db.execute("ALTER TABLE users ADD COLUMN last_question_msg_id INTEGER")
    if "timezone" not in columns:
        await db.execute("ALTER TABLE users ADD COLUMN timezone TEXT")


async def _migrate_day_messages(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(day_messages)") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if "day" not in columns:
        await db.execute("ALTER TABLE day_messages ADD COLUMN day TEXT")


async def _migrate_entries(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(entries)") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if "tone" in columns:
        return

    await db.execute("DROP TABLE IF EXISTS entries_new")

    await db.execute(
        """
        CREATE TABLE entries_new (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            timestamp TEXT    NOT NULL,
            emotion   TEXT    NOT NULL,
            intensity INTEGER,
            reason    TEXT,
            tone      TEXT
        )
        """
    )
    await db.execute(
        """
        INSERT INTO entries_new (id, user_id, timestamp, emotion, intensity, reason)
        SELECT id, user_id, timestamp, emotion, intensity, reason FROM entries
        """
    )
    await db.execute("DROP TABLE entries")
    await db.execute("ALTER TABLE entries_new RENAME TO entries")


async def upsert_user(
    user_id: int, frequency_hours: int, start_hour: int, end_hour: int
) -> None:
    async with _connect() as db:
        async with db.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            exists = await cursor.fetchone() is not None
        if exists:
            await db.execute(
                """
                UPDATE users
                SET frequency_hours = ?, start_hour = ?, end_hour = ?
                WHERE user_id = ?
                """,
                (frequency_hours, start_hour, end_hour, user_id),
            )
        else:
            await db.execute(
                """
                INSERT INTO users (user_id, frequency_hours, start_hour, end_hour)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, frequency_hours, start_hour, end_hour),
            )
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_all_users() -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def set_last_question(user_id: int, msg_id: int | None) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET last_question_msg_id = ? WHERE user_id = ?",
            (msg_id, user_id),
        )
        await db.commit()


async def set_user_timezone(user_id: int, tz_name: str) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET timezone = ? WHERE user_id = ?", (tz_name, user_id)
        )
        await db.commit()


async def users_without_timezone() -> list[int]:
    async with _connect() as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE timezone IS NULL"
        ) as cursor:
            return [row[0] for row in await cursor.fetchall()]


_PINNED_KINDS = {"settings", "today", "emotions", "tz"}


async def get_pinned_msg(user_id: int, kind: str) -> int | None:
    assert kind in _PINNED_KINDS, kind
    async with _connect() as db:
        async with db.execute(
            "SELECT message_id FROM pinned_messages WHERE user_id = ? AND kind = ?",
            (user_id, kind),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_pinned_msg(user_id: int, kind: str, msg_id: int | None) -> None:
    assert kind in _PINNED_KINDS, kind
    async with _connect() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO pinned_messages (user_id, kind, message_id)
            VALUES (?, ?, ?)
            """,
            (user_id, kind, msg_id),
        )
        await db.commit()


async def add_day_message(user_id: int, message_id: int, day: str) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT INTO day_messages (user_id, message_id, day) VALUES (?, ?, ?)",
            (user_id, message_id, day),
        )
        await db.commit()


async def get_users_with_day_messages() -> list[int]:
    async with _connect() as db:
        async with db.execute(
            "SELECT DISTINCT user_id FROM day_messages"
        ) as cursor:
            return [row[0] for row in await cursor.fetchall()]


async def peek_day_messages_grouped(user_id: int) -> dict[str | None, list[int]]:
    async with _connect() as db:
        async with db.execute(
            "SELECT day, message_id FROM day_messages WHERE user_id = ? ORDER BY message_id",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    grouped: dict[str | None, list[int]] = {}
    for day, message_id in rows:
        grouped.setdefault(day, []).append(message_id)
    return grouped


async def delete_day_messages(user_id: int, day: str | None) -> None:
    async with _connect() as db:
        if day is None:
            await db.execute(
                "DELETE FROM day_messages WHERE user_id = ? AND day IS NULL", (user_id,)
            )
        else:
            await db.execute(
                "DELETE FROM day_messages WHERE user_id = ? AND day = ?", (user_id, day)
            )
        await db.commit()


async def try_claim_digest(user_id: int, day: str) -> bool:
    async with _connect() as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO digested_days (user_id, day) VALUES (?, ?)",
            (user_id, day),
        )
        await db.commit()
        return cursor.rowcount > 0


async def release_digest(user_id: int, day: str) -> None:
    async with _connect() as db:
        await db.execute(
            "DELETE FROM digested_days WHERE user_id = ? AND day = ?", (user_id, day)
        )
        await db.commit()


async def add_entry(
    user_id: int,
    emotion: str,
    intensity: int | None,
    reason: str | None,
    tone: str | None = None,
    tz_name: str | None = None,
) -> str:
    timestamp = local_naive_now(tz_name).isoformat(timespec="seconds")
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO entries (user_id, timestamp, emotion, intensity, reason, tone)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, timestamp, emotion, intensity, reason, tone),
        )
        await db.commit()
    return timestamp


async def get_entries_for_day(user_id: int, day: str) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM entries
            WHERE user_id = ? AND date(timestamp) = ?
            ORDER BY timestamp
            """,
            (user_id, day),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
