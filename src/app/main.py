import asyncio
import contextlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from src.admin.app import create_admin_app
from src.config.settings import AppConfig, load_config
from src.db.repositories.admin_repository import AdminRepository
from src.db.repositories.reminder_repository import ReminderRepository
from src.services.reminder_service import ReminderService
from src.bot.handlers.reminder_handlers import ReminderBot
from src.bot.poller import run_bot


def setup_logging() -> None:
    project_root = Path(__file__).resolve().parents[2]
    logs_dir = project_root / "data" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "app.log"

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


async def run_admin(repo: AdminRepository, cfg: AppConfig) -> None:
    app = create_admin_app(repo, cfg)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=cfg.admin_host,
            port=cfg.admin_port,
            log_level="info",
        )
    )
    await server.serve()


async def main() -> None:
    setup_logging()

    cfg = load_config()
    repo = ReminderRepository(db_path=cfg.db_path)
    admin_repo = AdminRepository(db_path=str(cfg.db_path))
    await repo.init()
    if cfg.bot_proxy_url:
        logging.info("Proxy for bot is enabled")
    service = ReminderService(repo=repo, timezone=cfg.timezone)
    bot = ReminderBot(token=cfg.bot_token, service=service, proxy_url=cfg.bot_proxy_url)

    tasks = [asyncio.create_task(run_bot(bot))]

    if cfg.admin_enabled:
        tasks.append(asyncio.create_task(run_admin(repo=admin_repo, cfg=cfg)))
        logging.info("Admin panel: http://%s:%s/admin", cfg.admin_host, cfg.admin_port)

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    for task in pending:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    for task in done:
        exc = task.exception()
        if exc:
            raise exc
