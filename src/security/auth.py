"""Invite-token authentication with role and access-level management.

Flow:
    admin /invite  →  8-char cryptographic token (24h TTL)
    new user /start <token>  →  token validated, user gets 'user' role
    admin /promote /demote  →  role changes
    admin /revoke  →  deactivate token before redemption
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, Optional

import structlog

from src.storage.facade import StorageFacade
from src.storage.models import InviteModel, UserModel

logger = structlog.get_logger(__name__)

Role = Literal["admin", "user", "viewer"]
AccessLevel = Literal["sandbox", "project", "full"]

_TOKEN_BYTES = 4  # 8 hex chars = 4 bytes


class AccessManager:
    """Centralises auth checks and user management.

    The admin user is always bootstrapped from ADMIN_TELEGRAM_ID in config.
    All other users must redeem an invite token.
    """

    def __init__(self, storage: StorageFacade, admin_id: int) -> None:
        self._storage = storage
        self._admin_id = admin_id

    # ── Bootstrap ────────────────────────────────────────────────────────────

    async def ensure_admin(self) -> None:
        """Create admin user record if it doesn't exist yet."""
        existing = await self._storage.users.get(self._admin_id)
        if existing is None:
            admin = UserModel(
                user_id=self._admin_id,
                role="admin",
                access_level="full",
                is_active=True,
            )
            await self._storage.users.create(admin)
            logger.info("Admin user bootstrapped", user_id=self._admin_id)

    # ── Access checks ─────────────────────────────────────────────────────────

    async def is_authorised(self, user_id: int) -> bool:
        """Return True if the user exists and is active."""
        user = await self._storage.users.get(user_id)
        return user is not None and user.is_active

    async def is_admin(self, user_id: int) -> bool:
        """Return True if the user has admin role."""
        if user_id == self._admin_id:
            return True
        user = await self._storage.users.get(user_id)
        return user is not None and user.role == "admin"

    async def get_role(self, user_id: int) -> Optional[Role]:
        user = await self._storage.users.get(user_id)
        return user.role if user else None  # type: ignore[return-value]

    async def get_access_level(self, user_id: int) -> Optional[AccessLevel]:
        user = await self._storage.users.get(user_id)
        return user.access_level if user else None  # type: ignore[return-value]

    # ── Invite flow ───────────────────────────────────────────────────────────

    def generate_token(self) -> str:
        """Generate an 8-character hex invite token."""
        return secrets.token_hex(_TOKEN_BYTES)

    async def create_invite(self, created_by: int, ttl_hours: int = 24) -> InviteModel:
        """Create and persist a new invite token."""
        token = self.generate_token()
        invite = InviteModel(
            token=token,
            created_by=created_by,
            expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
        )
        await self._storage.invites.create(invite)
        logger.info("Invite created", by=created_by, token=token[:4] + "****")
        return invite

    async def redeem_invite(
        self,
        token: str,
        user_id: int,
        username: Optional[str] = None,
    ) -> bool:
        """Redeem a token, creating the user record.  Returns success flag."""
        invite = await self._storage.invites.get(token)
        if invite is None or not invite.is_redeemable():
            logger.warning("Invalid/expired invite token", user_id=user_id)
            return False

        # Check the user is not already registered
        existing = await self._storage.users.get(user_id)
        if existing is not None:
            logger.info("User already exists, skipping invite", user_id=user_id)
            # Still redeem the token so it can't be reused
            await self._storage.invites.redeem(token, user_id)
            return True

        # Create user and mark token as used atomically (best-effort in SQLite)
        redeemed = await self._storage.invites.redeem(token, user_id)
        if not redeemed:
            return False

        new_user = UserModel(
            user_id=user_id,
            username=username,
            role="user",
            access_level="sandbox",
            is_active=True,
        )
        await self._storage.users.create(new_user)
        logger.info("User registered via invite", user_id=user_id)
        return True

    async def revoke_invite(self, token: str) -> None:
        """Deactivate an invite token before it's redeemed."""
        await self._storage.invites.deactivate(token)
        logger.info("Invite revoked", token=token[:4] + "****")

    # ── Role management ───────────────────────────────────────────────────────

    async def promote(self, user_id: int, role: Role = "admin") -> bool:
        """Set user role. Returns False if user not found."""
        user = await self._storage.users.get(user_id)
        if user is None:
            return False
        await self._storage.users.set_role(user_id, role)
        logger.info("User promoted", user_id=user_id, role=role)
        return True

    async def demote(self, user_id: int, role: Role = "user") -> bool:
        """Lower user role. Returns False if user not found."""
        user = await self._storage.users.get(user_id)
        if user is None:
            return False
        await self._storage.users.set_role(user_id, role)
        logger.info("User demoted", user_id=user_id, role=role)
        return True

    async def set_access_level(self, user_id: int, level: AccessLevel) -> bool:
        """Change access level. Returns False if user not found."""
        user = await self._storage.users.get(user_id)
        if user is None:
            return False
        await self._storage.users.set_access_level(user_id, level)
        logger.info("Access level updated", user_id=user_id, level=level)
        return True

    async def deactivate_user(self, user_id: int) -> bool:
        """Deactivate (ban) a user. Returns False if user not found."""
        user = await self._storage.users.get(user_id)
        if user is None:
            return False
        await self._storage.users.deactivate(user_id)
        logger.info("User deactivated", user_id=user_id)
        return True
