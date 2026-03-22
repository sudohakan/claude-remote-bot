"""Feature flag management.

Centralizes all feature-enabled checks so the rest of the codebase
reads feature flags from a single place rather than accessing Settings
fields directly.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import Settings


class FeatureFlags:
    """Derive feature on/off state from Settings."""

    def __init__(self, settings: "Settings") -> None:
        self._s = settings

    # ── Core features ────────────────────────────────────────────────────────

    @property
    def tunnel_enabled(self) -> bool:
        """ngrok tunnel manager is active."""
        return self._s.enable_tunnel

    @property
    def monitor_enabled(self) -> bool:
        """System metrics collection is active."""
        return self._s.enable_monitor

    @property
    def api_server_enabled(self) -> bool:
        """FastAPI webhook server is active."""
        return self._s.enable_api_server

    @property
    def voice_messages_enabled(self) -> bool:
        """Voice message transcription is active."""
        return self._s.enable_voice_messages

    @property
    def file_uploads_enabled(self) -> bool:
        """File upload handling is active."""
        return self._s.enable_file_uploads

    @property
    def git_integration_enabled(self) -> bool:
        """Git operations are active."""
        return self._s.enable_git_integration

    @property
    def quick_actions_enabled(self) -> bool:
        """Inline keyboard quick actions are shown."""
        return self._s.enable_quick_actions

    @property
    def agentic_mode_enabled(self) -> bool:
        """Conversational agentic mode is active."""
        return self._s.agentic_mode

    @property
    def hourly_report_enabled(self) -> bool:
        """Hourly admin reports are sent."""
        return self._s.hourly_report_enabled

    # ── Generic lookup ───────────────────────────────────────────────────────

    def is_enabled(self, name: str) -> bool:
        """Check a feature by string name. Returns False for unknown names."""
        mapping: dict[str, bool] = {
            "tunnel": self.tunnel_enabled,
            "monitor": self.monitor_enabled,
            "api_server": self.api_server_enabled,
            "voice_messages": self.voice_messages_enabled,
            "file_uploads": self.file_uploads_enabled,
            "git_integration": self.git_integration_enabled,
            "quick_actions": self.quick_actions_enabled,
            "agentic_mode": self.agentic_mode_enabled,
            "hourly_report": self.hourly_report_enabled,
        }
        return mapping.get(name, False)

    def enabled_list(self) -> list[str]:
        """Return names of all currently enabled features."""
        return [
            "tunnel" if self.tunnel_enabled else None,
            "monitor" if self.monitor_enabled else None,
            "api_server" if self.api_server_enabled else None,
            "voice_messages" if self.voice_messages_enabled else None,
            "file_uploads" if self.file_uploads_enabled else None,
            "git_integration" if self.git_integration_enabled else None,
            "quick_actions" if self.quick_actions_enabled else None,
            "agentic_mode" if self.agentic_mode_enabled else None,
        ]  # type: ignore[return-value]
        # filter out Nones handled below

    def enabled_list(self) -> list[str]:  # noqa: F811
        """Return names of all currently enabled features."""
        candidates = {
            "tunnel": self.tunnel_enabled,
            "monitor": self.monitor_enabled,
            "api_server": self.api_server_enabled,
            "voice_messages": self.voice_messages_enabled,
            "file_uploads": self.file_uploads_enabled,
            "git_integration": self.git_integration_enabled,
            "quick_actions": self.quick_actions_enabled,
            "agentic_mode": self.agentic_mode_enabled,
        }
        return [name for name, active in candidates.items() if active]
