"""Shared test fixtures."""

import os
import pytest


@pytest.fixture(autouse=True)
def set_required_env(monkeypatch):
    """Inject minimum required env vars so Settings() always constructs."""
    defaults = {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "ADMIN_TELEGRAM_ID": "999888777",
    }
    for key, value in defaults.items():
        if key not in os.environ:
            monkeypatch.setenv(key, value)


@pytest.fixture
def minimal_settings(tmp_path):
    """Settings instance with safe test values."""
    from src.config.settings import Settings

    return Settings(
        telegram_bot_token="1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        admin_telegram_id=999888777,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )
