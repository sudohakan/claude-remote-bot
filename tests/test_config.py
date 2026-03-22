"""Tests for config/settings.py and config/features.py."""

import pytest
from pydantic import ValidationError

from src.config.features import FeatureFlags
from src.config.settings import Settings


class TestSettings:
    def test_minimal_construction(self, minimal_settings):
        s = minimal_settings
        assert s.admin_telegram_id == 999888777
        assert s.telegram_token_str.startswith("1234567890")

    def test_defaults(self, minimal_settings):
        s = minimal_settings
        assert s.claude_max_turns == 10
        assert s.claude_timeout_seconds == 120
        assert s.claude_max_cost_per_user == 5.0
        assert s.enable_tunnel is False
        assert s.enable_monitor is True
        assert s.log_level == "INFO"

    def test_log_level_normalised(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOG_LEVEL", "debug")
        s = Settings(
            telegram_bot_token="1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            admin_telegram_id=1,
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
        )
        assert s.log_level == "DEBUG"

    def test_invalid_log_level(self, tmp_path):
        with pytest.raises(ValidationError):
            Settings(
                telegram_bot_token="1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                admin_telegram_id=1,
                database_url=f"sqlite:///{tmp_path / 'test.db'}",
                log_level="VERBOSE",
            )

    def test_timeout_too_low(self, tmp_path):
        with pytest.raises(ValidationError):
            Settings(
                telegram_bot_token="1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                admin_telegram_id=1,
                database_url=f"sqlite:///{tmp_path / 'test.db'}",
                claude_timeout_seconds=10,
            )

    def test_database_path_property(self, minimal_settings):
        path = minimal_settings.database_path
        assert str(path).endswith("test.db")

    def test_secret_not_leaked_in_repr(self, minimal_settings):
        r = repr(minimal_settings)
        assert "AAAAAAAAAAAAA" not in r

    def test_telegram_token_str(self, minimal_settings):
        token = minimal_settings.telegram_token_str
        assert "1234567890" in token

    def test_anthropic_api_key_str_none(self, minimal_settings):
        assert minimal_settings.anthropic_api_key_str is None

    def test_anthropic_api_key_str_set(self, tmp_path):
        s = Settings(
            telegram_bot_token="1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            admin_telegram_id=1,
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            anthropic_api_key="sk-ant-test-key",
        )
        assert s.anthropic_api_key_str == "sk-ant-test-key"

    def test_is_debug_from_flag(self, tmp_path):
        s = Settings(
            telegram_bot_token="1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            admin_telegram_id=1,
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            debug=True,
        )
        assert s.is_debug is True

    def test_is_debug_from_log_level(self, tmp_path):
        s = Settings(
            telegram_bot_token="1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            admin_telegram_id=1,
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            log_level="DEBUG",
        )
        assert s.is_debug is True


class TestFeatureFlags:
    def test_defaults(self, minimal_settings):
        ff = FeatureFlags(minimal_settings)
        assert ff.tunnel_enabled is False
        assert ff.monitor_enabled is True
        assert ff.file_uploads_enabled is True
        assert ff.git_integration_enabled is True
        assert ff.agentic_mode_enabled is True

    def test_is_enabled_known(self, minimal_settings):
        ff = FeatureFlags(minimal_settings)
        assert ff.is_enabled("monitor") is True
        assert ff.is_enabled("tunnel") is False

    def test_is_enabled_unknown(self, minimal_settings):
        ff = FeatureFlags(minimal_settings)
        assert ff.is_enabled("nonexistent_feature") is False

    def test_enabled_list(self, minimal_settings):
        ff = FeatureFlags(minimal_settings)
        enabled = ff.enabled_list()
        assert "monitor" in enabled
        assert "tunnel" not in enabled
        assert "file_uploads" in enabled

    def test_tunnel_enabled(self, tmp_path):
        s = Settings(
            telegram_bot_token="1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            admin_telegram_id=1,
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            enable_tunnel=True,
        )
        ff = FeatureFlags(s)
        assert ff.tunnel_enabled is True
        assert "tunnel" in ff.enabled_list()
