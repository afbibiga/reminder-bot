import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class AppConfig:
    bot_token: str
    bot_proxy_url: str
    timezone: str
    db_path: Path
    admin_host: str
    admin_port: int
    admin_login: str
    admin_password: str
    admin_enabled: bool


def str_to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_config() -> AppConfig:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, encoding="utf-8-sig")
    else:
        load_dotenv(encoding="utf-8-sig")

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не задана")

    return AppConfig(
        bot_token=token,
        bot_proxy_url=os.getenv("BOT_PROXY_URL", "").strip(),
        timezone=os.getenv("BOT_TIMEZONE", "Asia/Irkutsk"),
        db_path=Path(os.getenv("BOT_DB_PATH", "reminders.db")),
        admin_host=os.getenv("ADMIN_HOST", "127.0.0.1"),
        admin_port=int(os.getenv("ADMIN_PORT", "8080")),
        admin_login=os.getenv("ADMIN_LOGIN", "admin"),
        admin_password=os.getenv("ADMIN_PASSWORD", "change_me_now"),
        admin_enabled=str_to_bool(os.getenv("ADMIN_ENABLED"), True),
    )
