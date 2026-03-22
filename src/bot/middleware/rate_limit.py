"""Rate limiting middleware.

Uses the RateLimiter 'commands' bucket to protect all bot interactions.
Heavy Claude requests are rate-limited separately inside the message handler.
"""

from typing import Any, Callable, Dict

import structlog

logger = structlog.get_logger(__name__)


async def rate_limit_middleware(
    handler: Callable, update: Any, data: Dict[str, Any]
) -> Any:
    """Enforce command-level rate limit for all updates."""
    user = getattr(update, "effective_user", None)
    if user is None:
        return

    user_id: int = user.id
    limiter = data.get("rate_limiter")

    if limiter is None:
        return await handler(update, data)

    allowed, wait = await limiter.check("commands", user_id)
    if not allowed:
        message = getattr(update, "effective_message", None)
        if message:
            from src.bot.utils.constants import MSG_RATE_LIMITED
            await message.reply_text(MSG_RATE_LIMITED.format(wait=wait))
        logger.info("Rate limit enforced", user_id=user_id, wait=wait)
        return  # drop update

    return await handler(update, data)
