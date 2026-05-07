import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.db.repositories.reminder_repository import Reminder, ReminderRepository

DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
TIME_RE = re.compile(r"\b\d{2}:\d{2}\b")


@dataclass
class UserContext:
    user_id: int
    chat_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None


class ReminderService:
    def __init__(self, repo: ReminderRepository, timezone: str):
        self.repo = repo
        self.tz = ZoneInfo(timezone)

    def now(self) -> datetime:
        return datetime.now(tz=self.tz)

    async def track_user(self, user: UserContext) -> None:
        await self.repo.upsert_user(
            user_id=user.user_id,
            chat_id=user.chat_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
        )

    @staticmethod
    def extract_payload(message_text: str | None) -> str:
        if not message_text:
            return ""
        parts = message_text.split(maxsplit=1)
        if len(parts) == 1:
            return ""
        return parts[1].strip()

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
            remind_at = (current + timedelta(hours=1)).replace(second=0, microsecond=0)

        if remind_at <= current:
            raise ValueError("Время напоминания должно быть в будущем.")
        return text, remind_at

    def _parse_datetime(self, date_str: str, time_str: str) -> datetime:
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("Неверный формат даты/времени. Используйте ГГГГ-ММ-ДД ЧЧ:ММ") from exc
        return dt.replace(tzinfo=self.tz)

    async def create_reminder(self, user_id: int, chat_id: int, payload: str) -> tuple[int, str, datetime]:
        text, remind_at = self.parse_add_payload(payload)
        reminder_id = await self.repo.add_reminder(user_id=user_id, chat_id=chat_id, text=text, remind_at=remind_at)
        return reminder_id, text, remind_at

    async def list_active_reminders(self, user_id: int) -> list[Reminder]:
        return await self.repo.get_active_reminders(user_id=user_id)

    async def delete_by_index(self, user_id: int, index: int) -> bool:
        return await self.repo.delete_by_index(user_id=user_id, index=index)

    async def delete_by_id(self, user_id: int, reminder_id: int) -> bool:
        return await self.repo.delete_by_id(user_id=user_id, reminder_id=reminder_id)

    async def get_reminder_for_user(self, user_id: int, reminder_id: int) -> Reminder | None:
        reminder = await self.repo.get_reminder_by_id(reminder_id)
        if not reminder or reminder.user_id != user_id:
            return None
        return reminder

    async def update_field(self, user_id: int, reminder_id: int, field: str, raw_value: str) -> tuple[str, datetime]:
        reminder = await self.get_reminder_for_user(user_id, reminder_id)
        if not reminder:
            raise ValueError("Напоминание не найдено.")

        if field == "text":
            new_text = raw_value.strip()
            if not new_text:
                raise ValueError("Текст не может быть пустым.")
            new_remind_at = reminder.remind_at
        elif field == "date":
            try:
                new_date = datetime.strptime(raw_value.strip(), "%Y-%m-%d").date()
            except ValueError as exc:
                raise ValueError("Ошибка: формат даты должен быть ГГГГ-ММ-ДД") from exc
            new_remind_at = datetime.combine(new_date, reminder.remind_at.timetz()).replace(tzinfo=self.tz)
            new_text = reminder.text
        elif field == "time":
            try:
                new_time = datetime.strptime(raw_value.strip(), "%H:%M").time()
            except ValueError as exc:
                raise ValueError("Ошибка: формат времени должен быть ЧЧ:ММ") from exc
            current_date = reminder.remind_at.astimezone(self.tz).date()
            new_remind_at = datetime.combine(current_date, new_time).replace(tzinfo=self.tz)
            new_text = reminder.text
        else:
            raise ValueError("Неизвестный режим изменения.")

        if new_remind_at <= self.now():
            raise ValueError("Ошибка: новое время напоминания должно быть в будущем.")

        updated = await self.repo.update_reminder(user_id=user_id, reminder_id=reminder_id, text=new_text, remind_at=new_remind_at)
        if not updated:
            raise ValueError("Не удалось обновить напоминание.")
        return new_text, new_remind_at

    async def mark_taken(self, user_id: int, reminder_id: int) -> bool:
        reminder = await self.get_reminder_for_user(user_id, reminder_id)
        if not reminder:
            return False
        await self.repo.mark_notification_responded(reminder_id)
        return True

    async def postpone(self, user_id: int, reminder_id: int, minutes: int) -> datetime | None:
        reminder = await self.get_reminder_for_user(user_id, reminder_id)
        if not reminder:
            return None
        new_time = (self.now() + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
        await self.repo.postpone_reminder(reminder_id, new_time)
        await self.repo.mark_notification_responded(reminder_id)
        return new_time

    async def get_due(self) -> list[Reminder]:
        return await self.repo.get_due_reminders(now_dt=self.now())

    async def mark_sent(self, reminder_id: int, message_id: int) -> None:
        now_dt = self.now()
        await self.repo.mark_sent(reminder_id)
        await self.repo.upsert_notification(reminder_id, message_id, now_dt)

    async def get_pending_followups(self) -> list[Reminder]:
        return await self.repo.get_pending_followups(now_dt=self.now())

    async def mark_followup_sent(self, reminder_id: int) -> None:
        await self.repo.mark_followup_sent(reminder_id)
