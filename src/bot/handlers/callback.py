"""Inline keyboard callback handler.

Callback data format: <action>:<payload>

Supported actions:
  new_session  — same as /new
  show_status  — same as /status
  show_help    — same as /help
"""

from telegram import Update
from telegram.ext import ContextTypes

import structlog

logger = structlog.get_logger(__name__)


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch inline keyboard button presses."""
    query = update.callback_query
    if query is None:
        return

    await query.answer()  # acknowledge the button press

    data = query.data or ""
    parts = data.split(":", 1)
    action = parts[0]
    payload = parts[1] if len(parts) > 1 else ""

    if action == "new_session":
        from src.bot.handlers.command import cmd_new
        await cmd_new(update, ctx)

    elif action == "show_status":
        from src.bot.handlers.command import cmd_status
        await cmd_status(update, ctx)

    elif action == "show_help":
        from src.bot.handlers.command import cmd_help
        await cmd_help(update, ctx)

    else:
        logger.warning("Unknown callback action", action=action, payload=payload)
        await query.edit_message_text("Unknown action.")
