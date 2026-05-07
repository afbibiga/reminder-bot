import asyncio
from contextlib import suppress
from typing import Any

from fastapi import FastAPI

from src.admin.app import create_admin_app
from src.app.main import setup_logging
from src.config.settings import load_config
from src.db.repositories.admin_repository import AdminRepository
from src.db.repositories.reminder_repository import ReminderRepository
from src.bot.handlers.reminder_handlers import ReminderBot
from src.bot.poller import run_bot
from src.services.reminder_service import ReminderService


app = FastAPI(title="Напомню обо всем")


@app.on_event("startup")
async def on_startup() -> None:
    setup_logging()
    cfg = load_config()

    repo = ReminderRepository(db_path=cfg.db_path)
    await repo.init()

    service = ReminderService(repo=repo, timezone=cfg.timezone)
    bot = ReminderBot(token=cfg.bot_token, service=service, proxy_url=cfg.bot_proxy_url)
    bot_task = asyncio.create_task(run_bot(bot))

    app.state.cfg = cfg
    app.state.bot = bot
    app.state.bot_task = bot_task

    if cfg.admin_enabled:
        admin_repo = AdminRepository(db_path=str(cfg.db_path))
        admin_app = create_admin_app(admin_repo, cfg)
        app.mount("/", admin_app)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    bot_task: asyncio.Task[Any] | None = getattr(app.state, "bot_task", None)
    if bot_task:
        bot_task.cancel()
        with suppress(asyncio.CancelledError):
            await bot_task


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
