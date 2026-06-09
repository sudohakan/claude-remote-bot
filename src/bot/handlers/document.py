"""Document message handler — downloads an uploaded file, routes to Claude.

Telegram documents (any non-photo file: code, text, PDF, archives, ...) carry
no ``.text``, so the text handler skips them. This handler downloads the file
to a temp directory and asks Claude to inspect it by absolute path (Claude
opens it with its own Read tool), reusing the exact same ``claude.execute()``
bridge as text and photo messages. The Claude bridge is intentionally left
untouched.

Mirrors ``handlers/photo.py`` deliberately so the two stay easy to compare.
"""

import asyncio
import re
from pathlib import Path

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.utils.formatting import claude_to_telegram_html, split_message
from src.claude.exceptions import ClaudeError, ClaudeTimeoutError

logger = structlog.get_logger(__name__)

_TYPING_INTERVAL_SECONDS = 4
_FILE_DIR = Path("/tmp/telegram-files")
# 50 MB — Telegram bot API download ceiling for most files
_MAX_FILE_BYTES = 50 * 1024 * 1024


def _safe_name(name: str) -> str:
    """Strip path separators and dangerous chars from a user-supplied filename."""
    base = Path(name).name  # drop any directory parts
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return cleaned or "file"


async def _keep_typing(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send typing action every few seconds until cancelled."""
    while True:
        try:
            await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        await asyncio.sleep(_TYPING_INTERVAL_SECONDS)


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Download the document, ask Claude to inspect it, send the response."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None or message.document is None:
        return

    from src.bot.utils import messages as M

    document = message.document

    # Size guard — refuse oversized files before attempting download.
    if document.file_size and document.file_size > _MAX_FILE_BYTES:
        await message.reply_text(
            M.msg_error(
                "Dosya çok büyük (50 MB üzeri). Daha küçük bir dosya gönder."
            ),
            parse_mode="HTML",
        )
        return

    # Claude rate limit (shared with text/photo messages)
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
        await message.reply_text(
            M.msg_unavailable("Claude bridge"), parse_mode="HTML"
        )
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

    # Download the file to a temp dir (ext4, Claude-readable). Prefix with the
    # message id so concurrent uploads of the same filename don't collide.
    fname = _safe_name(document.file_name or f"file_{message.message_id}")
    file_path = _FILE_DIR / f"tg_{user.id}_{message.message_id}_{fname}"
    try:
        _FILE_DIR.mkdir(parents=True, exist_ok=True)
        tg_file = await document.get_file()
        await tg_file.download_to_drive(str(file_path))
    except Exception as exc:
        logger.error("Document download failed", user_id=user.id, error=str(exc))
        await message.reply_text(
            M.msg_error("Dosya indirilemedi."), parse_mode="HTML"
        )
        return

    caption = (message.caption or "").strip()
    instruction = (
        caption if caption else "Bu dosyayı incele ve içeriğini özetle."
    )
    prompt = (
        f"{instruction}\n\n"
        f"[Kullanıcı bir dosya gönderdi: {document.file_name or fname}. "
        f"Şu dosyayı Read aracıyla aç ve incele: {file_path}]"
    )

    # Initial status message + continuous typing indicator
    status_msg = await message.reply_text("📄")
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
        logger.error("Claude error (document)", user_id=user.id, error=str(exc))
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
            CommandLogModel(user_id=user.id, command="<document>", result="ok")
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
