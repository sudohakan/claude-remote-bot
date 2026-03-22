"""Entry point — will be wired up fully in Task 10.

Placeholder that verifies config loads correctly.
"""

import asyncio
import structlog

from src.config.settings import Settings

logger = structlog.get_logger()


def run() -> None:
    """Start the bot (entry point for pyproject.toml script)."""
    asyncio.run(_main())


async def _main() -> None:
    settings = Settings()
    logger.info(
        "claude-remote-bot starting",
        debug=settings.debug,
        enable_tunnel=settings.enable_tunnel,
        enable_monitor=settings.enable_monitor,
    )
    # Full wiring happens in Task 10


if __name__ == "__main__":
    run()
