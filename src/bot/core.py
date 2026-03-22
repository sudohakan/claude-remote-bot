"""Bot application lifecycle — build, start, stop.

Wraps python-telegram-bot's Application with:
- Dependency injection via bot_data
- Middleware chain (security → auth → rate_limit)
- Graceful shutdown
"""

import asyncio
from typing import Any, Callable, Dict, Optional

import structlog
from telegram import Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationHandlerStop,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

from src.config.settings import Settings
from .orchestrator import BotOrchestrator

logger = structlog.get_logger(__name__)


class RemoteBot:
    """Manage the bot's full lifecycle."""

    def __init__(self, settings: Settings, deps: Dict[str, Any]) -> None:
        self._settings = settings
        self._deps = {**deps, "settings": settings}
        self._app: Optional[Application] = None
        self._running = False

    # ── Build ─────────────────────────────────────────────────────────────────

    async def build(self) -> None:
        """Construct the Application and register all handlers."""
        if self._app is not None:
            return

        logger.info("Building bot application")

        builder = (
            Application.builder()
            .token(self._settings.telegram_token_str)
            .defaults(Defaults(parse_mode="HTML"))
            .rate_limiter(AIORateLimiter(max_retries=1))
            .connect_timeout(30)
            .read_timeout(30)
            .write_timeout(30)
        )

        self._app = builder.build()

        # Inject dependencies into bot_data at build time
        for key, val in self._deps.items():
            self._app.bot_data[key] = val

        # Middleware groups (negative group = runs before handlers)
        for group, mw_fn in [
            (-3, self._wrap_middleware("security")),
            (-2, self._wrap_middleware("auth")),
            (-1, self._wrap_middleware("rate_limit")),
        ]:
            self._app.add_handler(
                MessageHandler(filters.ALL, mw_fn), group=group
            )

        # Handlers
        orchestrator = BotOrchestrator(self._deps)
        orchestrator.register(self._app)

        # Error handler
        self._app.add_error_handler(self._on_error)

        await self._app.initialize()
        await orchestrator.set_bot_commands(self._app)

        logger.info("Bot application built")

    # ── Start / Stop ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start polling (blocking until stop() is called)."""
        if self._running:
            logger.warning("Bot already running")
            return

        await self.build()
        assert self._app is not None

        self._running = True
        logger.info("Starting bot polling")

        await self._app.start()
        await self._app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )

        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """Signal the main loop to exit."""
        self._running = False

    async def _shutdown(self) -> None:
        if self._app:
            if self._app.updater.running:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        logger.info("Bot shutdown complete")

    # ── Middleware helpers ────────────────────────────────────────────────────

    def _wrap_middleware(self, name: str) -> Callable:
        """Create a MessageHandler-compatible wrapper for a middleware function."""

        async def _handler(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            # Skip bot-generated updates
            if update.effective_user and getattr(
                update.effective_user, "is_bot", False
            ):
                raise ApplicationHandlerStop

            # Refresh deps in context each call
            for key, val in self._deps.items():
                context.bot_data[key] = val

            handler_called = False

            async def _next(evt: Any, data: Any) -> None:
                nonlocal handler_called
                handler_called = True

            mw_fn = self._get_middleware(name)
            await mw_fn(_next, update, context.bot_data)

            if not handler_called:
                raise ApplicationHandlerStop

        return _handler

    @staticmethod
    def _get_middleware(name: str) -> Callable:
        if name == "security":
            from .middleware.security import security_middleware
            return security_middleware
        if name == "auth":
            from .middleware.auth import auth_middleware
            return auth_middleware
        if name == "rate_limit":
            from .middleware.rate_limit import rate_limit_middleware
            return rate_limit_middleware
        raise ValueError(f"Unknown middleware: {name}")

    # ── Error handler ─────────────────────────────────────────────────────────

    async def _on_error(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        error = context.error
        logger.error(
            "Unhandled error",
            error=str(error),
            error_type=type(error).__name__,
            user_id=(
                update.effective_user.id if update and update.effective_user else None
            ),
        )

        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "An unexpected error occurred. Please try again."
                )
            except Exception:
                pass
