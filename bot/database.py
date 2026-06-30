"""Работа с базой данных SQLite через aiosqlite.

Две таблицы:
    users   — настройки расписания пользователя;
    entries — записи о настроении (эмоция, сила, причина).
"""

from datetime import datetime

import aiosqlite

from bot.config import DB_PATH


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет. Вызывается один раз при старте."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id              INTEGER PRIMARY KEY,
                frequency_hours      INTEGER NOT NULL,
                start_hour           INTEGER NOT NULL,
                end_hour             INTEGER NOT NULL,
                last_question_msg_id INTEGER   -- id последнего неотвеченного вопроса
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
        # Сообщения опроса за день — чтобы удалить их при вечернем дайджесте.
        # day (YYYY-MM-DD) привязывает сообщение к конкретному дню: уборка и
        # дайджест идут по дням, поэтому пропущенный (бот был офлайн) день не
        # теряется, а его сводка приходит «вдогонку».
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS day_messages (
                user_id    INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                day        TEXT
            )
            """
        )
        # «Закреплённые» одиночные сообщения бота (мастер настройки / «Готово»,
        # вывод /today, словарик /emotions). Храним id, чтобы при повторе убрать
        # предыдущее и держать в чате одно. Отдельная таблица (а не колонки в
        # users) — чтобы дедуп работал и для пользователя без строки users
        # (например, ещё не прошедшего /start), переживая рестарт процесса.
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
        await _migrate_entries(db)
        await _migrate_users(db)
        await _migrate_day_messages(db)
        await db.commit()


async def _migrate_users(db: aiosqlite.Connection) -> None:
    """Добавляет колонку last_question_msg_id в users, если её ещё нет.

    Колонка nullable, поэтому достаточно ALTER TABLE ADD COLUMN — пересборка не
    нужна. Проверка наличия делает миграцию идемпотентной. (Прочие id-сообщений
    теперь живут в отдельной таблице pinned_messages, см. init_db.)
    """
    async with db.execute("PRAGMA table_info(users)") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if "last_question_msg_id" not in columns:
        await db.execute("ALTER TABLE users ADD COLUMN last_question_msg_id INTEGER")


async def _migrate_day_messages(db: aiosqlite.Connection) -> None:
    """Добавляет колонку day в day_messages для баз, созданных без неё.

    Колонка nullable — старым строкам проставится NULL; при дайджесте они
    попадут в отдельную «группу без дня» и просто удалятся (без сводки).
    """
    async with db.execute("PRAGMA table_info(day_messages)") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if "day" not in columns:
        await db.execute("ALTER TABLE day_messages ADD COLUMN day TEXT")


async def _migrate_entries(db: aiosqlite.Connection) -> None:
    """Мягкая миграция таблицы entries для баз, созданных старой версией.

    Старая схема: intensity INTEGER NOT NULL и без колонки tone. Чтобы поддержать
    сценарий «Не знаю» (intensity = NULL, tone = оттенок), пересобираем таблицу,
    сохраняя имеющиеся записи. Если колонка tone уже есть — ничего не делаем.
    """
    async with db.execute("PRAGMA table_info(entries)") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if "tone" in columns:
        return  # схема уже актуальна

    # На случай, если прошлый запуск был прерван между созданием временной
    # таблицы и коммитом: убираем возможный «осиротевший» entries_new, иначе
    # повторный старт упадёт на CREATE («table entries_new already exists»).
    await db.execute("DROP TABLE IF EXISTS entries_new")

    # Пересоздаём таблицу с новой схемой и переносим старые данные.
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


# --- Пользователи (расписание) --------------------------------------------

async def upsert_user(
    user_id: int, frequency_hours: int, start_hour: int, end_hour: int
) -> None:
    """Сохраняет (или обновляет) настройки расписания пользователя.

    Обновляем только поля расписания, не трогая last_question_msg_id: иначе
    при перенастройке (/settings) «потерялся» бы id висящего вопроса, и его
    не получилось бы потом погасить. INSERT OR REPLACE не подходит — он
    переписывает строку целиком и обнуляет невёрстанные колонки. Делаем
    UPDATE-или-INSERT вручную: синтаксис UPSERT `ON CONFLICT` есть только в
    SQLite 3.24+, а версия в окружении старее.
    """
    async with aiosqlite.connect(DB_PATH) as db:
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
    """Возвращает настройки пользователя или None, если он не настроен."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_all_users() -> list[dict]:
    """Возвращает всех пользователей — нужен планировщику для рассылки."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def set_last_question(user_id: int, msg_id: int | None) -> None:
    """Запоминает id последнего отправленного вопроса (или сбрасывает в NULL,
    когда пользователь начал на него отвечать)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET last_question_msg_id = ? WHERE user_id = ?",
            (msg_id, user_id),
        )
        await db.commit()


