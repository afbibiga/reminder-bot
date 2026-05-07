from pathlib import Path

import pytest

from src.config.settings import load_config, str_to_bool


def test_str_to_bool_parses_truthy_values() -> None:
    assert str_to_bool("1", False) is True
    assert str_to_bool("true", False) is True
    assert str_to_bool("YES", False) is True


def test_str_to_bool_returns_default_for_none() -> None:
    assert str_to_bool(None, True) is True
    assert str_to_bool(None, False) is False


def test_load_config_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("BOT_TIMEZONE", "Asia/Irkutsk")
    monkeypatch.setenv("BOT_DB_PATH", "custom.db")
    monkeypatch.setenv("ADMIN_HOST", "0.0.0.0")
    monkeypatch.setenv("ADMIN_PORT", "9000")
    monkeypatch.setenv("ADMIN_LOGIN", "admin_user")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("ADMIN_ENABLED", "false")
    monkeypatch.setenv("BOT_PROXY_URL", "http://proxy.local:8080")

    cfg = load_config()

    assert cfg.bot_token == "token"
    assert cfg.timezone == "Asia/Irkutsk"
    assert cfg.db_path == Path("custom.db")
    assert cfg.admin_host == "0.0.0.0"
    assert cfg.admin_port == 9000
    assert cfg.admin_login == "admin_user"
    assert cfg.admin_password == "secret"
    assert cfg.admin_enabled is False
    assert cfg.bot_proxy_url == "http://proxy.local:8080"


def test_load_config_raises_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "")

    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        load_config()
