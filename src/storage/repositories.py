"""Repository pattern — one class per table, clean CRUD interface.

Each repository takes a DatabaseManager and exposes async methods.
No raw SQL leaks outside this module.
"""

from datetime import UTC, datetime
from typing import List, Literal, Optional

import structlog

from .database import DatabaseManager
from .models import CommandLogModel, InviteModel, SessionModel, UserModel

logger = structlog.get_logger(__name__)


# ── UserRepository ────────────────────────────────────────────────────────────


class UserRepository:
    """CRUD for the users table."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def get(self, user_id: int) -> Optional[UserModel]:
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            )
            row = await cur.fetchone()
        return UserModel.from_row(row) if row else None

    async def create(self, user: UserModel) -> UserModel:
        now = datetime.now(UTC)
        async with self._db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO users
                    (user_id, username, first_seen, last_active,
                     role, access_level, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.user_id,
                    user.username,
                    user.first_seen or now,
                    user.last_active or now,
                    user.role,
                    user.access_level,
                    user.is_active,
                ),
            )
            await conn.commit()
        logger.info("User created", user_id=user.user_id)
        return user

    async def upsert(self, user: UserModel) -> UserModel:
        """Insert or update a user record."""
        now = datetime.now(UTC)
        async with self._db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO users
                    (user_id, username, first_seen, last_active,
                     role, access_level, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username     = excluded.username,
                    last_active  = excluded.last_active,
                    role         = excluded.role,
                    access_level = excluded.access_level,
                    is_active    = excluded.is_active
                """,
                (
                    user.user_id,
                    user.username,
                    user.first_seen or now,
                    user.last_active or now,
                    user.role,
                    user.access_level,
                    user.is_active,
                ),
            )
            await conn.commit()
        return user

    async def update_activity(self, user_id: int, cost_delta: float = 0.0) -> None:
        now = datetime.now(UTC)
        async with self._db.get_connection() as conn:
            await conn.execute(
                """
                UPDATE users
                SET last_active   = ?,
                    total_cost    = total_cost + ?,
                    message_count = message_count + 1
                WHERE user_id = ?
                """,
                (now, cost_delta, user_id),
            )
            await conn.commit()

    async def set_role(
        self, user_id: int, role: Literal["admin", "user", "viewer"]
    ) -> None:
        async with self._db.get_connection() as conn:
            await conn.execute(
                "UPDATE users SET role = ? WHERE user_id = ?", (role, user_id)
            )
            await conn.commit()

    async def set_access_level(
        self, user_id: int, level: Literal["sandbox", "project", "full"]
    ) -> None:
        async with self._db.get_connection() as conn:
            await conn.execute(
                "UPDATE users SET access_level = ? WHERE user_id = ?",
                (level, user_id),
            )
            await conn.commit()

    async def deactivate(self, user_id: int) -> None:
        async with self._db.get_connection() as conn:
            await conn.execute(
                "UPDATE users SET is_active = FALSE WHERE user_id = ?", (user_id,)
            )
            await conn.commit()

    async def list_active(self) -> List[UserModel]:
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM users WHERE is_active = TRUE ORDER BY first_seen DESC"
            )
            rows = await cur.fetchall()
        return [UserModel.from_row(r) for r in rows]


# ── InviteRepository ──────────────────────────────────────────────────────────


