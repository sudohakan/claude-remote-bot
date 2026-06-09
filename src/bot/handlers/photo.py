"""Photo message handler — downloads image, routes to Claude via file path.

Telegram photos carry no ``.text``, so the text handler skips them. This
handler downloads the highest-resolution photo to a temp file and asks Claude
to inspect it by absolute path (Claude opens it with its Read tool), reusing
the exact same ``claude.execute()`` bridge as text messages. The Claude bridge
is intentionally left untouched.
"""

import asyncio
from pathlib import Path

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.utils.formatting import claude_to_telegram_html, split_message
from src.claude.exceptions import ClaudeError, ClaudeTimeoutError

logger = structlog.get_logger(__name__)

_TYPING_INTERVAL_SECONDS = 4
_IMAGE_DIR = Path("/tmp/telegram-images")


async def _keep_typing(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send typing action every few seconds until cancelled."""
    while True:
        try:
            await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        await asyncio.sleep(_TYPING_INTERVAL_SECONDS)


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Download the photo, ask Claude to inspect it, send the response."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None or not message.photo:
        return

    from src.bot.utils import messages as M

    # Claude rate limit (shared with text messages)
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

    # Download highest-resolution photo to a temp file (ext4, Claude-readable).
    photo = message.photo[-1]
    img_path = _IMAGE_DIR / f"tg_{user.id}_{message.message_id}.jpg"
    try:
        _IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        tg_file = await photo.get_file()
        await tg_file.download_to_drive(str(img_path))
    except Exception as exc:
        logger.error("Photo download failed", user_id=user.id, error=str(exc))
        await message.reply_text(M.msg_error("Görsel indirilemedi."), parse_mode="HTML")
        return

    caption = (message.caption or "").strip()
    instruction = caption if caption else "Bu görselde ne olduğunu açıkla."
    prompt = (
        f"{instruction}\n\n"
        f"[Kullanıcı bir görsel gönderdi. Şu dosyayı Read aracıyla aç ve "
        f"incele: {img_path}]"
    )

    # Initial status message + continuous typing indicator
    status_msg = await message.reply_text("🖼️")
    typing_task = asyncio.create_task(_keep_typing(chat_id, ctx))

    try:
        response = await claude.execute(
            user_id=user.id,
            prompt=prompt,
            access_level=access_level,
            username=user.username,
            role=role,
        )
    except ClaudeTimeoutError:
        await status_msg.edit_text(
            M.compose(
                M.header(M.ICON_WARNING, "Claude timed out"),
                f"Daha kısa bir istek dene veya {M.code('/new')} ile sıfırla.",
            ),
            parse_mode="HTML",
        )
        return
    except ClaudeError as exc:
        await status_msg.edit_text(M.msg_error(str(exc)), parse_mode="HTML")
        logger.error("Claude error (photo)", user_id=user.id, error=str(exc))
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
            CommandLogModel(user_id=user.id, command="<photo>", result="ok")
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
