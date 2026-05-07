from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

@dataclass
class Reminder:
    id: int
    user_id: int
    chat_id: int
    text: str
    remind_at: datetime
    is_sent: bool

class ReminderRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL UNIQUE,
                    chat_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    language_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    is_sent INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS reminder_notifications (
                    reminder_id INTEGER PRIMARY KEY,
                    message_id INTEGER NOT NULL,
                    sent_at TEXT NOT NULL,
                    responded INTEGER NOT NULL DEFAULT 0,
                    followup_sent INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await db.commit()

    async def upsert_user(
        self,
        user_id: int,
        chat_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
    ) -> None:
        now_iso = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (
                    telegram_user_id, chat_id, username, first_name, last_name, language_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    chat_id=excluded.chat_id,
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    language_code=excluded.language_code,
                    updated_at=excluded.updated_at
                """,
                (user_id, chat_id, username, first_name, last_name, language_code, now_iso, now_iso),
            )
            await db.commit()

    async def add_reminder(self, user_id: int, chat_id: int, text: str, remind_at: datetime) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO reminders (user_id, chat_id, text, remind_at, is_sent, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (
                    user_id,
                    chat_id,
                    text,
                    remind_at.isoformat(),
                    datetime.now(tz=remind_at.tzinfo).isoformat(),
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def get_active_reminders(self, user_id: int) -> list[Reminder]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, user_id, chat_id, text, remind_at, is_sent
                FROM reminders
                WHERE user_id = ? AND is_sent = 0
                ORDER BY remind_at ASC
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()

        result: list[Reminder] = []
        for row in rows:
            result.append(
                Reminder(
                    id=row[0],
                    user_id=row[1],
                    chat_id=row[2],
                    text=row[3],
                    remind_at=datetime.fromisoformat(row[4]),
                    is_sent=bool(row[5]),
                )
            )
        return result

    async def delete_by_index(self, user_id: int, index: int) -> bool:
        reminders = await self.get_active_reminders(user_id)
        if index < 1 or index > len(reminders):
            return False

        reminder_id = reminders[index - 1].id
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM reminders WHERE id = ? AND user_id = ?",
                (reminder_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_by_id(self, user_id: int, reminder_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM reminders WHERE id = ? AND user_id = ?",
                (reminder_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_due_reminders(self, now_dt: datetime) -> list[Reminder]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, user_id, chat_id, text, remind_at, is_sent
                FROM reminders
                WHERE is_sent = 0 AND remind_at <= ?
                ORDER BY remind_at ASC
                """,
                (now_dt.isoformat(),),
            )
            rows = await cursor.fetchall()

        result: list[Reminder] = []
        for row in rows:
            result.append(
                Reminder(
                    id=row[0],
                    user_id=row[1],
                    chat_id=row[2],
                    text=row[3],
                    remind_at=datetime.fromisoformat(row[4]),
                    is_sent=bool(row[5]),
                )
            )
        return result

    async def mark_sent(self, reminder_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE reminders SET is_sent = 1 WHERE id = ?", (reminder_id,))
            await db.commit()

    async def get_reminder_by_id(self, reminder_id: int) -> Reminder | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, user_id, chat_id, text, remind_at, is_sent
                FROM reminders
                WHERE id = ?
                """,
                (reminder_id,),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return Reminder(
            id=row[0],
            user_id=row[1],
            chat_id=row[2],
            text=row[3],
            remind_at=datetime.fromisoformat(row[4]),
            is_sent=bool(row[5]),
        )

    async def postpone_reminder(self, reminder_id: int, remind_at: datetime) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE reminders SET remind_at = ?, is_sent = 0 WHERE id = ?",
                (remind_at.isoformat(), reminder_id),
            )
            await db.commit()

    async def update_reminder(self, user_id: int, reminder_id: int, text: str, remind_at: datetime) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE reminders
                SET text = ?, remind_at = ?, is_sent = 0
                WHERE id = ? AND user_id = ?
                """,
                (text, remind_at.isoformat(), reminder_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def upsert_notification(self, reminder_id: int, message_id: int, sent_at: datetime) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO reminder_notifications (reminder_id, message_id, sent_at, responded, followup_sent)
                VALUES (?, ?, ?, 0, 0)
                ON CONFLICT(reminder_id) DO UPDATE SET
                    message_id=excluded.message_id,
                    sent_at=excluded.sent_at,
                    responded=0,
                    followup_sent=0
                """,
                (reminder_id, message_id, sent_at.isoformat()),
            )
            await db.commit()

    async def mark_notification_responded(self, reminder_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE reminder_notifications SET responded = 1 WHERE reminder_id = ?",
                (reminder_id,),
            )
            await db.commit()

    async def mark_followup_sent(self, reminder_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE reminder_notifications SET followup_sent = 1 WHERE reminder_id = ?",
                (reminder_id,),
            )
            await db.commit()

    async def get_pending_followups(self, now_dt: datetime) -> list[Reminder]:
        threshold = (now_dt - timedelta(minutes=5)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT r.id, r.user_id, r.chat_id, r.text, r.remind_at, r.is_sent
                FROM reminders r
                INNER JOIN reminder_notifications rn ON rn.reminder_id = r.id
                WHERE r.is_sent = 1
                  AND rn.responded = 0
                  AND rn.followup_sent = 0
                  AND rn.sent_at <= ?
                ORDER BY rn.sent_at ASC
                """,
                (threshold,),
            )
            rows = await cursor.fetchall()

        return [
            Reminder(
                id=row[0],
                user_id=row[1],
                chat_id=row[2],
                text=row[3],
                remind_at=datetime.fromisoformat(row[4]),
                is_sent=bool(row[5]),
            )
            for row in rows
        ]



