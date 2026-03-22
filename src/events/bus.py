"""Central async event bus.

Decouples event producers (tunnel, monitor, bot handlers) from
consumers (notifications, logging). All communication goes through
typed events queued and dispatched here.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine, Dict, List, Optional, Type

import structlog

logger = structlog.get_logger(__name__)

EventHandler = Callable[["Event"], Coroutine[Any, Any, None]]


@dataclass
class Event:
    """Base class for all events."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str = "unknown"

    @property
    def event_type(self) -> str:
        return type(self).__name__


class EventBus:
    """Async publish/subscribe event bus with typed handler registration."""

    def __init__(self) -> None:
        self._handlers: Dict[Type[Event], List[EventHandler]] = {}
        self._global_handlers: List[EventHandler] = []
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running: bool = False
        self._processor: Optional[asyncio.Task[None]] = None

    # ── Registration ──────────────────────────────────────────────────────────

    def subscribe(self, event_type: Type[Event], handler: EventHandler) -> None:
        """Subscribe *handler* to events of *event_type* (and subclasses)."""
        self._handlers.setdefault(event_type, []).append(handler)
        logger.debug(
            "Handler subscribed",
            event_type=event_type.__name__,
            handler=handler.__qualname__,
        )

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe *handler* to every event regardless of type."""
        self._global_handlers.append(handler)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._processor = asyncio.create_task(self._process_loop())
        logger.info("Event bus started")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._processor:
            self._processor.cancel()
            try:
                await self._processor
            except asyncio.CancelledError:
                pass
        logger.info("Event bus stopped")

    # ── Publishing ────────────────────────────────────────────────────────────

    async def publish(self, event: Event) -> None:
        """Queue *event* for dispatch to registered handlers."""
        logger.debug(
            "Event queued",
            event_type=event.event_type,
            event_id=event.id,
            source=event.source,
        )
        await self._queue.put(event)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _process_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            await self._dispatch(event)

    async def _dispatch(self, event: Event) -> None:
        handlers: List[EventHandler] = []
        for etype, h_list in self._handlers.items():
            if isinstance(event, etype):
                handlers.extend(h_list)
        handlers.extend(self._global_handlers)

        if not handlers:
            logger.debug("No handlers for event", event_type=event.event_type)
            return

        results = await asyncio.gather(
            *(self._safe_call(h, event) for h in handlers),
            return_exceptions=True,
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Event handler raised an exception",
                    event_type=event.event_type,
                    handler=handlers[i].__qualname__,
                    error=str(result),
                )

    @staticmethod
    async def _safe_call(handler: EventHandler, event: Event) -> None:
        try:
            await handler(event)
        except Exception:
            logger.exception(
                "Unhandled error in event handler",
                handler=handler.__qualname__,
                event_type=event.event_type,
            )
            raise
