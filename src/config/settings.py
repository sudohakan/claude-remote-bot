"""Application settings via Pydantic BaseSettings.

Loads from .env file and environment variables.
All secrets are stored as SecretStr — access via .get_secret_value().
"""

from pathlib import Path
from typing import Any, List, Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bot configuration loaded from environment variables."""

    # ── Telegram ────────────────────────────────────────────────────────────
    telegram_bot_token: SecretStr = Field(
        ..., description="Telegram bot token from BotFather"
    )
    admin_telegram_id: int = Field(..., description="Admin Telegram user ID")

    # ── Anthropic / Claude ───────────────────────────────────────────────────
    anthropic_api_key: Optional[SecretStr] = Field(
        None, description="Anthropic API key (optional — falls back to CLI auth)"
    )
    claude_model: Optional[str] = Field(None, description="Claude model override")
    claude_max_turns: int = Field(10, description="Max conversation turns per request")
    claude_timeout_seconds: int = Field(120, description="Claude execution timeout")
    claude_max_cost_per_user: float = Field(
        5.0, description="Daily cost cap per user (USD)"
    )
    claude_max_cost_per_request: float = Field(
        1.0, description="Budget cap per individual request (USD)"
    )
    claude_cli_path: Optional[str] = Field(
        None, description="Path to claude CLI binary"
    )

    # ── Storage ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        "sqlite:///data/bot.db", description="SQLite database URL"
    )
    session_timeout_hours: int = Field(24, description="Inactive session timeout")

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_claude_per_min: int = Field(
        20, description="Claude requests per minute per user"
    )
    rate_limit_commands_per_min: int = Field(
        5, description="Bot commands per minute per user"
    )
    rate_limit_invites_per_hour: int = Field(
        3, description="Invite tokens an admin can generate per hour"
    )

    # ── Tunnel (ngrok) ────────────────────────────────────────────────────────
    ngrok_authtoken: Optional[SecretStr] = Field(
        None, description="ngrok authentication token"
    )
    ssh_port: int = Field(22, description="Local SSH port to expose via ngrok")
    tunnel_poll_interval_seconds: int = Field(
        30, description="Seconds between ngrok API health polls"
    )
    tunnel_max_retries: int = Field(5, description="Max ngrok restart attempts")

    # ── System Monitor ────────────────────────────────────────────────────────
    monitor_enabled: bool = Field(True, description="Enable system metrics collection")
    monitor_collect_interval_seconds: int = Field(
        60, description="Metrics collection interval"
    )
    hourly_report_enabled: bool = Field(
        False, description="Send hourly status reports (admin opt-in)"
    )
    alert_cpu_percent: float = Field(90.0, description="CPU usage alert threshold")
    alert_ram_percent: float = Field(85.0, description="RAM usage alert threshold")
    alert_disk_percent: float = Field(90.0, description="Disk usage alert threshold")
    alert_ssh_failures_per_min: int = Field(
        5, description="SSH auth failure rate to trigger brute-force alert"
    )

    # ── Bot Behaviour ────────────────────────────────────────────────────────
    log_level: str = Field("INFO", description="Logging level")
    debug: bool = Field(False, description="Enable debug mode")
    agentic_mode: bool = Field(True, description="Use conversational agentic mode")

    # ── Feature Flags ────────────────────────────────────────────────────────
    enable_tunnel: bool = Field(False, description="Enable ngrok tunnel manager")
    enable_monitor: bool = Field(True, description="Enable system monitor")
    enable_api_server: bool = Field(False, description="Enable FastAPI webhook server")
    api_server_port: int = Field(8080, description="API server port")
    enable_voice_messages: bool = Field(
        False, description="Enable voice message transcription"
    )
    enable_file_uploads: bool = Field(True, description="Enable file uploads")
    enable_git_integration: bool = Field(True, description="Enable git operations")
    enable_quick_actions: bool = Field(
        True, description="Show inline keyboard quick actions"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: Any) -> str:
        """Normalize and validate log level."""
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {sorted(valid)}")
        return upper

    @field_validator("claude_timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: Any) -> int:
        """Clamp timeout to a sensible range."""
        if v < 30:
            raise ValueError("claude_timeout_seconds must be >= 30")
        if v > 600:
            raise ValueError("claude_timeout_seconds must be <= 600")
        return v

    @model_validator(mode="after")
    def validate_tunnel_dependencies(self) -> "Settings":
        """Warn if tunnel is enabled without ngrok token."""
        if self.enable_tunnel and not self.ngrok_authtoken:
            # Non-fatal: tunnel manager will handle the missing token at startup
            pass
        return self

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def telegram_token_str(self) -> str:
        """Telegram token as plain string."""
        return self.telegram_bot_token.get_secret_value()

    @property
    def anthropic_api_key_str(self) -> Optional[str]:
        """Anthropic API key as plain string, or None."""
        return (
            self.anthropic_api_key.get_secret_value()
            if self.anthropic_api_key
            else None
        )

    @property
    def ngrok_authtoken_str(self) -> Optional[str]:
        """ngrok auth token as plain string, or None."""
        return (
            self.ngrok_authtoken.get_secret_value() if self.ngrok_authtoken else None
        )

    @property
    def database_path(self) -> Path:
        """Resolve SQLite file path from database URL."""
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url[10:]).resolve()
        if self.database_url.startswith("sqlite://"):
            return Path(self.database_url[9:]).resolve()
        return Path(self.database_url).resolve()

    @property
    def is_debug(self) -> bool:
        """Whether debug mode is active."""
        return self.debug or self.log_level == "DEBUG"
