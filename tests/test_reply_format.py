"""Reply format contract: one sender for Claude replies, HTML with plain-text fallback.

(a) `send_claude_reply` falls back to the SAME chunk as plain text when Telegram rejects
    the HTML render (never the rendered form).
(b) All four Claude-reply handlers (text, document, photo, media) go through that one
    function — asserted by patching the name in each handler module, not by grep.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.utils.formatting import send_claude_reply


class _Reject(Exception):
    """Stands in for telegram.error.BadRequest ('can't parse entities')."""


# ── (a) fallback contract ───────────────────────────────────────────────────


async def test_html_rejected_falls_back_to_same_plain_chunk():
    message = MagicMock()
    message.reply_text = AsyncMock(side_effect=[_Reject("can't parse entities"), None])
    logger = MagicMock()

    content = "Use <uuid> here and `code` there"
    await send_claude_reply(message, content, logger=logger)

    assert message.reply_text.await_count == 2
    first, second = message.reply_text.await_args_list
    assert first.kwargs["parse_mode"] == "HTML"
    assert "&lt;uuid&gt;" in first.args[0]  # the HTML render was attempted
    assert second.kwargs["parse_mode"] is None
    assert second.args[0] == content  # raw chunk, not the escaped render
    logger.warning.assert_called_once()


async def test_html_accepted_sends_once():
    message = MagicMock()
    message.reply_text = AsyncMock()
    await send_claude_reply(message, "hello", logger=MagicMock())
    message.reply_text.assert_awaited_once()
    assert message.reply_text.await_args.kwargs["parse_mode"] == "HTML"


async def test_empty_content_has_one_placeholder():
    message = MagicMock()
    message.reply_text = AsyncMock()
    await send_claude_reply(message, None, logger=MagicMock())
    assert message.reply_text.await_args.args[0] == "(no response)"


# ── (b) every handler uses the single sender ────────────────────────────────


def _ctx(monkeypatch):
    """bot_data with the collaborators every handler asks for, all permissive."""
    claude = MagicMock()
    claude.execute = AsyncMock(
        return_value=SimpleNamespace(content="answer", cost=0.0, num_turns=1)
    )
    limiter = MagicMock()
    limiter.check = AsyncMock(return_value=(True, 0))
    access = MagicMock()
    access.get_access_level = AsyncMock(return_value="sandbox")
    access.get_role = AsyncMock(return_value="user")
    ctx = MagicMock()
    ctx.bot_data = {
        "claude_facade": claude,
        "rate_limiter": limiter,
        "access_manager": access,
        "storage": None,
    }
    ctx.bot = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()
    return ctx


def _update(**message_fields):
    upd = MagicMock()
    upd.effective_user.id = 1
    upd.effective_user.username = "u"
    upd.effective_chat.id = 1
    msg = MagicMock()
    msg.chat_id = 1
    msg.reply_text = AsyncMock(
        return_value=MagicMock(edit_text=AsyncMock(), delete=AsyncMock())
    )
    for name in ("text", "document", "photo", "voice", "audio", "video_note", "video"):
        setattr(msg, name, None)
    for k, v in message_fields.items():
        setattr(msg, k, v)
    upd.effective_message = msg
    upd.message = msg
    return upd


def _tg_file():
    f = MagicMock()

    async def _download(path):
        Path(path).write_bytes(b"x")

    f.download_to_drive = AsyncMock(side_effect=_download)
    return f


def _media(**attrs):
    m = MagicMock()
    m.get_file = AsyncMock(return_value=_tg_file())
    m.file_size = 10
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


HANDLERS = [
    ("src.bot.handlers.message", "handle_message", lambda: _update(text="hi")),
    (
        "src.bot.handlers.document",
        "handle_document",
        lambda: _update(document=_media(file_name="a.txt", mime_type="text/plain")),
    ),
    ("src.bot.handlers.photo", "handle_photo", lambda: _update(photo=[_media()])),
    (
        "src.bot.handlers.media",
        "handle_media",
        lambda: _update(voice=_media(file_name="v.ogg", duration=1)),
    ),
]


@pytest.mark.parametrize("module_path,fn_name,make_update", HANDLERS)
async def test_handler_replies_through_single_sender(
    monkeypatch, module_path, fn_name, make_update
):
    import importlib

    module = importlib.import_module(module_path)
    sender = AsyncMock()
    # The handler holds its own reference (module-level import): patch it THERE.
    monkeypatch.setattr(module, "send_claude_reply", sender)
    if hasattr(module, "_keep_typing"):

        async def _quiet(*_a, **_k):
            return None

        monkeypatch.setattr(module, "_keep_typing", _quiet)

    upd = make_update()
    with patch("src.bot.handlers.callback.handle_callback", new=AsyncMock()):
        await getattr(module, fn_name)(upd, _ctx(monkeypatch))

    sender.assert_awaited_once()
    assert sender.await_args.args[1] == "answer"
    # Claude content never leaves through the handler's own reply_text
    for call in upd.effective_message.reply_text.await_args_list:
        assert "answer" not in (call.args[0] if call.args else "")
