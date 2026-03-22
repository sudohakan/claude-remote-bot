"""Text message handler — routes to Claude bridge.

In agentic mode every non-command text message becomes a Claude prompt.
Rate-limits Claude requests separately from general commands.
"""

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.utils.formatting import escape_html, split_message
from src.claude.exceptions import ClaudeError, ClaudeTimeoutError

logger = structlog.get_logger(__name__)


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward user message to Claude and send the response."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    text = (message.text or "").strip()
    if not text:
        return

    # Claude rate limit (separate from command limit)
    limiter = ctx.bot_data.get("rate_limiter")
    if limiter:
        allowed, wait = await limiter.check("claude", user.id)
        if not allowed:
            await message.reply_text(f"Claude rate limit — wait {wait:.0f}s.")
            return

    claude = ctx.bot_data.get("claude_facade")
    if claude is None:
        await message.reply_text("Claude bridge not available.")
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

    # Show "typing…" indicator
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        response = await claude.execute(
            user_id=user.id,
            prompt=text,
            access_level=access_level,
            username=user.username,
            role=role,
        )
    except ClaudeTimeoutError:
        await message.reply_text(
            "Claude timed out. Try a simpler request or /new to reset."
        )
        return
    except ClaudeError as exc:
        await message.reply_text(
            f"Claude error: {escape_html(str(exc))}", parse_mode="HTML"
        )
        logger.error("Claude error", user_id=user.id, error=str(exc))
        return

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
        await message.reply_text(chunk, parse_mode="HTML")
