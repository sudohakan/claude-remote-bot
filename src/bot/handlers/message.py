"""Text message handler — routes to Claude bridge.

In agentic mode every non-command text message becomes a Claude prompt.
Rate-limits Claude requests separately from general commands.
Sends periodic typing indicators and a status message while Claude works.
"""

import asyncio

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.utils.formatting import (
    claude_to_telegram_html,
    split_message,
)
from src.claude.exceptions import ClaudeError, ClaudeTimeoutError

logger = structlog.get_logger(__name__)

_TYPING_INTERVAL_SECONDS = 4


async def _keep_typing(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send typing action every few seconds until cancelled."""
    while True:
        try:
            await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        await asyncio.sleep(_TYPING_INTERVAL_SECONDS)


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward user message to Claude and send the response."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    text = (message.text or "").strip()
    if not text:
        return

    from src.bot.utils import messages as M

    # Claude rate limit (separate from command limit)
    limiter = ctx.bot_data.get("rate_limiter")
    if limiter:
        allowed, wait = await limiter.check("claude", user.id)
        if not allowed:
            await message.reply_text(
                M.msg_rate_limited(wait, context="Claude"), parse_mode="HTML"
            )
            return

    claude = ctx.bot_data.get("claude_facade")
    if claude is None:
        await message.reply_text(M.msg_unavailable("Claude bridge"), parse_mode="HTML")
        return

    access = ctx.bot_data.get("access_manager")
    access_level = "sandbox"
    role = "user"
    if access:
        level = await access.get_access_level(user.id)
        if level:
            access_level = level
        user_role = await access.get_role(user.id)
        if user_role:
            role = user_role

    chat_id = update.effective_chat.id

    # Send initial status message and start continuous typing indicator
    status_msg = await message.reply_text("⏳")
    typing_task = asyncio.create_task(_keep_typing(chat_id, ctx))

    try:
        response = await claude.execute(
            user_id=user.id,
            prompt=text,
            access_level=access_level,
            username=user.username,
            role=role,
        )
    except ClaudeTimeoutError:
        await status_msg.edit_text(
            M.compose(
                M.header(M.ICON_WARNING, "Claude timed out"),
                f"Try a shorter prompt, or reset with {M.code('/new')}.",
            ),
            parse_mode="HTML",
        )
        return
    except ClaudeError as exc:
        await status_msg.edit_text(M.msg_error(str(exc)), parse_mode="HTML")
        logger.error("Claude error", user_id=user.id, error=str(exc))
        return
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    # Delete status message — real response follows
    try:
        await status_msg.delete()
    except Exception:
        pass

    # Log command
    storage = ctx.bot_data.get("storage")
    if storage:
        from src.storage.models import CommandLogModel

        await storage.commands.log(
            CommandLogModel(user_id=user.id, command="<message>", result="ok")
        )

    # Send response in chunks if needed
    chunks = split_message(response.content or "(no response)")
    for chunk in chunks:
        rendered = claude_to_telegram_html(chunk)
        try:
            await message.reply_text(rendered, parse_mode="HTML")
        except Exception as send_exc:
            logger.warning(
                "HTML reply failed, falling back to plain text",
                error=str(send_exc),
                error_type=type(send_exc).__name__,
            )
            await message.reply_text(chunk, parse_mode=None)