class InviteRepository:
    """CRUD for the invites table."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def create(self, invite: InviteModel) -> InviteModel:
        now = datetime.now(UTC)
        async with self._db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO invites
                    (token, created_by, created_at, expires_at, is_active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    invite.token,
                    invite.created_by,
                    invite.created_at or now,
                    invite.expires_at,
                    invite.is_active,
                ),
            )
            await conn.commit()
        logger.info("Invite created", token=invite.token[:4] + "****")
        return invite

    async def get(self, token: str) -> Optional[InviteModel]:
        async with self._db.get_connection() as conn:
            cur = await conn.execute("SELECT * FROM invites WHERE token = ?", (token,))
            row = await cur.fetchone()
        return InviteModel.from_row(row) if row else None

    async def redeem(self, token: str, user_id: int) -> bool:
        """Mark token as redeemed. Returns True if successful."""
        invite = await self.get(token)
        if not invite or not invite.is_redeemable():
            return False

        now = datetime.now(UTC)
        async with self._db.get_connection() as conn:
            await conn.execute(
                """
                UPDATE invites
                SET redeemed_by = ?, redeemed_at = ?, is_active = FALSE
                WHERE token = ? AND is_active = TRUE AND redeemed_by IS NULL
                """,
                (user_id, now, token),
            )
            await conn.commit()

        logger.info("Invite redeemed", user_id=user_id)
        return True

    async def deactivate(self, token: str) -> None:
        async with self._db.get_connection() as conn:
            await conn.execute(
                "UPDATE invites SET is_active = FALSE WHERE token = ?", (token,)
            )
            await conn.commit()

    async def list_by_creator(self, created_by: int) -> List[InviteModel]:
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM invites WHERE created_by = ? ORDER BY created_at DESC",
                (created_by,),
            )
            rows = await cur.fetchall()
        return [InviteModel.from_row(r) for r in rows]

    async def count_recent(self, created_by: int, hours: int = 1) -> int:
        """Count invites created by user in the last N hours (for rate limiting)."""
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                """
                SELECT COUNT(*) FROM invites
                WHERE created_by = ?
                  AND created_at > datetime('now', '-' || ? || ' hours')
                """,
                (created_by, hours),
            )
            row = await cur.fetchone()
        return row[0] if row else 0


# ── SessionRepository ─────────────────────────────────────────────────────────


class SessionRepository:
    """CRUD for the claude_sessions table."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def create(self, session: SessionModel) -> SessionModel:
        now = datetime.now(UTC)
        async with self._db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO claude_sessions
                    (session_id, user_id, working_dir, created_at, last_used)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.user_id,
                    session.working_dir,
                    session.created_at or now,
                    session.last_used or now,
                ),
            )
            await conn.commit()
        return session

    async def get(self, session_id: str) -> Optional[SessionModel]:
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM claude_sessions WHERE session_id = ?", (session_id,)
            )
            row = await cur.fetchone()
        return SessionModel.from_row(row) if row else None

    async def get_active_for_user(self, user_id: int) -> Optional[SessionModel]:
        """Return the most recently used active session for a user."""
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM claude_sessions
                WHERE user_id = ? AND is_active = TRUE
                ORDER BY last_used DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cur.fetchone()
        return SessionModel.from_row(row) if row else None

    async def update_usage(
        self, session_id: str, cost_delta: float, turns_delta: int = 1
    ) -> None:
        now = datetime.now(UTC)
        async with self._db.get_connection() as conn:
            await conn.execute(
                """
                UPDATE claude_sessions
                SET last_used   = ?,
                    total_cost  = total_cost + ?,
                    total_turns = total_turns + ?
                WHERE session_id = ?
                """,
                (now, cost_delta, turns_delta, session_id),
            )
            await conn.commit()

    async def deactivate(self, session_id: str) -> None:
        async with self._db.get_connection() as conn:
            await conn.execute(
                "UPDATE claude_sessions SET is_active = FALSE WHERE session_id = ?",
                (session_id,),
            )
            await conn.commit()

    async def deactivate_all_for_user(self, user_id: int) -> int:
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                """
                UPDATE claude_sessions SET is_active = FALSE
                WHERE user_id = ? AND is_active = TRUE
                """,
                (user_id,),
            )
            await conn.commit()
        return cur.rowcount

    async def list_active_for_user(self, user_id: int) -> List[SessionModel]:
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM claude_sessions
                WHERE user_id = ? AND is_active = TRUE
                ORDER BY last_used DESC
                """,
                (user_id,),
            )
            rows = await cur.fetchall()
        return [SessionModel.from_row(r) for r in rows]


# ── CommandLogRepository ──────────────────────────────────────────────────────


class CommandLogRepository:
    """CRUD for the command_log table with 30-day retention."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def log(self, entry: CommandLogModel) -> int:
        now = datetime.now(UTC)
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO command_log (user_id, command, args, result, logged_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry.user_id,
                    entry.command,
                    entry.args,
                    entry.result,
                    entry.logged_at or now,
                ),
            )
            await conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def recent_for_user(
        self, user_id: int, limit: int = 20
    ) -> List[CommandLogModel]:
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM command_log
                WHERE user_id = ?
                ORDER BY logged_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cur.fetchall()
        return [CommandLogModel.from_row(r) for r in rows]

    async def purge_older_than_days(self, days: int = 30) -> int:
        """Delete log entries older than `days`. Returns deleted row count."""
        async with self._db.get_connection() as conn:
            cur = await conn.execute(
                """
                DELETE FROM command_log
                WHERE logged_at < datetime('now', '-' || ? || ' days')
                """,
                (days,),
            )
            await conn.commit()
        deleted = cur.rowcount
        if deleted:
            logger.info("Command log purged", deleted=deleted, days=days)
        return deleted
