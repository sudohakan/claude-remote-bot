"""Media message handler — voice / audio / video / video_note.

Telegram voice notes, audio files, videos, and round video notes carry no
``.text``, so the text handler skips them and they were previously dropped
silently. This handler downloads the media to a temp directory and asks Claude
to transcribe / inspect it by absolute path, reusing the exact same
``claude.execute()`` bridge as text, photo, and document messages. The Claude
bridge is intentionally left untouched.

Transcription is delegated to Claude (which calls the local ``transcribeLocal``
faster-whisper tool, falling back to the ElevenLabs ``transcribe`` tool) — the
bot itself adds no transcription dependency, so a missing backend degrades
gracefully into a normal Claude reply instead of breaking startup.

Mirrors ``handlers/document.py`` deliberately so the handlers stay easy to
compare.
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
_MEDIA_DIR = Path("/tmp/telegram-media")
# 50 MB — Telegram bot API download ceiling for most files
_MAX_FILE_BYTES = 50 * 1024 * 1024


def _extract(message) -> tuple:
    """Return (telegram_media_obj, kind, default_ext, status_icon) or Nones.

    ``kind`` drives the instruction Claude receives. ``default_ext`` is used
    when the media object exposes no usable file name.
    """
    if message.voice is not None:
        # Telegram voice notes are opus-in-ogg; save as .ogg (not .oga) so the
        # transcribeLocal tool's extension allowlist accepts them.
        return message.voice, "voice", "ogg", "🎤"
    if message.audio is not None:
        return message.audio, "audio", "mp3", "🎵"
    if message.video_note is not None:
        return message.video_note, "video_note", "mp4", "🎬"
    if message.video is not None:
        return message.video, "video", "mp4", "🎬"
    return None, None, None, None


def _filename(media, kind: str, default_ext: str, message_id: int) -> str:
    """Build a safe local filename for the downloaded media."""
    name = getattr(media, "file_name", None)
    if name:
        base = Path(name).name
        cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)
        cleaned = cleaned.strip("._")
        if cleaned:
            return cleaned
    return f"{kind}_{message_id}.{default_ext}"


def _instruction(kind: str, caption: str) -> str:
    """Compose the Claude instruction for a given media kind."""
    if kind in ("voice", "audio"):
        base = (
            "Bu ses dosyasını transcribeLocal aracıyla (yerel faster-whisper) "
            "yazıya dök. transcribeLocal yoksa ElevenLabs transcribe aracını "
            "kullan. Önce transkripti ver"
        )
        if caption:
            return f"{base}, sonra şu isteği uygula: {caption}"
        return f"{base}, sonra transkripte göre yardımcı ol."
    # video / video_note
    base = (
        "Bu bir video dosyası. Ses kanalını transcribeLocal aracıyla yazıya "
        "dökmeyi dene (faster-whisper PyAV ile mp4 sesini çözebilir); "
        "başarısız olursa dosya hakkında bildiğini söyle"
    )
    if caption:
        return f"{base}. İstek: {caption}"
    return f"{base}."


async def _keep_typing(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send typing action every few seconds until cancelled."""
    while True:
        try:
            await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        await asyncio.sleep(_TYPING_INTERVAL_SECONDS)


async def handle_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Download voice/audio/video, ask Claude to transcribe/inspect, reply."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    media, kind, default_ext, status_icon = _extract(message)
    if media is None:
        return

    from src.bot.utils import messages as M

    # Size guard — refuse oversized media before attempting download.
    file_size = getattr(media, "file_size", None)
    if file_size and file_size > _MAX_FILE_BYTES:
        await message.reply_text(
            M.msg_error("Dosya çok büyük (50 MB üzeri). Daha kısa bir kayıt gönder."),
            parse_mode="HTML",
        )
        return

    # Claude rate limit (shared with text/photo/document messages)
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

    # Download the media to a temp dir (ext4, Claude-readable). Prefix with the
    # message id so concurrent uploads of the same name don't collide.
    fname = _filename(media, kind, default_ext, message.message_id)
    file_path = _MEDIA_DIR / f"tg_{user.id}_{message.message_id}_{fname}"
    try:
        _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        tg_file = await media.get_file()
        await tg_file.download_to_drive(str(file_path))
    except Exception as exc:
        logger.error("Media download failed", user_id=user.id, kind=kind, error=str(exc))
        await message.reply_text(
            M.msg_error("Medya indirilemedi."), parse_mode="HTML"
        )
        return

    caption = (message.caption or "").strip()
    instruction = _instruction(kind, caption)
    duration = getattr(media, "duration", None)
    dur_note = f" (~{duration}s)" if duration else ""
    prompt = (
        f"{instruction}\n\n"
        f"[Kullanıcı bir {kind} kaydı gönderdi{dur_note}. "
        f"Dosya yolu: {file_path}]"
    )

    # Initial status message + continuous typing indicator
    status_msg = await message.reply_text(status_icon)
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
                f"Daha kısa bir kayıt dene veya {M.code('/new')} ile sıfırla.",
            ),
            parse_mode="HTML",
        )
        return
    except ClaudeError as exc:
        await status_msg.edit_text(M.msg_error(str(exc)), parse_mode="HTML")
        logger.error("Claude error (media)", user_id=user.id, kind=kind, error=str(exc))
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
            CommandLogModel(user_id=user.id, command=f"<{kind}>", result="ok")
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
