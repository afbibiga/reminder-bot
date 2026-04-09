import asyncio
import contextlib
import html
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials


DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
TIME_RE = re.compile(r"\b\d{2}:\d{2}\b")
security = HTTPBasic()

HELP_TEXT = (
    "💡 Как пользоваться:\n"
    "1) Нажмите «➕ ДОБАВИТЬ НАПОМИНАНИЕ»\n"
    "2) Отправьте: текст + дата/время\n"
    "Пример: Позвонить врачу 2026-04-15 14:30\n\n"
    "Как изменить напоминание:\n"
    "1) Нажмите «📋 МОИ НАПОМИНАНИЯ»\n"
    "2) Под нужным пунктом нажмите «✏️ ИЗМЕНИТЬ»\n"
    "3) Выберите, что менять: текст, дату или время\n\n"
    "Как удалить напоминание:\n"
    "1) Нажмите «📋 МОИ НАПОМИНАНИЯ»\n"
    "2) Под нужным пунктом нажмите «❌ УДАЛИТЬ»\n\n"
    "Правила времени:\n"
    "- Нет даты: сегодня + 1 час\n"
    "- Нет времени: 09:00"
)

BTN_ADD = "➕ ДОБАВИТЬ НАПОМИНАНИЕ"
BTN_LIST = "📋 МОИ НАПОМИНАНИЯ"
BTN_HELP = "❓ ПОМОЩЬ"
BTN_HOME = "🏠 ГЛАВНОЕ МЕНЮ"


