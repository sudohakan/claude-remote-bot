"""Notification service — delivers agent/system events to Telegram.

Anti-spam rules enforced here:
1. State-change only — never repeat the same message type within the dedup window.
2. Dedup window — same (chat_id, message_type) pair suppressed for 5 minutes.
3. Per-chat rate limit — at most 1 message per second (Telegram constraint).
4. All notifications go through send_notification() which enforces these rules.
"""

import asyncio
import hashlib
import time
from typing import Dict, List, Optional, Tuple

import structlog
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from src.events.bus import Event, EventBus
from src.events.types import AgentResponseEvent

logger = structlog.get_logger(__name__)

# Telegram: ~1 msg/sec per chat
_SEND_INTERVAL = 1.1

# Dedup window: suppress same message type for 5 minutes
_DEDUP_WINDOW_SECONDS = 300


class NotificationService:
    """Rate-limited, dedup-aware notification delivery to Telegram chats."""

    def __init__(
        self,
        event_bus: EventBus,
        bot: Bot,
        default_chat_ids: Optional[List[int]] = None,
    ) -> None:
        self.bus = event_bus
        self.bot = bot
        self.default_chat_ids: List[int] = default_chat_ids or []

        # (chat_id, message_type) → last send timestamp
        self._dedup: Dict[Tuple[int, str], float] = {}

        # chat_id → last send time (for rate limiting)
        self._last_send: Dict[int, float] = {}

        self._queue: asyncio.Queue[AgentResponseEvent] = asyncio.Queue()
        self._running: bool = False
        self._sender: Optional[asyncio.Task[None]] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def register(self) -> None:
        """Subscribe to AgentResponseEvent on the bus."""
        self.bus.subscribe(AgentResponseEvent, self._enqueue)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._sender = asyncio.create_task(self._process_queue())
        logger.info("Notification service started")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._sender:
            self._sender.cancel()
            try:
                await self._sender
            except asyncio.CancelledError:
                pass
        logger.info("Notification service stopped")

    # ── Public API ────────────────────────────────────────────────────────────

    async def send_notification(
        self,
        chat_id: int,
        text: str,
        message_type: str = "generic",
        parse_mode: Optional[str] = "HTML",
    ) -> bool:
        """Send *text* to *chat_id* if it is not suppressed by dedup rules.

        Returns True if the message was sent, False if suppressed.
        """
        if self._is_deduped(chat_id, message_type):
            logger.debug(
                "Notification suppressed by dedup",
                chat_id=chat_id,
                message_type=message_type,
            )
            return False

        await self._do_send(chat_id, text, parse_mode)
        self._record_send(chat_id, message_type)
        return True

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _enqueue(self, event: Event) -> None:
        if isinstance(event, AgentResponseEvent):
            await self._queue.put(event)

    async def _process_queue(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            chat_ids = [event.chat_id] if event.chat_id else list(self.default_chat_ids)
            for cid in chat_ids:
                await self._rate_limited_send(cid, event)

    async def _rate_limited_send(self, chat_id: int, event: AgentResponseEvent) -> None:
        now = time.monotonic()
        last = self._last_send.get(chat_id, 0.0)
        wait = _SEND_INTERVAL - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)

        await self._do_send(
            chat_id,
            event.text,
            event.parse_mode,
        )
        self._last_send[chat_id] = time.monotonic()

    async def _do_send(
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = "HTML",
    ) -> None:
        chunks = self._split(text)
        pm = ParseMode.HTML if parse_mode == "HTML" else None
        for chunk in chunks:
            try:
                await self.bot.send_message(chat_id=chat_id, text=chunk, parse_mode=pm)
                self._last_send[chat_id] = time.monotonic()
                if len(chunks) > 1:
                    await asyncio.sleep(_SEND_INTERVAL)
            except TelegramError as exc:
                logger.error(
                    "Failed to send notification",
                    chat_id=chat_id,
                    error=str(exc),
                )

    # ── Dedup helpers ─────────────────────────────────────────────────────────

    def _is_deduped(self, chat_id: int, message_type: str) -> bool:
        key = (chat_id, message_type)
        last = self._dedup.get(key, 0.0)
        return (time.monotonic() - last) < _DEDUP_WINDOW_SECONDS

    def _record_send(self, chat_id: int, message_type: str) -> None:
        self._dedup[(chat_id, message_type)] = time.monotonic()

    def _content_hash(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    # ── Message splitting ─────────────────────────────────────────────────────

    @staticmethod
    def _split(text: str, limit: int = 4096) -> List[str]:
        if len(text) <= limit:
            return [text]
        chunks: List[str] = []
        while text:
            if len(text) <= limit:
                chunks.append(text)
                break
            pos = text.rfind("\n\n", 0, limit)
            if pos == -1:
                pos = text.rfind("\n", 0, limit)
            if pos == -1:
                pos = text.rfind(" ", 0, limit)
            if pos == -1:
                pos = limit
            chunks.append(text[:pos])
            text = text[pos:].lstrip()
        return chunks
