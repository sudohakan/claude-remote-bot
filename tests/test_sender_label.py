"""Every message the bot sends on its own names its sender in the first line (Hakan tells it from Jarvis)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.notifications import service as svc
from src.notifications.sender import BOT_SERVICE, BOT_SESSION, TUNNEL, labelled


def test_labelled_heads_once():
    assert labelled("selam", BOT_SERVICE) == f"{BOT_SERVICE}\nselam"
    assert labelled(f"{BOT_SERVICE}\nselam", BOT_SERVICE) == f"{BOT_SERVICE}\nselam"
    assert labelled("selam", BOT_SERVICE, html=True) == f"<b>{BOT_SERVICE}</b>\nselam"


def test_notification_parts_carry_the_line(monkeypatch):
    async def same(text):
        return text

    monkeypatch.setattr(svc, "humanize", same)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    ns = svc.NotificationService.__new__(svc.NotificationService)
    ns.bot = bot
    ns._last_send = {}
    asyncio.run(ns._do_send(1, "x" * 5000, None, head=BOT_SESSION))
    texts = [c.kwargs["text"] for c in bot.send_message.await_args_list]
    assert len(texts) == 2
    assert all(
        t.startswith(BOT_SESSION + "\n") and len(t) <= 4096 for t in texts
    )  # parse_mode None: plain line
    bot.send_message.reset_mock()
    asyncio.run(ns._do_send(1, "kısa"))
    assert bot.send_message.await_args.kwargs["text"] == f"<b>{BOT_SERVICE}</b>\nkısa"


def test_tunnel_line_is_distinct():
    assert TUNNEL != BOT_SERVICE and TUNNEL.endswith("(otomatik):")
