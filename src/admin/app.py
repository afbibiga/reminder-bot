import html
import secrets

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.config.settings import AppConfig
from src.db.repositories.admin_repository import AdminRepository

security = HTTPBasic()

def create_admin_app(repo: AdminRepository, cfg: AppConfig) -> FastAPI:
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


