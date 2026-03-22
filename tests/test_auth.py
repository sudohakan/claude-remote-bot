"""Tests for security/auth.py, security/rate_limiter.py, and invite flow."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.storage.facade import StorageFacade
from src.storage.models import UserModel
from src.security.auth import AccessManager
from src.security.rate_limiter import RateLimiter


@pytest.fixture
async def storage(tmp_path):
    s = StorageFacade(f"sqlite:///{tmp_path / 'test.db'}")
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def mgr(storage):
    access = AccessManager(storage=storage, admin_id=9000)
    await access.ensure_admin()
    return access


# ── AccessManager ─────────────────────────────────────────────────────────────

class TestAccessManager:
    async def test_ensure_admin_creates_user(self, storage):
        mgr = AccessManager(storage=storage, admin_id=9001)
        await mgr.ensure_admin()
        user = await storage.users.get(9001)
        assert user is not None
        assert user.role == "admin"
        assert user.access_level == "full"

    async def test_ensure_admin_idempotent(self, storage):
        mgr = AccessManager(storage=storage, admin_id=9002)
        await mgr.ensure_admin()
        await mgr.ensure_admin()  # Should not raise
        user = await storage.users.get(9002)
        assert user.role == "admin"

    async def test_is_admin_true(self, mgr):
        assert await mgr.is_admin(9000) is True

    async def test_is_admin_false(self, storage, mgr):
        await storage.users.create(UserModel(user_id=1001, role="user"))
        assert await mgr.is_admin(1001) is False

    async def test_is_authorised_unknown_user(self, mgr):
        assert await mgr.is_authorised(9999) is False

    async def test_invite_create_and_redeem(self, storage, mgr):
        invite = await mgr.create_invite(created_by=9000, ttl_hours=24)
        assert len(invite.token) == 8  # 4 bytes hex = 8 chars

        # Redeem with new user
        ok = await mgr.redeem_invite(invite.token, user_id=2001, username="newuser")
        assert ok is True

        user = await storage.users.get(2001)
        assert user is not None
        assert user.role == "user"
        assert user.access_level == "sandbox"

    async def test_redeem_expired_invite(self, storage, mgr):
        from src.storage.models import InviteModel

        expired = InviteModel(
            token="expired1",
            created_by=9000,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await storage.invites.create(expired)
        ok = await mgr.redeem_invite("expired1", user_id=5001)
        assert ok is False

    async def test_redeem_nonexistent_token(self, mgr):
        ok = await mgr.redeem_invite("badtoken", user_id=5002)
        assert ok is False

    async def test_redeem_already_used(self, storage, mgr):
        invite = await mgr.create_invite(created_by=9000)
        await storage.users.create(UserModel(user_id=6001))
        await storage.users.create(UserModel(user_id=6002))
        await mgr.redeem_invite(invite.token, user_id=6001)
        ok = await mgr.redeem_invite(invite.token, user_id=6002)
        assert ok is False

    async def test_revoke_invite(self, storage, mgr):
        invite = await mgr.create_invite(created_by=9000)
        await mgr.revoke_invite(invite.token)
        fetched = await storage.invites.get(invite.token)
        assert fetched.is_active is False

    async def test_promote_user(self, storage, mgr):
        await storage.users.create(UserModel(user_id=7001, role="user"))
        result = await mgr.promote(7001, role="admin")
        assert result is True
        user = await storage.users.get(7001)
        assert user.role == "admin"

    async def test_demote_user(self, storage, mgr):
        await storage.users.create(UserModel(user_id=7002, role="admin"))
        result = await mgr.demote(7002, role="viewer")
        assert result is True
        user = await storage.users.get(7002)
        assert user.role == "viewer"

    async def test_promote_missing_user(self, mgr):
        result = await mgr.promote(9999, role="admin")
        assert result is False

    async def test_set_access_level(self, storage, mgr):
        await storage.users.create(UserModel(user_id=8001))
        result = await mgr.set_access_level(8001, "full")
        assert result is True
        user = await storage.users.get(8001)
        assert user.access_level == "full"

    async def test_deactivate_user(self, storage, mgr):
        await storage.users.create(UserModel(user_id=8002))
        result = await mgr.deactivate_user(8002)
        assert result is True
        assert await mgr.is_authorised(8002) is False


# ── RateLimiter ───────────────────────────────────────────────────────────────

class TestRateLimiter:
    async def test_allow_within_limit(self):
        limiter = RateLimiter(claude_per_min=20)
        ok, wait = await limiter.check("claude", user_id=1)
        assert ok is True
        assert wait == 0.0

    async def test_deny_when_bucket_empty(self):
        # Small capacity: 1 token
        limiter = RateLimiter(commands_per_min=1)
        # Override the bucket capacity to 1 for testing
        limiter._config["commands"]["capacity"] = 1.0

        await limiter.check("commands", user_id=1)  # consumes the 1 token
        ok, wait = await limiter.check("commands", user_id=1)
        assert ok is False
        assert wait > 0

    async def test_independent_per_user(self):
        limiter = RateLimiter(commands_per_min=1)
        limiter._config["commands"]["capacity"] = 1.0

        ok1, _ = await limiter.check("commands", user_id=1)
        ok2, _ = await limiter.check("commands", user_id=2)
        assert ok1 is True
        assert ok2 is True  # different user, fresh bucket

    async def test_reset_restores_bucket(self):
        limiter = RateLimiter(commands_per_min=1)
        limiter._config["commands"]["capacity"] = 1.0

        await limiter.check("commands", user_id=1)
        ok, _ = await limiter.check("commands", user_id=1)
        assert ok is False

        await limiter.reset(user_id=1, category="commands")
        ok, _ = await limiter.check("commands", user_id=1)
        assert ok is True

    async def test_different_categories_independent(self):
        limiter = RateLimiter(claude_per_min=1, commands_per_min=1)
        limiter._config["claude"]["capacity"] = 1.0
        limiter._config["commands"]["capacity"] = 1.0

        await limiter.check("claude", user_id=1)
        ok_claude, _ = await limiter.check("claude", user_id=1)
        ok_cmds, _ = await limiter.check("commands", user_id=1)

        assert ok_claude is False  # claude bucket empty
        assert ok_cmds is True   # commands bucket still full

    async def test_unknown_category_uses_commands_config(self):
        limiter = RateLimiter()
        ok, _ = await limiter.check("unknown_cat", user_id=1)
        assert ok is True  # falls back to commands config, bucket starts full
