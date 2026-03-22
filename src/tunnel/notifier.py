"""Tunnel state-change notifier.

Subscribes to TunnelStateChangeEvent and TunnelRetryExhaustedEvent
on the event bus and sends admin notifications — but ONLY on actual
state transitions (up→down, down→up) and when retries are exhausted.

Anti-spam guarantees:
- Same (from_state, to_state) pair suppressed for 5 minutes.
- Retry-exhausted messages suppressed for 1 hour.
"""

import time
from typing import Optional

import structlog
from telegram import Bot
from telegram.error import TelegramError

from src.events.bus import Event, EventBus
from src.events.types import TunnelRetryExhaustedEvent, TunnelStateChangeEvent

logger = structlog.get_logger(__name__)

# Notification dedup windows
_STATE_CHANGE_WINDOW = 300  # 5 minutes
_RETRY_EXHAUSTED_WINDOW = 3600  # 1 hour


class TunnelNotifier:
    """Send admin Telegram messages on tunnel state changes."""

    def __init__(
        self,
        event_bus: EventBus,
        bot: Bot,
        admin_chat_id: int,
    ) -> None:
        self._bus = event_bus
        self._bot = bot
        self._admin_chat_id = admin_chat_id

        # (prev_state, new_state) → last notify timestamp
        self._sent_changes: dict[tuple[str, str], float] = {}
        self._retry_exhausted_sent: float = 0.0

    def register(self) -> None:
        """Subscribe to tunnel events."""
        self._bus.subscribe(TunnelStateChangeEvent, self._on_state_change)
        self._bus.subscribe(TunnelRetryExhaustedEvent, self._on_retry_exhausted)

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _on_state_change(self, event: Event) -> None:
        if not isinstance(event, TunnelStateChangeEvent):
            return

        prev = event.previous_state
        new = event.new_state

        # Only interesting transitions
        if new not in ("up", "down") or prev == new:
            return

        key = (prev, new)
        now = time.monotonic()
        if now - self._sent_changes.get(key, 0.0) < _STATE_CHANGE_WINDOW:
            logger.debug("Tunnel notification suppressed (dedup)", transition=key)
            return

        self._sent_changes[key] = now
        msg = self._format_state_change(event)
        await self._send(msg)

    async def _on_retry_exhausted(self, event: Event) -> None:
        if not isinstance(event, TunnelRetryExhaustedEvent):
            return

        now = time.monotonic()
        if now - self._retry_exhausted_sent < _RETRY_EXHAUSTED_WINDOW:
            logger.debug("Retry-exhausted notification suppressed (dedup)")
            return

        self._retry_exhausted_sent = now
        msg = (
            f"<b>Tunnel: retries exhausted</b>\n\n"
            f"ngrok failed to restart after {event.attempts} attempts.\n"
            "Manual intervention required."
        )
        await self._send(msg)

    # ── Formatting ────────────────────────────────────────────────────────────

    def _format_state_change(self, event: TunnelStateChangeEvent) -> str:
        if event.new_state == "up":
            ssh_cmd = ""
            if event.ssh_host and event.ssh_port:
                ssh_cmd = f"\n\nConnect: <code>ssh -p {event.ssh_port} user@{event.ssh_host}</code>"
            return (
                f"<b>Tunnel: UP</b>{ssh_cmd}\n\n"
                f"URL: <code>{event.tunnel_url or 'unknown'}</code>"
            )
        else:
            return (
                f"<b>Tunnel: DOWN</b>\n\n"
                f"Previous state: {event.previous_state}\n"
                "Attempting to restart..."
            )

    # ── Delivery ──────────────────────────────────────────────────────────────

    async def _send(self, text: str) -> None:
        try:
            await self._bot.send_message(
                chat_id=self._admin_chat_id,
                text=text,
                parse_mode="HTML",
            )
            logger.info("Tunnel notification sent", chat_id=self._admin_chat_id)
        except TelegramError as exc:
            logger.error(
                "Failed to send tunnel notification",
                chat_id=self._admin_chat_id,
                error=str(exc),
            )
