"""Entry point.

Wires config, storage, security, claude bridge, and bot together.
Full background tasks (tunnel, monitor) are wired in Task 10.
"""

import asyncio
import signal
from typing import Optional

import structlog

from src.config.settings import Settings
from src.storage.facade import StorageFacade
from src.security.auth import AccessManager
from src.security.rate_limiter import RateLimiter
from src.claude.facade import ClaudeFacade
from src.claude.monitor import CostTracker
from src.claude.sanitizer import CredentialSanitizer
from src.claude.sdk_integration import ClaudeSDKRunner
from src.claude.session import SessionManager
from src.bot.core import RemoteBot

logger = structlog.get_logger(__name__)


def run() -> None:
    """Entry point for pyproject.toml script."""
    asyncio.run(_main())


async def _main() -> None:
    settings = Settings()

    import logging
    logging.basicConfig(level=settings.log_level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        )
    )

    logger.info("claude-remote-bot starting", version="0.1.0")

    # Storage
    storage = StorageFacade(settings.database_url)
    await storage.initialize()

    # Security
    access = AccessManager(storage=storage, admin_id=settings.admin_telegram_id)
    await access.ensure_admin()

    limiter = RateLimiter(
        claude_per_min=settings.rate_limit_claude_per_min,
        commands_per_min=settings.rate_limit_commands_per_min,
        invites_per_hour=settings.rate_limit_invites_per_hour,
    )

    # Claude bridge
    runner = ClaudeSDKRunner(
        anthropic_api_key=settings.anthropic_api_key_str,
        claude_model=settings.claude_model,
        max_turns=settings.claude_max_turns,
        timeout_seconds=settings.claude_timeout_seconds,
        cli_path=settings.claude_cli_path,
    )
    session_mgr = SessionManager(timeout_hours=settings.session_timeout_hours)
    cost_tracker = CostTracker()
    sanitizer = CredentialSanitizer()

    claude_facade = ClaudeFacade(
        runner=runner,
        session_mgr=session_mgr,
        cost_tracker=cost_tracker,
        sanitizer=sanitizer,
        max_cost_per_user=settings.claude_max_cost_per_user,
    )

    deps = {
        "storage": storage,
        "access_manager": access,
        "rate_limiter": limiter,
        "claude_facade": claude_facade,
    }

    bot = RemoteBot(settings=settings, deps=deps)

    # Graceful shutdown on SIGTERM/SIGINT
    loop = asyncio.get_running_loop()

    def _signal_handler():
        logger.info("Shutdown signal received")
        asyncio.create_task(bot.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows

    try:
        await bot.start()
    finally:
        await storage.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    run()
