"""
Модуль для работы бота через webhook (для Amvera и других облачных платформ).
"""

import asyncio
import contextlib
import logging

from aiogram.types import Update

from src.bot.handlers.reminder_handlers import ReminderBot
from src.bot.keyboards.reminder_keyboards import (
    followup_actions_keyboard,
    reminder_actions_keyboard,
)


async def reminder_checker(bot: ReminderBot) -> None:
    """
    Фоновая задача для проверки напоминаний каждую минуту.
    
    Args:
        bot: Экземпляр ReminderBot
    """
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


async def setup_webhook(bot: ReminderBot, webhook_url: str) -> asyncio.Task:
    """
    Настраивает webhook для бота и запускает фоновую задачу проверки напоминаний.
    
    Args:
        bot: Экземпляр ReminderBot
        webhook_url: URL для webhook (например, https://your-app.amvera.ru/webhook)
    
    Returns:
        Task с фоновой задачей проверки напоминаний
    """
    await bot.bot.delete_webhook(drop_pending_updates=True)
    await bot.bot.set_webhook(webhook_url)
    logging.info("Webhook установлен: %s", webhook_url)
    
    checker_task = asyncio.create_task(reminder_checker(bot))
    return checker_task


async def process_update(bot: ReminderBot, update: Update) -> None:
    """
    Обрабатывает входящий Update от Telegram.
    
    Args:
        bot: Экземпляр ReminderBot
        update: Update от Telegram API
    """
    await bot.dp.feed_update(bot.bot, update)


async def shutdown_webhook(bot: ReminderBot, checker_task: asyncio.Task) -> None:
    """
    Корректно завершает работу webhook.
    
    Args:
        bot: Экземпляр ReminderBot
        checker_task: Task с фоновой задачей проверки напоминаний
    """
    checker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await checker_task
    await bot.bot.delete_webhook()
    await bot.bot.session.close()
    logging.info("Webhook остановлен")
