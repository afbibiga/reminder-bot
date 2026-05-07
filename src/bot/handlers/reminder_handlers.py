
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards.reminder_keyboards import (
    BTN_ADD,
    BTN_HELP,
    BTN_HOME,
    BTN_LIST,
    HELP_TEXT,
    edit_field_keyboard,
    format_reminder_line,
    main_menu_keyboard,
    reminder_item_keyboard,
    reminders_manage_keyboard,
)
from src.services.reminder_service import ReminderService, UserContext


class ReminderBot:
    def __init__(self, token: str, service: ReminderService, proxy_url: str = ""):
        session = AiohttpSession(proxy=proxy_url) if proxy_url else None
        self.bot = Bot(token=token, session=session)
        self.dp = Dispatcher()
        self.service = service
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

    async def _track_user(self, message: Message) -> None:
        if not message.from_user:
            return
        await self.service.track_user(
            UserContext(
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                language_code=message.from_user.language_code,
            )
        )

    async def _send_reminders_list(self, message: Message) -> None:
        if not message.from_user:
            await message.answer("Не удалось определить пользователя.")
            return
        reminders = await self.service.list_active_reminders(user_id=message.from_user.id)
        if not reminders:
            await message.answer("📋 У вас пока нет активных напоминаний.", reply_markup=main_menu_keyboard())
            return

        await message.answer("📋 ВАШИ НАПОМИНАНИЯ:")
        for idx, reminder in enumerate(reminders, start=1):
            await message.answer(
                format_reminder_line(idx, reminder.text, reminder.remind_at.astimezone(self.service.tz)),
                reply_markup=reminder_item_keyboard(reminder.id),
            )
        await message.answer("Что хотите сделать?", reply_markup=reminders_manage_keyboard())

    async def cmd_start(self, message: Message) -> None:
        await self._track_user(message)
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
            reply_markup=main_menu_keyboard(),
        )

    async def cmd_help(self, message: Message) -> None:
        await self._track_user(message)
        await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())

    async def cmd_add(self, message: Message) -> None:
        if not message.from_user:
            await message.answer("Не удалось определить пользователя.")
            return
        await self._track_user(message)

        payload = self.service.extract_payload(message.text)
        if not payload:
            self.awaiting_add_input.add(message.from_user.id)
            await message.answer(
                "✍️ Отправьте напоминание в формате:\nТекст ГГГГ-ММ-ДД ЧЧ:ММ\n\nПример:\nПозвонить врачу 2026-04-15 14:30",
                reply_markup=main_menu_keyboard(),
            )
            return

        try:
            reminder_id, text, remind_at = await self.service.create_reminder(message.from_user.id, message.chat.id, payload)
        except ValueError as exc:
            await message.answer(f"Ошибка: {exc}")
            return

        await message.answer(
            "✅ Напоминание добавлено\n"
            f"#{reminder_id}. {text}\n"
            f"🕒 {remind_at.strftime('%Y-%m-%d %H:%M')}",
            reply_markup=main_menu_keyboard(),
        )

    async def cmd_list(self, message: Message) -> None:
        await self._track_user(message)
        await self._send_reminders_list(message)

    async def cmd_delete(self, message: Message) -> None:
        if not message.from_user:
            await message.answer("Не удалось определить пользователя.")
            return
        await self._track_user(message)

        payload = self.service.extract_payload(message.text)
        if not payload or not payload.isdigit():
            await message.answer("Используйте: /delete [номер]. Например: /delete 1")
            return

        deleted = await self.service.delete_by_index(user_id=message.from_user.id, index=int(payload))
        if not deleted:
            await message.answer("Напоминание с таким номером не найдено.")
            return

        await message.answer(f"❌ Напоминание #{payload} удалено.", reply_markup=main_menu_keyboard())

    async def menu_add(self, message: Message) -> None:
        if not message.from_user:
            return
        await self._track_user(message)
        self.awaiting_add_input.add(message.from_user.id)
        await message.answer(
            "✍️ Напишите напоминание:\nПример: Примите Лозартан 50 мг 2026-04-15 08:00",
            reply_markup=main_menu_keyboard(),
        )

    async def menu_list(self, message: Message) -> None:
        await self._track_user(message)
        await self._send_reminders_list(message)

    async def menu_help(self, message: Message) -> None:
        await self._track_user(message)
        await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())

    async def menu_home(self, message: Message) -> None:
        await self.cmd_start(message)

    @staticmethod
    def _parse_callback_reminder_id(data: str) -> int | None:
        parts = data.split(":")
        if len(parts) < 3:
            return None
        return int(parts[-1]) if parts[-1].isdigit() else None

    async def handle_add_text_input(self, message: Message) -> None:
        if not message.from_user or not message.text:
            return
        if message.text.startswith("/") or message.text in {BTN_ADD, BTN_LIST, BTN_HELP, BTN_HOME}:
            return
        await self._track_user(message)

        user_id = message.from_user.id
        if user_id in self.awaiting_edit_input:
            reminder_id, field = self.awaiting_edit_input.pop(user_id)
            try:
                new_text, new_remind_at = await self.service.update_field(user_id, reminder_id, field, message.text)
            except ValueError as exc:
                await message.answer(str(exc))
                return
            await message.answer(
                "✏️ Изменения сохранены\n"
                f"#{reminder_id}. {new_text}\n"
                f"🕒 {new_remind_at.strftime('%Y-%m-%d %H:%M')}",
                reply_markup=main_menu_keyboard(),
            )
            return

        if user_id in self.awaiting_add_input:
            self.awaiting_add_input.discard(user_id)
            try:
                reminder_id, text, remind_at = await self.service.create_reminder(user_id, message.chat.id, message.text)
            except ValueError as exc:
                await message.answer(f"Ошибка: {exc}")
                return
            await message.answer(
                "✅ Напоминание добавлено\n"
                f"#{reminder_id}. {text}\n"
                f"🕒 {remind_at.strftime('%Y-%m-%d %H:%M')}",
                reply_markup=main_menu_keyboard(),
            )

    async def on_reminder_taken(self, callback: CallbackQuery) -> None:
        reminder_id = self._parse_callback_reminder_id(callback.data or "")
        if reminder_id is None or not callback.from_user:
            await callback.answer("Некорректные данные", show_alert=True)
            return
        if not await self.service.mark_taken(callback.from_user.id, reminder_id):
            await callback.answer("Напоминание не найдено", show_alert=True)
            return
        await callback.answer("Отмечено")
        if callback.message:
            await callback.message.answer("👍 Отлично! Записано.\nСледующее напоминание завтра в 8:00.")

    async def on_followup_yes(self, callback: CallbackQuery) -> None:
        await self.on_reminder_taken(callback)

    async def _postpone(self, callback: CallbackQuery, minutes: int, success_text: str) -> None:
        reminder_id = self._parse_callback_reminder_id(callback.data or "")
        if reminder_id is None or not callback.from_user:
            await callback.answer("Некорректные данные", show_alert=True)
            return
        new_time = await self.service.postpone(callback.from_user.id, reminder_id, minutes)
        if not new_time:
            await callback.answer("Напоминание не найдено", show_alert=True)
            return
        await callback.answer("Принято")
        if callback.message:
            await callback.message.answer(f"{success_text}\nНовое время: {new_time.strftime('%Y-%m-%d %H:%M')}", reply_markup=main_menu_keyboard())

    async def on_remind_later_10(self, callback: CallbackQuery) -> None:
        await self._postpone(callback, 10, "⏰ Ок, напомню через 10 минут.")

    async def on_remind_later_30(self, callback: CallbackQuery) -> None:
        await self._postpone(callback, 30, "⏰ Ок, напомню через 30 минут.")

    async def on_reminder_edit(self, callback: CallbackQuery) -> None:
        reminder_id = self._parse_callback_reminder_id(callback.data or "")
        if reminder_id is None or not callback.from_user:
            await callback.answer("Некорректные данные", show_alert=True)
            return
        if not await self.service.get_reminder_for_user(callback.from_user.id, reminder_id):
            await callback.answer("Напоминание не найдено", show_alert=True)
            return
        await callback.answer("Выберите, что изменить")
        if callback.message:
            await callback.message.answer("✏️ Что изменить в напоминании?", reply_markup=edit_field_keyboard(reminder_id))

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
        reminder = await self.service.get_reminder_for_user(callback.from_user.id, reminder_id)
        if not reminder:
            await callback.answer("Напоминание не найдено", show_alert=True)
            return

        self.awaiting_edit_input[callback.from_user.id] = (reminder_id, field)
        await callback.answer("Ожидаю ввод")
        if not callback.message:
            return

        if field == "text":
            await callback.message.answer(f"📝 Введите новый текст напоминания.\nТекущее: {reminder.text}", reply_markup=main_menu_keyboard())
        elif field == "date":
            date_value = reminder.remind_at.astimezone(self.service.tz).strftime("%Y-%m-%d")
            await callback.message.answer(f"📅 Введите новую дату в формате ГГГГ-ММ-ДД.\nТекущая: {date_value}", reply_markup=main_menu_keyboard())
        else:
            time_value = reminder.remind_at.astimezone(self.service.tz).strftime("%H:%M")
            await callback.message.answer(f"🕒 Введите новое время в формате ЧЧ:ММ.\nТекущее: {time_value}", reply_markup=main_menu_keyboard())

    async def on_delete_by_id(self, callback: CallbackQuery) -> None:
        reminder_id = self._parse_callback_reminder_id(callback.data or "")
        if reminder_id is None or not callback.from_user:
            await callback.answer("Некорректные данные", show_alert=True)
            return
        deleted = await self.service.delete_by_id(callback.from_user.id, reminder_id)
        if not deleted:
            await callback.answer("Напоминание не найдено", show_alert=True)
            return
        await callback.answer("Удалено")
        if callback.message:
            await callback.message.answer("❌ Напоминание удалено.", reply_markup=main_menu_keyboard())

    async def on_menu_callbacks(self, callback: CallbackQuery) -> None:
        data = callback.data or ""
        await callback.answer()
        if not callback.message:
            return
        if data == "menu:add":
            if callback.from_user:
                self.awaiting_add_input.add(callback.from_user.id)
            await callback.message.answer("✍️ Введите новое напоминание:", reply_markup=main_menu_keyboard())
        elif data == "menu:delete":
            await callback.message.answer("❌ Нажмите кнопку «УДАЛИТЬ» под нужным напоминанием в списке.")
        elif data == "menu:edit":
            await callback.message.answer("✏️ Нажмите кнопку «ИЗМЕНИТЬ» под нужным напоминанием в списке.")
        elif data == "menu:home":
            await callback.message.answer("🏠 Главное меню", reply_markup=main_menu_keyboard())

    async def unknown_command(self, message: Message) -> None:
        await self._track_user(message)
        await message.answer("Неизвестная команда. Нажмите «❓ ПОМОЩЬ».", reply_markup=main_menu_keyboard())

