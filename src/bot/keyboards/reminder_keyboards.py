from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

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


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_ADD)], [KeyboardButton(text=BTN_LIST)], [KeyboardButton(text=BTN_HELP)]],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


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


def followup_actions_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ ДА", callback_data=f"reminder:yes:{reminder_id}"),
                InlineKeyboardButton(text="⏰ НАПОМНИТЬ ЧЕРЕЗ 30 МИН", callback_data=f"reminder:later30:{reminder_id}"),
            ]
        ]
    )


def reminders_manage_keyboard() -> InlineKeyboardMarkup:
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


def reminder_item_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ ИЗМЕНИТЬ", callback_data=f"reminder:edit:{reminder_id}"),
                InlineKeyboardButton(text="❌ УДАЛИТЬ", callback_data=f"reminder:delete:{reminder_id}"),
            ]
        ]
    )


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


def format_reminder_line(index: int, text: str, remind_at: datetime) -> str:
    return f"{index}. {text}\n🕒 {remind_at.strftime('%Y-%m-%d %H:%M')}"
