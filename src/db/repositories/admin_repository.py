import aiosqlite


class AdminRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

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
