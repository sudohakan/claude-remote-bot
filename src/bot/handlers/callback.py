"""Inline keyboard callback handler.

Callback data format: <action>:<payload>

Supported actions:
  new_session  — same as /new
  show_status  — same as /status
  show_help    — same as /help
"""

import structlog
from telegram import Update
from telegram.ext import ContextTypes

logger = structlog.get_logger(__name__)


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch inline keyboard button presses."""
    query = update.callback_query
    if query is None:
        return

    # Auth middleware is registered as a MessageHandler, which never matches a
    # callback_query update — so a button press reaches this handler unchecked.
    # A user revoked after receiving a keyboard could still drive /new and
    # /status through old buttons. Re-check here.
    access_mgr = ctx.bot_data.get("access_manager")
    user = update.effective_user
    if (
        access_mgr is None
        or user is None
        or not await access_mgr.is_authorised(user.id)
    ):
        logger.info("Unauthorised callback", user_id=getattr(user, "id", None))
        await query.answer()
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
