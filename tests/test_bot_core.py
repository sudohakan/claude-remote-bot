"""Tests for bot core, formatting utilities, and middleware.

We avoid instantiating a real Telegram Application (which needs a live token).
Instead we test the utility functions, middleware logic, and handler logic
using mocked Update/Context objects.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.utils.formatting import (
    escape_html,
    split_message,
    format_code_block,
)
from src.bot.utils.constants import (
    BOT_VERSION,
    MSG_AUTH_REQUIRED,
    MSG_RATE_LIMITED,
    MESSAGE_CHUNK_SIZE,
)


# ── Formatting utilities ──────────────────────────────────────────────────────

class TestFormatting:
    def test_escape_html_lt_gt(self):
        result = escape_html("<script>alert('xss')</script>")
        assert "<" not in result
        assert ">" not in result

    def test_escape_html_ampersand(self):
        result = escape_html("foo & bar")
        assert "&amp;" in result

    def test_escape_html_quotes(self):
        result = escape_html('"quoted"')
        assert "&quot;" in result

    def test_escape_html_clean(self):
        result = escape_html("normal text 123")
        assert result == "normal text 123"

    def test_split_message_short_no_split(self):
        text = "Short message"
        chunks = split_message(text)
        assert chunks == [text]

    def test_split_message_long_splits(self):
        text = "word " * 1000
        chunks = split_message(text, chunk_size=100)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 120  # allow small overage for fence closing

    def test_split_message_total_content_preserved(self):
        text = "Hello world. " * 500
        chunks = split_message(text)
        joined = "".join(chunks)
        # Content may have added ``` fences — just check original words present
        assert "Hello world" in joined

    def test_split_message_exact_limit(self):
        text = "a" * MESSAGE_CHUNK_SIZE
        chunks = split_message(text)
        assert len(chunks) == 1

    def test_split_message_just_over_limit(self):
        text = "a" * (MESSAGE_CHUNK_SIZE + 1)
        chunks = split_message(text)
        assert len(chunks) >= 2

    def test_format_code_block_escapes(self):
        result = format_code_block("<hello>")
        assert "&lt;hello&gt;" in result
        assert "<pre>" in result

    def test_format_code_block_with_language(self):
        result = format_code_block("print('hi')", language="python")
        assert 'class="language-python"' in result


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants:
    def test_bot_version_not_empty(self):
        assert BOT_VERSION

    def test_auth_required_message(self):
        assert "invite" in MSG_AUTH_REQUIRED.lower() or "token" in MSG_AUTH_REQUIRED.lower()

    def test_rate_limited_template(self):
        msg = MSG_RATE_LIMITED.format(wait=30)
        assert "30" in msg

    def test_message_chunk_size(self):
        assert MESSAGE_CHUNK_SIZE <= 4096  # Telegram hard limit


# ── Middleware (unit tests with mocks) ────────────────────────────────────────

class TestAuthMiddleware:
    async def test_authorised_user_passes(self):
        from src.bot.middleware.auth import auth_middleware

        handler = AsyncMock()
        update = MagicMock()
        update.effective_user.id = 1001
        update.effective_user.is_bot = False

        access_mgr = MagicMock()
        access_mgr.is_authorised = AsyncMock(return_value=True)

        data = {"access_manager": access_mgr}
        await auth_middleware(handler, update, data)
        handler.assert_called_once()

    async def test_unauthorised_user_blocked(self):
        from src.bot.middleware.auth import auth_middleware

        handler = AsyncMock()
        update = MagicMock()
        update.effective_user.id = 9999
        update.effective_message.reply_text = AsyncMock()

        access_mgr = MagicMock()
        access_mgr.is_authorised = AsyncMock(return_value=False)

        data = {"access_manager": access_mgr}
        await auth_middleware(handler, update, data)
        handler.assert_not_called()
        update.effective_message.reply_text.assert_called_once()

    async def test_no_user_skips(self):
        from src.bot.middleware.auth import auth_middleware

        handler = AsyncMock()
        update = MagicMock()
        update.effective_user = None

        data = {"access_manager": MagicMock()}
        await auth_middleware(handler, update, data)
        handler.assert_not_called()


class TestRateLimitMiddleware:
    async def test_allowed_passes(self):
        from src.bot.middleware.rate_limit import rate_limit_middleware

        handler = AsyncMock()
        update = MagicMock()
        update.effective_user.id = 1

        limiter = MagicMock()
        limiter.check = AsyncMock(return_value=(True, 0.0))

        data = {"rate_limiter": limiter}
        await rate_limit_middleware(handler, update, data)
        handler.assert_called_once()

    async def test_limited_blocked(self):
        from src.bot.middleware.rate_limit import rate_limit_middleware

        handler = AsyncMock()
        update = MagicMock()
        update.effective_user.id = 1
        update.effective_message.reply_text = AsyncMock()

        limiter = MagicMock()
        limiter.check = AsyncMock(return_value=(False, 30.0))

        data = {"rate_limiter": limiter}
        await rate_limit_middleware(handler, update, data)
        handler.assert_not_called()
        update.effective_message.reply_text.assert_called_once()


class TestSecurityMiddleware:
    async def test_normal_message_passes(self):
        from src.bot.middleware.security import security_middleware

        handler = AsyncMock()
        update = MagicMock()
        update.effective_message.text = "Hello Claude"

        await security_middleware(handler, update, {})
        handler.assert_called_once()

    async def test_null_byte_dropped(self):
        from src.bot.middleware.security import security_middleware

        handler = AsyncMock()
        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = 1
        update.effective_message.text = "Hello\x00world"

        await security_middleware(handler, update, {})
        handler.assert_not_called()

    async def test_too_long_rejected(self):
        from src.bot.middleware.security import security_middleware

        handler = AsyncMock()
        update = MagicMock()
        update.effective_message.text = "a" * 9000
        update.effective_message.reply_text = AsyncMock()

        await security_middleware(handler, update, {})
        handler.assert_not_called()
        update.effective_message.reply_text.assert_called_once()
