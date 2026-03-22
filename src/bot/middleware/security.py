"""Security middleware — input sanitization and basic validation.

Runs before auth so we can block obviously malformed updates early.
"""

from typing import Any, Callable, Dict

import structlog

logger = structlog.get_logger(__name__)

# Maximum raw message text length accepted
_MAX_TEXT_LEN = 8000


async def security_middleware(
    handler: Callable, update: Any, data: Dict[str, Any]
) -> Any:
    """Validate update content before passing to handlers."""
    message = getattr(update, "effective_message", None)
    if message is None:
        return await handler(update, data)

    text = getattr(message, "text", None) or ""

    # Block null bytes
    if "\x00" in text:
        logger.warning(
            "Null byte in message text",
            user_id=getattr(getattr(update, "effective_user", None), "id", None),
        )
        return  # drop silently

    # Block excessively long messages
    if len(text) > _MAX_TEXT_LEN:
        await message.reply_text(
            f"Message too long (max {_MAX_TEXT_LEN:,} chars). Please shorten it."
        )
        return

    return await handler(update, data)