# --- Закреплённые одиночные сообщения бота ---------------------------------
# kind: "settings" (мастер/«Готово»), "today" (вывод /today), "emotions"
# (словарик). Не зависят от строки users — работают и до /start, и переживают
# рестарт. INSERT OR REPLACE по (user_id, kind) совместим со старым SQLite.

_PINNED_KINDS = {"settings", "today", "emotions"}


async def get_pinned_msg(user_id: int, kind: str) -> int | None:
    """Возвращает id закреплённого сообщения данного вида или None."""
    assert kind in _PINNED_KINDS, kind
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT message_id FROM pinned_messages WHERE user_id = ? AND kind = ?",
            (user_id, kind),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_pinned_msg(user_id: int, kind: str, msg_id: int | None) -> None:
    """Запоминает id закреплённого сообщения данного вида (перетирает прежнее)."""
    assert kind in _PINNED_KINDS, kind
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO pinned_messages (user_id, kind, message_id)
            VALUES (?, ?, ?)
            """,
            (user_id, kind, msg_id),
        )
        await db.commit()


# --- Сообщения опроса за день (для уборки при дайджесте) -------------------

async def add_day_message(user_id: int, message_id: int, day: str) -> None:
    """Запоминает id отправленного сообщения опроса с привязкой ко дню (day).

    Все шаги одного опроса живут в одном сообщении (вопрос → карточка/
    «пропущено»/«в другой раз»), поэтому достаточно хранить id вопроса.
    day (YYYY-MM-DD) нужен, чтобы вечером убрать и подытожить именно тот день,
    к которому относится сообщение.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO day_messages (user_id, message_id, day) VALUES (?, ?, ?)",
            (user_id, message_id, day),
        )
        await db.commit()


async def get_users_with_day_messages() -> list[int]:
    """Возвращает id всех пользователей, у кого есть накопленные сообщения дня.

    Дайджест ходит по ним (а не только по настроенным users), иначе сообщения
    того, кто, например, вызвал /today до /start, остались бы сиротами навсегда.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT user_id FROM day_messages"
        ) as cursor:
            return [row[0] for row in await cursor.fetchall()]


async def peek_day_messages_grouped(user_id: int) -> dict[str | None, list[int]]:
    """Читает (НЕ удаляя) накопленные сообщения пользователя по дням:
    {day -> [message_id, ...]}.

    Не удаляем здесь, чтобы дайджест был атомарным к сбою отправки: строки дня
    стираются (delete_day_messages) только ПОСЛЕ успешной обработки этого дня —
    иначе при сетевой ошибке сводка и карточки пропали бы безвозвратно."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    """Удаляет строки day_messages конкретного дня (NULL-день — отдельная группа)."""
    async with aiosqlite.connect(DB_PATH) as db:
        if day is None:
            await db.execute(
                "DELETE FROM day_messages WHERE user_id = ? AND day IS NULL", (user_id,)
            )
        else:
            await db.execute(
                "DELETE FROM day_messages WHERE user_id = ? AND day = ?", (user_id, day)
            )
        await db.commit()


# --- Записи о настроении ---------------------------------------------------

async def add_entry(
    user_id: int,
    emotion: str,
    intensity: int | None,
    reason: str | None,
    tone: str | None = None,
) -> str:
    """Сохраняет одну запись о настроении с текущей отметкой времени.

    Для обычной эмоции задан intensity (1–10), tone = None.
    Для «Не знаю» intensity = None, а tone — оттенок (приятное/…/смешанное).
    Возвращает ISO-метку времени записи (нужна для карточки-итога).
    """
    timestamp = datetime.now().isoformat(timespec="seconds")
    async with aiosqlite.connect(DB_PATH) as db:
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
    """Возвращает записи пользователя за конкретный день (формат day: YYYY-MM-DD),
    отсортированные по времени."""
    async with aiosqlite.connect(DB_PATH) as db:
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