@dataclass
class AppConfig:
    bot_token: str
    timezone: str
    db_path: Path
    admin_host: str
    admin_port: int
    admin_login: str
    admin_password: str
    admin_enabled: bool


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

    async def get_dashboard_stats(self) -> dict[str, int]:
        async with aiosqlite.connect(self.db_path) as db:
            users_cursor = await db.execute("SELECT COUNT(*) FROM users")
            users_count = await users_cursor.fetchone()
            total_cursor = await db.execute("SELECT COUNT(*) FROM reminders")
            total_count = await total_cursor.fetchone()
            active_cursor = await db.execute("SELECT COUNT(*) FROM reminders WHERE is_sent = 0")
            active_count = await active_cursor.fetchone()
            sent_cursor = await db.execute("SELECT COUNT(*) FROM reminders WHERE is_sent = 1")
            sent_count = await sent_cursor.fetchone()

        return {
            "users": int(users_count[0] if users_count else 0),
            "reminders_total": int(total_count[0] if total_count else 0),
            "reminders_active": int(active_count[0] if active_count else 0),
            "reminders_sent": int(sent_count[0] if sent_count else 0),
        }

    async def list_users(self, limit: int = 200) -> list[dict[str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT telegram_user_id, chat_id, username, first_name, last_name, language_code, created_at, updated_at
                FROM users
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()

        users: list[dict[str, str]] = []
        for row in rows:
            users.append(
                {
                    "telegram_user_id": str(row[0]),
                    "chat_id": str(row[1]),
                    "username": row[2] or "",
                    "first_name": row[3] or "",
                    "last_name": row[4] or "",
                    "language_code": row[5] or "",
                    "created_at": row[6] or "",
                    "updated_at": row[7] or "",
                }
            )
        return users


class ReminderBot:
    def __init__(self, token: str, repo: ReminderRepository, timezone: str):
        self.tz = ZoneInfo(timezone)
        self.repo = repo
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self._checker_task: asyncio.Task | None = None
        self.awaiting_add_input: set[int] = set()
        self.awaiting_edit_input: dict[int, tuple[int, str]] = {}

        self.dp.message.register(self.cmd_start, CommandStart())
        self.dp.message.register(self.cmd_help, Command("help"))
        self.dp.message.register(self.cmd_add, Command("add"))
        self.dp.message.register(self.cmd_list, Command("list"))
        self.dp.message.register(self.cmd_delete, Command("delete"))
        self.dp.message.register(self.menu_add, F.text == BTN_ADD)
        self.dp.message.register(self.menu_list, F.text == BTN_LIST)
        self.dp.message.register(self.menu_help, F.text == BTN_HELP)
        self.dp.message.register(self.menu_home, F.text == BTN_HOME)
        self.dp.message.register(self.handle_add_text_input, F.text)
        self.dp.callback_query.register(self.on_reminder_taken, F.data.startswith("reminder:take:"))
        self.dp.callback_query.register(self.on_remind_later_10, F.data.startswith("reminder:later10:"))
        self.dp.callback_query.register(self.on_reminder_edit, F.data.startswith("reminder:edit:"))
        self.dp.callback_query.register(self.on_edit_field_selected, F.data.startswith("reminder:editfield:"))
        self.dp.callback_query.register(self.on_followup_yes, F.data.startswith("reminder:yes:"))
        self.dp.callback_query.register(self.on_remind_later_30, F.data.startswith("reminder:later30:"))
        self.dp.callback_query.register(self.on_delete_by_id, F.data.startswith("reminder:delete:"))
        self.dp.callback_query.register(self.on_menu_callbacks, F.data.startswith("menu:"))
        self.dp.message.register(self.unknown_command, F.text.startswith("/"))

    def now(self) -> datetime:
        return datetime.now(tz=self.tz)

    async def track_user(self, message: Message) -> None:
        if not message.from_user:
            return
        await self.repo.upsert_user(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
        )

    @staticmethod
    def _extract_payload(message_text: str | None) -> str:
        if not message_text:
            return ""
        parts = message_text.split(maxsplit=1)
        if len(parts) == 1:
            return ""
        return parts[1].strip()

    @staticmethod
    def main_menu_keyboard() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=BTN_ADD)],
                [KeyboardButton(text=BTN_LIST)],
                [KeyboardButton(text=BTN_HELP)],
            ],
            resize_keyboard=True,
            input_field_placeholder="Выберите действие",
        )

    @staticmethod
    def reminder_actions_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Я ПРИНЯЛ(А)", callback_data=f"reminder:take:{reminder_id}"),
                    InlineKeyboardButton(text="⏰ НАПОМНИТЬ ЧЕРЕЗ 10 МИН", callback_data=f"reminder:later10:{reminder_id}"),
                ],
                [InlineKeyboardButton(text="✏️ ИЗМЕНИТЬ", callback_data=f"reminder:edit:{reminder_id}")],
            ]
        )

    @staticmethod
    def followup_actions_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ ДА", callback_data=f"reminder:yes:{reminder_id}"),
                    InlineKeyboardButton(text="⏰ НАПОМНИТЬ ЧЕРЕЗ 30 МИН", callback_data=f"reminder:later30:{reminder_id}"),
                ]
            ]
        )

    def reminders_manage_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="➕ ДОБАВИТЬ", callback_data="menu:add"),
                    InlineKeyboardButton(text="✏️ ИЗМЕНИТЬ", callback_data="menu:edit"),
                ],
                [
                    InlineKeyboardButton(text="❌ УДАЛИТЬ", callback_data="menu:delete"),
                    InlineKeyboardButton(text="🏠 ГЛАВНОЕ МЕНЮ", callback_data="menu:home"),
                ],
            ]
        )

    @staticmethod
    def reminder_item_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✏️ ИЗМЕНИТЬ", callback_data=f"reminder:edit:{reminder_id}"),
                    InlineKeyboardButton(text="❌ УДАЛИТЬ", callback_data=f"reminder:delete:{reminder_id}"),
                ]
            ]
        )

    @staticmethod
    def edit_field_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📝 ТЕКСТ", callback_data=f"reminder:editfield:text:{reminder_id}"),
                    InlineKeyboardButton(text="📅 ДАТА", callback_data=f"reminder:editfield:date:{reminder_id}"),
                    InlineKeyboardButton(text="🕒 ВРЕМЯ", callback_data=f"reminder:editfield:time:{reminder_id}"),
                ]
            ]
        )

    def parse_add_payload(self, payload: str) -> tuple[str, datetime]:
        payload = payload.strip()
        if not payload:
            raise ValueError("Не указан текст напоминания.")

        date_match = DATE_RE.search(payload)
        time_match = TIME_RE.search(payload)

        date_str = date_match.group(0) if date_match else None
        time_str = time_match.group(0) if time_match else None

        text = payload
        if date_match:
            text = text.replace(date_match.group(0), "", 1)
        if time_match:
            text = text.replace(time_match.group(0), "", 1)
        text = " ".join(text.split())

        if not text:
            raise ValueError("Не указан текст напоминания.")

        current = self.now()

        if date_str and time_str:
            remind_at = self._parse_datetime(date_str, time_str)
        elif date_str and not time_str:
            remind_at = self._parse_datetime(date_str, "09:00")
        elif not date_str and time_str:
            remind_at = self._parse_datetime(current.strftime("%Y-%m-%d"), time_str)
        else:
            remind_at = current + timedelta(hours=1)
            remind_at = remind_at.replace(second=0, microsecond=0)

        if remind_at <= current:
            raise ValueError("Время напоминания должно быть в будущем.")

        return text, remind_at

    def _parse_datetime(self, date_str: str, time_str: str) -> datetime:
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("Неверный формат даты/времени. Используйте ГГГГ-ММ-ДД ЧЧ:ММ") from exc
        return dt.replace(tzinfo=self.tz)

    def _parse_date_only(self, date_str: str) -> datetime:
        try:
            date_part = datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("Неверный формат даты. Используйте ГГГГ-ММ-ДД") from exc
        return datetime.combine(date_part, self.now().timetz()).replace(tzinfo=self.tz)

    def _parse_time_only(self, time_str: str) -> datetime:
        try:
            time_part = datetime.strptime(time_str.strip(), "%H:%M").time()
        except ValueError as exc:
            raise ValueError("Неверный формат времени. Используйте ЧЧ:ММ") from exc
        return datetime.combine(self.now().date(), time_part).replace(tzinfo=self.tz)

    async def create_reminder_from_payload(self, message: Message, payload: str) -> None:
        if not message.from_user:
            await message.answer("Не удалось определить пользователя.")
            return
        try:
            text, remind_at = self.parse_add_payload(payload)
        except ValueError as exc:
            await message.answer(f"Ошибка: {exc}")
            return

        reminder_id = await self.repo.add_reminder(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            text=text,
            remind_at=remind_at,
        )
        await message.answer(
            "✅ Напоминание добавлено\n"
            f"#{reminder_id}. {text}\n"
            f"🕒 {remind_at.strftime('%Y-%m-%d %H:%M')}",
            reply_markup=self.main_menu_keyboard(),
        )

    async def update_reminder_from_payload(self, message: Message, reminder_id: int, payload: str) -> None:
        if not message.from_user:
            await message.answer("Не удалось определить пользователя.")
            return
        try:
            text, remind_at = self.parse_add_payload(payload)
        except ValueError as exc:
            await message.answer(f"Ошибка: {exc}")
            return

        updated = await self.repo.update_reminder(
            user_id=message.from_user.id,
            reminder_id=reminder_id,
            text=text,
            remind_at=remind_at,
        )
        if not updated:
            await message.answer("Не удалось обновить напоминание. Возможно, оно уже удалено.")
            return

        await message.answer(
            "✏️ Напоминание обновлено\n"
            f"#{reminder_id}. {text}\n"
            f"🕒 {remind_at.strftime('%Y-%m-%d %H:%M')}",
            reply_markup=self.main_menu_keyboard(),
        )

    async def send_reminders_list(self, message: Message) -> None:
        if not message.from_user:
            await message.answer("Не удалось определить пользователя.")
            return
        reminders = await self.repo.get_active_reminders(user_id=message.from_user.id)
        if not reminders:
            await message.answer("📋 У вас пока нет активных напоминаний.", reply_markup=self.main_menu_keyboard())
            return

        await message.answer("📋 ВАШИ НАПОМИНАНИЯ:")
        for idx, reminder in enumerate(reminders, start=1):
            await message.answer(
                f"{idx}. {reminder.text}\n🕒 {reminder.remind_at.astimezone(self.tz).strftime('%Y-%m-%d %H:%M')}",
                reply_markup=self.reminder_item_keyboard(reminder.id),
            )
        await message.answer("Что хотите сделать?", reply_markup=self.reminders_manage_keyboard())

    async def cmd_start(self, message: Message) -> None:
        await self.track_user(message)
        await message.answer(
            "💊 ЗДРАВСТВУЙТЕ! Я — ЗАБОТЛИВЫЙ помощник\n\n"
            "Я буду напоминать вам о важном:\n"
            "✅ Вовремя выпить таблетки\n"
            "✅ Измерить давление\n"
            "✅ Сходить к врачу\n"
            "✅ Сделать зарядку\n"
            "✅ Другое\n\n"
            "Всё просто — вы говорите ЧТО и КОГДА, я напоминаю.\n\n"
            "Нажмите кнопку, чтобы начать:",
            reply_markup=self.main_menu_keyboard(),
        )

    async def cmd_help(self, message: Message) -> None:
        await self.track_user(message)
        await message.answer(HELP_TEXT, reply_markup=self.main_menu_keyboard())

    async def cmd_add(self, message: Message) -> None:
        if not message.from_user:
            await message.answer("Не удалось определить пользователя.")
            return

        await self.track_user(message)
        payload = self._extract_payload(message.text)
        if not payload:
            self.awaiting_add_input.add(message.from_user.id)
            await message.answer(
                "✍️ Отправьте напоминание в формате:\n"
                "Текст ГГГГ-ММ-ДД ЧЧ:ММ\n\n"
                "Пример:\n"
                "Позвонить врачу 2026-04-15 14:30",
                reply_markup=self.main_menu_keyboard(),
            )
            return
        await self.create_reminder_from_payload(message, payload)

    async def cmd_list(self, message: Message) -> None:
        if not message.from_user:
            await message.answer("Не удалось определить пользователя.")
            return

        await self.track_user(message)
        await self.send_reminders_list(message)

    async def cmd_delete(self, message: Message) -> None:
        if not message.from_user:
            await message.answer("Не удалось определить пользователя.")
            return

        await self.track_user(message)
        payload = self._extract_payload(message.text)
        if not payload or not payload.isdigit():
            await message.answer("Используйте: /delete [номер]. Например: /delete 1")
            return

        index = int(payload)
        deleted = await self.repo.delete_by_index(user_id=message.from_user.id, index=index)
        if not deleted:
            await message.answer("Напоминание с таким номером не найдено.")
            return

        await message.answer(f"❌ Напоминание #{index} удалено.", reply_markup=self.main_menu_keyboard())

    async def menu_add(self, message: Message) -> None:
        await self.track_user(message)
        if not message.from_user:
            return
        self.awaiting_add_input.add(message.from_user.id)
        await message.answer(
            "✍️ Напишите напоминание:\n"
            "Пример: Примите Лозартан 50 мг 2026-04-15 08:00",
            reply_markup=self.main_menu_keyboard(),
        )

    async def menu_list(self, message: Message) -> None:
        await self.track_user(message)
        await self.send_reminders_list(message)

    async def menu_help(self, message: Message) -> None:
        await self.track_user(message)
        await message.answer(HELP_TEXT, reply_markup=self.main_menu_keyboard())

    async def menu_home(self, message: Message) -> None:
        await self.track_user(message)
        await self.cmd_start(message)

    async def handle_add_text_input(self, message: Message) -> None:
        if not message.from_user or not message.text:
            return
        if message.text.startswith("/"):
            return
        if message.text in {BTN_ADD, BTN_LIST, BTN_HELP, BTN_HOME}:
            return
        await self.track_user(message)
        user_id = message.from_user.id

        if user_id in self.awaiting_edit_input:
            reminder_id, edit_mode = self.awaiting_edit_input.pop(user_id)
            reminder = await self.repo.get_reminder_by_id(reminder_id)
            if not reminder or reminder.user_id != user_id:
                await message.answer("Напоминание не найдено.")
                return

            try:
                if edit_mode == "text":
                    new_text = message.text.strip()
                    if not new_text:
                        raise ValueError("Текст не может быть пустым.")
                    new_remind_at = reminder.remind_at
                elif edit_mode == "date":
                    new_date = datetime.strptime(message.text.strip(), "%Y-%m-%d").date()
                    new_remind_at = datetime.combine(new_date, reminder.remind_at.timetz()).replace(tzinfo=self.tz)
                    new_text = reminder.text
                elif edit_mode == "time":
                    new_time = datetime.strptime(message.text.strip(), "%H:%M").time()
                    current_date = reminder.remind_at.astimezone(self.tz).date()
                    new_remind_at = datetime.combine(current_date, new_time).replace(tzinfo=self.tz)
                    new_text = reminder.text
                else:
                    await message.answer("Неизвестный режим изменения.")
                    return
            except ValueError:
                if edit_mode == "date":
                    await message.answer("Ошибка: формат даты должен быть ГГГГ-ММ-ДД")
                elif edit_mode == "time":
                    await message.answer("Ошибка: формат времени должен быть ЧЧ:ММ")
                else:
                    await message.answer("Ошибка: не удалось обработать новое значение.")
                return

            if new_remind_at <= self.now():
                await message.answer("Ошибка: новое время напоминания должно быть в будущем.")
                return

            updated = await self.repo.update_reminder(
                user_id=user_id,
                reminder_id=reminder_id,
                text=new_text,
                remind_at=new_remind_at,
            )
            if not updated:
                await message.answer("Не удалось обновить напоминание.")
                return
            await message.answer(
                "✏️ Изменения сохранены\n"
                f"#{reminder_id}. {new_text}\n"
                f"🕒 {new_remind_at.strftime('%Y-%m-%d %H:%M')}",
                reply_markup=self.main_menu_keyboard(),
            )
            return

        if user_id in self.awaiting_add_input:
            self.awaiting_add_input.discard(user_id)
            await self.create_reminder_from_payload(message, message.text)

    @staticmethod
    def _parse_callback_reminder_id(data: str) -> int | None:
        parts = data.split(":")
        if len(parts) < 3:
            return None
        candidate = parts[-1]
        if not candidate.isdigit():
            return None
        return int(candidate)

    async def on_reminder_taken(self, callback: CallbackQuery) -> None:
        reminder_id = self._parse_callback_reminder_id(callback.data or "")
        if reminder_id is None or not callback.from_user:
            await callback.answer("Некорректные данные", show_alert=True)
            return
        reminder = await self.repo.get_reminder_by_id(reminder_id)
        if not reminder or reminder.user_id != callback.from_user.id:
            await callback.answer("Напоминание не найдено", show_alert=True)
            return
        await self.repo.mark_notification_responded(reminder_id)
        await callback.answer("Отмечено")
        if callback.message:
            await callback.message.answer("👍 Отлично! Записано.\nСледующее напоминание завтра в 8:00.")

    async def on_followup_yes(self, callback: CallbackQuery) -> None:
        await self.on_reminder_taken(callback)

    async def on_remind_later_10(self, callback: CallbackQuery) -> None:
        await self._postpone_from_callback(callback, minutes=10, success_text="⏰ Ок, напомню через 10 минут.")

    async def on_remind_later_30(self, callback: CallbackQuery) -> None:
        await self._postpone_from_callback(callback, minutes=30, success_text="⏰ Ок, напомню через 30 минут.")

    async def _postpone_from_callback(self, callback: CallbackQuery, minutes: int, success_text: str) -> None:
        reminder_id = self._parse_callback_reminder_id(callback.data or "")
        if reminder_id is None or not callback.from_user:
            await callback.answer("Некорректные данные", show_alert=True)
            return
        reminder = await self.repo.get_reminder_by_id(reminder_id)
        if not reminder or reminder.user_id != callback.from_user.id:
            await callback.answer("Напоминание не найдено", show_alert=True)
            return

        new_time = self.now() + timedelta(minutes=minutes)
        new_time = new_time.replace(second=0, microsecond=0)
        await self.repo.postpone_reminder(reminder_id, new_time)
        await self.repo.mark_notification_responded(reminder_id)
        await callback.answer("Принято")
        if callback.message:
            await callback.message.answer(
                f"{success_text}\nНовое время: {new_time.strftime('%Y-%m-%d %H:%M')}",
                reply_markup=self.main_menu_keyboard(),
            )

    async def on_reminder_edit(self, callback: CallbackQuery) -> None:
        reminder_id = self._parse_callback_reminder_id(callback.data or "")
        if reminder_id is None or not callback.from_user:
            await callback.answer("Некорректные данные", show_alert=True)
            return
        reminder = await self.repo.get_reminder_by_id(reminder_id)
        if not reminder or reminder.user_id != callback.from_user.id:
            await callback.answer("Напоминание не найдено", show_alert=True)
            return

        await callback.answer("Выберите, что изменить")
        if callback.message:
            await callback.message.answer(
                "✏️ Что изменить в напоминании?",
                reply_markup=self.edit_field_keyboard(reminder_id),
            )

    async def on_edit_field_selected(self, callback: CallbackQuery) -> None:
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) != 4 or not callback.from_user:
            await callback.answer("Некорректные данные", show_alert=True)
            return
        _, _, field, reminder_id_raw = parts
        if field not in {"text", "date", "time"} or not reminder_id_raw.isdigit():
            await callback.answer("Некорректные данные", show_alert=True)
            return
        reminder_id = int(reminder_id_raw)
        reminder = await self.repo.get_reminder_by_id(reminder_id)
        if not reminder or reminder.user_id != callback.from_user.id:
            await callback.answer("Напоминание не найдено", show_alert=True)
            return

        self.awaiting_edit_input[callback.from_user.id] = (reminder_id, field)
        await callback.answer("Ожидаю ввод")
        if not callback.message:
            return

        if field == "text":
            await callback.message.answer(
                "📝 Введите новый текст напоминания.\n"
                f"Текущее: {reminder.text}",
                reply_markup=self.main_menu_keyboard(),
            )
        elif field == "date":
            await callback.message.answer(
                "📅 Введите новую дату в формате ГГГГ-ММ-ДД.\n"
                f"Текущая: {reminder.remind_at.astimezone(self.tz).strftime('%Y-%m-%d')}",
                reply_markup=self.main_menu_keyboard(),
            )
        else:
            await callback.message.answer(
                "🕒 Введите новое время в формате ЧЧ:ММ.\n"
                f"Текущее: {reminder.remind_at.astimezone(self.tz).strftime('%H:%M')}",
                reply_markup=self.main_menu_keyboard(),
            )

    async def on_delete_by_id(self, callback: CallbackQuery) -> None:
        reminder_id = self._parse_callback_reminder_id(callback.data or "")
        if reminder_id is None or not callback.from_user:
            await callback.answer("Некорректные данные", show_alert=True)
            return
        deleted = await self.repo.delete_by_id(callback.from_user.id, reminder_id)
        if not deleted:
            await callback.answer("Напоминание не найдено", show_alert=True)
            return
        await callback.answer("Удалено")
        if callback.message:
            await callback.message.answer("❌ Напоминание удалено.", reply_markup=self.main_menu_keyboard())

    async def on_menu_callbacks(self, callback: CallbackQuery) -> None:
        data = callback.data or ""
        await callback.answer()
        if not callback.message:
            return
        if data == "menu:add":
            if callback.from_user:
                self.awaiting_add_input.add(callback.from_user.id)
            await callback.message.answer("✍️ Введите новое напоминание:", reply_markup=self.main_menu_keyboard())
        elif data == "menu:delete":
            await callback.message.answer("❌ Нажмите кнопку «УДАЛИТЬ» под нужным напоминанием в списке.")
        elif data == "menu:edit":
            await callback.message.answer("✏️ Нажмите кнопку «ИЗМЕНИТЬ» под нужным напоминанием в списке.")
        elif data == "menu:home":
            await callback.message.answer("🏠 Главное меню", reply_markup=self.main_menu_keyboard())

    async def unknown_command(self, message: Message) -> None:
        await self.track_user(message)
        await message.answer("Неизвестная команда. Нажмите «❓ ПОМОЩЬ».", reply_markup=self.main_menu_keyboard())

    async def reminder_checker(self) -> None:
        while True:
            try:
                now_dt = self.now()
                due_reminders = await self.repo.get_due_reminders(now_dt=now_dt)

                for reminder in due_reminders:
                    try:
                        sent_message = await self.bot.send_message(
                            chat_id=reminder.chat_id,
                            text=(
                                "⏰ НАПОМИНАНИЕ\n\n"
                                f"💊 {reminder.text}"
                            ),
                            reply_markup=self.reminder_actions_keyboard(reminder.id),
                        )
                        await self.repo.mark_sent(reminder.id)
                        await self.repo.upsert_notification(reminder.id, sent_message.message_id, now_dt)
                    except Exception:
                        logging.exception("Failed to send reminder %s", reminder.id)

                pending_followups = await self.repo.get_pending_followups(now_dt=now_dt)
                for reminder in pending_followups:
                    try:
                        await self.bot.send_message(
                            chat_id=reminder.chat_id,
                            text=(
                                "☝️ НАПОМИНАЮ ЕЩЕ РАЗ\n\n"
                                f"💊 {reminder.text} ждет вас.\n"
                                "Принять сейчас?"
                            ),
                            reply_markup=self.followup_actions_keyboard(reminder.id),
                        )
                        await self.repo.mark_followup_sent(reminder.id)
                    except Exception:
                        logging.exception("Failed to send follow-up for reminder %s", reminder.id)

            except Exception:
                logging.exception("Reminder checker failed")

            await asyncio.sleep(60)

    async def run(self) -> None:
        self._checker_task = asyncio.create_task(self.reminder_checker())

        try:
            await self.dp.start_polling(self.bot)
        finally:
            if self._checker_task:
                self._checker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._checker_task
            await self.bot.session.close()


