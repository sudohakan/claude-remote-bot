"""Tests for the storage layer."""

import secrets
from datetime import UTC, datetime, timedelta

import pytest

from src.storage.database import DatabaseManager
from src.storage.facade import StorageFacade
from src.storage.models import (
    CommandLogModel,
    InviteModel,
    SessionModel,
    UserModel,
)


@pytest.fixture
async def db(tmp_path):
    """In-memory-equivalent: fresh SQLite in tmp dir per test."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'test.db'}")
    await mgr.initialize()
    yield mgr
    await mgr.close()


@pytest.fixture
async def storage(tmp_path):
    facade = StorageFacade(f"sqlite:///{tmp_path / 'test.db'}")
    await facade.initialize()
    yield facade
    await facade.close()


# ── DatabaseManager ──────────────────────────────────────────────────────────


class TestDatabaseManager:
    async def test_health_check(self, db):
        assert await db.health_check() is True

    async def test_double_init_is_safe(self, tmp_path):
        """Running initialize twice should not raise."""
        mgr = DatabaseManager(f"sqlite:///{tmp_path / 'idempotent.db'}")
        await mgr.initialize()
        await mgr.initialize()
        assert await mgr.health_check()
        await mgr.close()

    async def test_connection_pool_returns_connection(self, db):
        async with db.get_connection() as conn:
            cur = await conn.execute("SELECT 1")
            row = await cur.fetchone()
        assert row[0] == 1


# ── UserRepository ────────────────────────────────────────────────────────────


class TestUserRepository:
    async def test_create_and_get(self, storage):
        user = UserModel(user_id=1001, username="alice")
        await storage.users.create(user)
        fetched = await storage.users.get(1001)
        assert fetched is not None
        assert fetched.username == "alice"
        assert fetched.role == "user"
        assert fetched.access_level == "sandbox"

    async def test_get_missing_returns_none(self, storage):
        assert await storage.users.get(9999) is None

    async def test_upsert_updates_existing(self, storage):
        user = UserModel(user_id=2001, username="bob", role="user")
        await storage.users.create(user)
        updated = UserModel(user_id=2001, username="bob_renamed", role="admin")
        await storage.users.upsert(updated)
        fetched = await storage.users.get(2001)
        assert fetched.username == "bob_renamed"
        assert fetched.role == "admin"

    async def test_set_role(self, storage):
        await storage.users.create(UserModel(user_id=3001))
        await storage.users.set_role(3001, "viewer")
        u = await storage.users.get(3001)
        assert u.role == "viewer"

    async def test_set_access_level(self, storage):
        await storage.users.create(UserModel(user_id=4001))
        await storage.users.set_access_level(4001, "full")
        u = await storage.users.get(4001)
        assert u.access_level == "full"

    async def test_update_activity(self, storage):
        await storage.users.create(UserModel(user_id=5001))
        await storage.users.update_activity(5001, cost_delta=0.05)
        u = await storage.users.get(5001)
        assert u.total_cost == pytest.approx(0.05)
        assert u.message_count == 1

    async def test_deactivate(self, storage):
        await storage.users.create(UserModel(user_id=6001, is_active=True))
        await storage.users.deactivate(6001)
        u = await storage.users.get(6001)
        assert u.is_active is False

    async def test_list_active(self, storage):
        await storage.users.create(UserModel(user_id=7001))
        await storage.users.create(UserModel(user_id=7002))
        await storage.users.deactivate(7002)
        active = await storage.users.list_active()
        ids = [u.user_id for u in active]
        assert 7001 in ids
        assert 7002 not in ids


# ── InviteRepository ──────────────────────────────────────────────────────────


class TestInviteRepository:
    def _make_invite(self, created_by: int = 1, hours: int = 24) -> InviteModel:
        return InviteModel(
            token=secrets.token_hex(4),
            created_by=created_by,
            expires_at=datetime.now(UTC) + timedelta(hours=hours),
        )

    async def _ensure_user(self, storage, user_id: int) -> None:
        await storage.users.create(UserModel(user_id=user_id))

    async def test_create_and_get(self, storage):
        await self._ensure_user(storage, 1)
        invite = self._make_invite(created_by=1)
        await storage.invites.create(invite)
        fetched = await storage.invites.get(invite.token)
        assert fetched is not None
        assert fetched.token == invite.token
        assert fetched.is_active is True

    async def test_get_missing_returns_none(self, storage):
        assert await storage.invites.get("nonexistent") is None

    async def test_redeem_success(self, storage):
        await self._ensure_user(storage, 10)
        await self._ensure_user(storage, 20)
        invite = self._make_invite(created_by=10)
        await storage.invites.create(invite)

        result = await storage.invites.redeem(invite.token, user_id=20)
        assert result is True

        fetched = await storage.invites.get(invite.token)
        assert fetched.redeemed_by == 20
        assert fetched.is_active is False

    async def test_redeem_expired_fails(self, storage):
        await self._ensure_user(storage, 10)
        invite = self._make_invite(created_by=10, hours=-1)  # already expired
        await storage.invites.create(invite)

        result = await storage.invites.redeem(invite.token, user_id=99)
        assert result is False

    async def test_redeem_twice_fails(self, storage):
        await self._ensure_user(storage, 10)
        await self._ensure_user(storage, 20)
        await self._ensure_user(storage, 30)
        invite = self._make_invite(created_by=10)
        await storage.invites.create(invite)

        await storage.invites.redeem(invite.token, user_id=20)
        result = await storage.invites.redeem(invite.token, user_id=30)
        assert result is False

    async def test_deactivate(self, storage):
        await self._ensure_user(storage, 10)
        invite = self._make_invite(created_by=10)
        await storage.invites.create(invite)
        await storage.invites.deactivate(invite.token)
        fetched = await storage.invites.get(invite.token)
        assert fetched.is_active is False
        assert fetched.is_redeemable() is False

    async def test_count_recent(self, storage):
        await self._ensure_user(storage, 10)
        for _ in range(3):
            await storage.invites.create(self._make_invite(created_by=10))
        count = await storage.invites.count_recent(created_by=10, hours=1)
        assert count == 3


# ── SessionRepository ─────────────────────────────────────────────────────────


class TestSessionRepository:
    async def _ensure_user(self, storage, user_id: int) -> None:
        await storage.users.create(UserModel(user_id=user_id))

    def _make_session(self, user_id: int, session_id: str = "sess-1") -> SessionModel:
        return SessionModel(
            session_id=session_id,
            user_id=user_id,
            working_dir="/tmp/sandbox",
        )

    async def test_create_and_get(self, storage):
        await self._ensure_user(storage, 100)
        sess = self._make_session(100)
        await storage.sessions.create(sess)
        fetched = await storage.sessions.get("sess-1")
        assert fetched is not None
        assert fetched.user_id == 100
        assert fetched.is_active is True

    async def test_get_active_for_user(self, storage):
        await self._ensure_user(storage, 200)
        await storage.sessions.create(self._make_session(200, "s-a"))
        await storage.sessions.create(self._make_session(200, "s-b"))
        await storage.sessions.deactivate("s-a")
        active = await storage.sessions.get_active_for_user(200)
        assert active is not None
        assert active.session_id == "s-b"

    async def test_update_usage(self, storage):
        await self._ensure_user(storage, 300)
        await storage.sessions.create(self._make_session(300, "s-cost"))
        await storage.sessions.update_usage("s-cost", cost_delta=0.10, turns_delta=2)
        fetched = await storage.sessions.get("s-cost")
        assert fetched.total_cost == pytest.approx(0.10)
        assert fetched.total_turns == 2

    async def test_deactivate(self, storage):
        await self._ensure_user(storage, 400)
        await storage.sessions.create(self._make_session(400, "s-deact"))
        await storage.sessions.deactivate("s-deact")
        fetched = await storage.sessions.get("s-deact")
        assert fetched.is_active is False

    async def test_deactivate_all_for_user(self, storage):
        await self._ensure_user(storage, 500)
        await storage.sessions.create(self._make_session(500, "s1"))
        await storage.sessions.create(self._make_session(500, "s2"))
        count = await storage.sessions.deactivate_all_for_user(500)
        assert count == 2
        assert await storage.sessions.get_active_for_user(500) is None

    async def test_is_expired(self, storage):
        await self._ensure_user(storage, 600)
        sess = SessionModel(
            session_id="s-old",
            user_id=600,
            working_dir="/tmp",
            last_used=datetime(2020, 1, 1, tzinfo=UTC),
        )
        await storage.sessions.create(sess)
        fetched = await storage.sessions.get("s-old")
        assert fetched.is_expired(timeout_hours=24) is True


# ── CommandLogRepository ──────────────────────────────────────────────────────


class TestCommandLogRepository:
    async def _ensure_user(self, storage, user_id: int) -> None:
        await storage.users.create(UserModel(user_id=user_id))

    async def test_log_and_retrieve(self, storage):
        await self._ensure_user(storage, 700)
        entry = CommandLogModel(user_id=700, command="/ping", result="ok")
        await storage.commands.log(entry)
        recent = await storage.commands.recent_for_user(700)
        assert len(recent) == 1
        assert recent[0].command == "/ping"

    async def test_purge_old_entries(self, storage):
        await self._ensure_user(storage, 800)
        old_entry = CommandLogModel(
            user_id=800,
            command="/old",
            result="ok",
            logged_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        recent_entry = CommandLogModel(user_id=800, command="/new", result="ok")
        await storage.commands.log(old_entry)
        await storage.commands.log(recent_entry)

        deleted = await storage.commands.purge_older_than_days(days=30)
        assert deleted >= 1

        remaining = await storage.commands.recent_for_user(800)
        commands = [e.command for e in remaining]
        assert "/new" in commands
        assert "/old" not in commands
