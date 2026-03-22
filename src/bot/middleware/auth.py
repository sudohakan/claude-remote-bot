"""Authentication middleware.

Checks every incoming update against AccessManager before it reaches
a command or message handler.  Unauthenticated users get the invite
instructions and the update is dropped.
"""

from typing import Any, Callable, Dict

import structlog

logger = structlog.get_logger(__name__)


async def auth_middleware(handler: Callable, update: Any, data: Dict[str, Any]) -> Any:
    """Reject updates from users not in the bot's user table."""
    user = getattr(update, "effective_user", None)
    if user is None:
        return  # channel post or similar — skip

    user_id: int = user.id
    access_mgr = data.get("access_manager")

    if access_mgr is None:
        logger.error("access_manager missing from middleware context")
        return

    if not await access_mgr.is_authorised(user_id):
        logger.info("Unauthorised access attempt", user_id=user_id)
        message = getattr(update, "effective_message", None)
        if message:
            from src.bot.utils.constants import MSG_AUTH_REQUIRED

            await message.reply_text(MSG_AUTH_REQUIRED)
        return  # do NOT call handler

    return await handler(update, data)