def create_admin_app(repo: ReminderRepository, cfg: AppConfig) -> FastAPI:
    app = FastAPI(title="Напомню обо всем — Админка")

    def authenticate(credentials: HTTPBasicCredentials = Depends(security)) -> str:
        login_ok = secrets.compare_digest(credentials.username, cfg.admin_login)
        password_ok = secrets.compare_digest(credentials.password, cfg.admin_password)
        if not (login_ok and password_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        return credentials.username

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/admin", response_class=HTMLResponse)
    async def dashboard(_: str = Depends(authenticate)) -> str:
        stats = await repo.get_dashboard_stats()
        users = await repo.list_users(limit=200)

        rows = []
        for user in users:
            rows.append(
                "<tr>"
                f"<td>{html.escape(user['telegram_user_id'])}</td>"
                f"<td>{html.escape(user['chat_id'])}</td>"
                f"<td>{html.escape(user['username'])}</td>"
                f"<td>{html.escape((user['first_name'] + ' ' + user['last_name']).strip())}</td>"
                f"<td>{html.escape(user['language_code'])}</td>"
                f"<td>{html.escape(user['created_at'])}</td>"
                f"<td>{html.escape(user['updated_at'])}</td>"
                "</tr>"
            )

        users_table = "\n".join(rows) if rows else "<tr><td colspan='7'>Пользователей пока нет</td></tr>"

        return f"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <title>Напомню обо всем — Админка</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #f6f8fb; color: #1f2937; }}
    h1 {{ margin: 0 0 16px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .card {{ background: white; border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; }}
    .k {{ font-size: 12px; color: #6b7280; }}
    .v {{ font-size: 24px; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 10px; text-align: left; font-size: 13px; }}
    th {{ background: #f9fafb; }}
  </style>
</head>
<body>
  <h1>Напомню обо всем — Админка</h1>
  <div class="cards">
    <div class="card"><div class="k">Пользователи</div><div class="v">{stats['users']}</div></div>
    <div class="card"><div class="k">Напоминаний всего</div><div class="v">{stats['reminders_total']}</div></div>
    <div class="card"><div class="k">Активных</div><div class="v">{stats['reminders_active']}</div></div>
    <div class="card"><div class="k">Отправленных</div><div class="v">{stats['reminders_sent']}</div></div>
  </div>
  <h2>Пользователи</h2>
  <table>
    <thead>
      <tr>
        <th>Telegram user ID</th>
        <th>Chat ID</th>
        <th>Username</th>
        <th>Имя</th>
        <th>Язык</th>
        <th>Создан</th>
        <th>Обновлен</th>
      </tr>
    </thead>
    <tbody>
      {users_table}
    </tbody>
  </table>
</body>
</html>
"""

    return app


def str_to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_config() -> AppConfig:
    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, encoding="utf-8-sig")
    else:
        load_dotenv(encoding="utf-8-sig")

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не задана")

    return AppConfig(
        bot_token=token,
        timezone=os.getenv("BOT_TIMEZONE", "Asia/Irkutsk"),
        db_path=Path(os.getenv("BOT_DB_PATH", "reminders.db")),
        admin_host=os.getenv("ADMIN_HOST", "127.0.0.1"),
        admin_port=int(os.getenv("ADMIN_PORT", "8080")),
        admin_login=os.getenv("ADMIN_LOGIN", "admin"),
        admin_password=os.getenv("ADMIN_PASSWORD", "change_me_now"),
        admin_enabled=str_to_bool(os.getenv("ADMIN_ENABLED"), True),
    )


async def run_admin(repo: ReminderRepository, cfg: AppConfig) -> None:
    app = create_admin_app(repo, cfg)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=cfg.admin_host,
            port=cfg.admin_port,
            log_level="info",
        )
    )
    await server.serve()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    cfg = load_config()
    repo = ReminderRepository(db_path=cfg.db_path)
    await repo.init()
    bot = ReminderBot(token=cfg.bot_token, repo=repo, timezone=cfg.timezone)

    tasks = [asyncio.create_task(bot.run())]

    if cfg.admin_enabled:
        tasks.append(asyncio.create_task(run_admin(repo=repo, cfg=cfg)))
        logging.info("Admin panel: http://%s:%s/admin", cfg.admin_host, cfg.admin_port)

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    for task in pending:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    for task in done:
        exc = task.exception()
        if exc:
            raise exc


if __name__ == "__main__":
    asyncio.run(main())

