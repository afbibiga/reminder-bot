import asyncio
import logging
from contextlib import suppress
from typing import Any

from aiogram.types import Update
from fastapi import FastAPI, Request

from src.admin.app import create_admin_app
from src.app.main import setup_logging
from src.config.settings import load_config
from src.db.repositories.admin_repository import AdminRepository
from src.db.repositories.reminder_repository import ReminderRepository
from src.bot.handlers.reminder_handlers import ReminderBot
from src.bot.poller import run_bot
from src.bot.webhook import process_update, setup_webhook, shutdown_webhook
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

    app.state.cfg = cfg
    app.state.bot = bot

    if cfg.webhook_enabled:
        if not cfg.webhook_url:
            raise RuntimeError("WEBHOOK_URL должен быть задан при WEBHOOK_ENABLED=true")
        logging.info("Запуск в режиме WEBHOOK: %s", cfg.webhook_url)
        checker_task = await setup_webhook(bot, cfg.webhook_url)
        app.state.bot_task = checker_task
    else:
        logging.info("Запуск в режиме POLLING")
        bot_task = asyncio.create_task(run_bot(bot))
        app.state.bot_task = bot_task

    if cfg.admin_enabled:
        admin_repo = AdminRepository(db_path=str(cfg.db_path))
        admin_app = create_admin_app(admin_repo, cfg)
        app.mount("/", admin_app)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    cfg = getattr(app.state, "cfg", None)
    bot = getattr(app.state, "bot", None)
    bot_task: asyncio.Task[Any] | None = getattr(app.state, "bot_task", None)
    
    if cfg and cfg.webhook_enabled and bot and bot_task:
        await shutdown_webhook(bot, bot_task)
    elif bot_task:
        bot_task.cancel()
        with suppress(asyncio.CancelledError):
            await bot_task


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, str]:
    """
    Endpoint для приема обновлений от Telegram через webhook.
    """
    bot = getattr(app.state, "bot", None)
    if not bot:
        logging.error("Bot не инициализирован")
        return {"status": "error", "message": "Bot not initialized"}
    
    try:
        update_data = await request.json()
        update = Update(**update_data)
        await process_update(bot, update)
        return {"status": "ok"}
    except Exception as exc:
        logging.exception("Ошибка обработки webhook: %s", exc)
        return {"status": "error", "message": str(exc)}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
