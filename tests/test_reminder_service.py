import asyncio
from datetime import datetime

from src.db.repositories.reminder_repository import ReminderRepository
from src.services.reminder_service import ReminderService


class FixedNowReminderService(ReminderService):
    def __init__(self, repo: ReminderRepository, timezone: str, fixed_now: datetime):
        super().__init__(repo=repo, timezone=timezone)
        self._fixed_now = fixed_now

    def now(self) -> datetime:
        return self._fixed_now


def test_parse_add_payload_with_full_datetime(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    repo = ReminderRepository(db_path=db_path)
    service = FixedNowReminderService(
        repo=repo,
        timezone="Asia/Irkutsk",
        fixed_now=datetime(2026, 5, 7, 10, 0, tzinfo=ReminderService(repo, "Asia/Irkutsk").tz),
    )

    text, remind_at = service.parse_add_payload("Позвонить врачу 2026-05-08 14:30")

    assert text == "Позвонить врачу"
    assert remind_at.strftime("%Y-%m-%d %H:%M") == "2026-05-08 14:30"


def test_parse_add_payload_without_date_and_time_sets_plus_one_hour(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    repo = ReminderRepository(db_path=db_path)
    tz = ReminderService(repo, "Asia/Irkutsk").tz
    service = FixedNowReminderService(
        repo=repo,
        timezone="Asia/Irkutsk",
        fixed_now=datetime(2026, 5, 7, 10, 5, 33, tzinfo=tz),
    )

    text, remind_at = service.parse_add_payload("Проверить давление")

    assert text == "Проверить давление"
    assert remind_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-05-07 11:05:00"


def test_create_list_update_delete_reminder_flow(tmp_path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "test.db"
        repo = ReminderRepository(db_path=db_path)
        await repo.init()

        tz = ReminderService(repo, "Asia/Irkutsk").tz
        service = FixedNowReminderService(
            repo=repo,
            timezone="Asia/Irkutsk",
            fixed_now=datetime(2026, 5, 7, 10, 0, 0, tzinfo=tz),
        )

        reminder_id, text, remind_at = await service.create_reminder(
            user_id=1,
            chat_id=100,
            payload="Принять лекарство 2026-05-07 12:30",
        )
        assert reminder_id > 0
        assert text == "Принять лекарство"
        assert remind_at.strftime("%H:%M") == "12:30"

        reminders = await service.list_active_reminders(user_id=1)
        assert len(reminders) == 1
        assert reminders[0].id == reminder_id

        new_text, new_remind_at = await service.update_field(
            user_id=1,
            reminder_id=reminder_id,
            field="time",
            raw_value="13:15",
        )
        assert new_text == "Принять лекарство"
        assert new_remind_at.strftime("%H:%M") == "13:15"

        deleted = await service.delete_by_id(user_id=1, reminder_id=reminder_id)
        assert deleted is True
        assert await service.list_active_reminders(user_id=1) == []

    asyncio.run(scenario())
