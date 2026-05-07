import asyncio
import contextlib
import logging

from src.bot.handlers.reminder_handlers import ReminderBot
from src.bot.keyboards.reminder_keyboards import followup_actions_keyboard, reminder_actions_keyboard


async def reminder_checker(bot: ReminderBot) -> None:
    while True:
        try:
            due_reminders = await bot.service.get_due()
            for reminder in due_reminders:
                try:
                    sent_message = await bot.bot.send_message(
                        chat_id=reminder.chat_id,
                        text="⏰ НАПОМИНАНИЕ\n\n" f"💊 {reminder.text}",
                        reply_markup=reminder_actions_keyboard(reminder.id),
                    )
                    await bot.service.mark_sent(reminder.id, sent_message.message_id)
                except Exception:
                    logging.exception("Failed to send reminder %s", reminder.id)

            pending_followups = await bot.service.get_pending_followups()
            for reminder in pending_followups:
                try:
                    await bot.bot.send_message(
                        chat_id=reminder.chat_id,
                        text="☝️ НАПОМИНАЮ ЕЩЕ РАЗ\n\n" f"💊 {reminder.text} ждет вас.\nПринять сейчас?",
                        reply_markup=followup_actions_keyboard(reminder.id),
                    )
                    await bot.service.mark_followup_sent(reminder.id)
                except Exception:
                    logging.exception("Failed to send follow-up for reminder %s", reminder.id)
        except Exception:
            logging.exception("Reminder checker failed")
        await asyncio.sleep(60)


async def run_bot(bot: ReminderBot) -> None:
    checker_task = asyncio.create_task(reminder_checker(bot))
    try:
        await bot.dp.start_polling(bot.bot)
    finally:
        checker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await checker_task
        await bot.bot.session.close()
