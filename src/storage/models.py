"""Dataclass models for each storage table.

All models implement:
- from_row(row) — construct from aiosqlite.Row
- to_dict() — serialise to plain dict (datetimes as ISO strings)
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Dict, Literal, Optional

import aiosqlite


def _parse_dt(value: Any) -> Optional[datetime]:
    """Coerce an ISO string or datetime to datetime, or return None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value  # type: ignore[return-value]


# ── User ─────────────────────────────────────────────────────────────────────


@dataclass
class UserModel:
    """Represents a row in the users table."""

    user_id: int
    username: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_active: Optional[datetime] = None
    role: Literal["admin", "user", "viewer"] = "user"
    access_level: Literal["sandbox", "project", "full"] = "sandbox"
    is_active: bool = True
    total_cost: float = 0.0
    message_count: int = 0

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "UserModel":
        data = dict(row)
        data["first_seen"] = _parse_dt(data.get("first_seen"))
        data["last_active"] = _parse_dt(data.get("last_active"))
        data["is_active"] = bool(data.get("is_active", True))
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for key in ("first_seen", "last_active"):
            if d[key] is not None:
                d[key] = d[key].isoformat()
        return d


# ── Invite token ─────────────────────────────────────────────────────────────


@dataclass
class InviteModel:
    """Represents a row in the invites table."""

    token: str
    created_by: int
    expires_at: datetime
    created_at: Optional[datetime] = None
    redeemed_by: Optional[int] = None
    redeemed_at: Optional[datetime] = None
    is_active: bool = True

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "InviteModel":
        data = dict(row)
        data["expires_at"] = _parse_dt(data["expires_at"])
        data["created_at"] = _parse_dt(data.get("created_at"))
        data["redeemed_at"] = _parse_dt(data.get("redeemed_at"))
        data["is_active"] = bool(data.get("is_active", True))
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for key in ("expires_at", "created_at", "redeemed_at"):
            if d[key] is not None:
                d[key] = d[key].isoformat()
        return d

    def is_expired(self) -> bool:
        """True if the invite has passed its expiry timestamp."""
        return datetime.now(UTC) > self.expires_at.replace(tzinfo=UTC)

    def is_redeemable(self) -> bool:
        """True if the invite can still be used."""
        return self.is_active and not self.is_expired() and self.redeemed_by is None


# ── Claude session ────────────────────────────────────────────────────────────


@dataclass
class SessionModel:
    """Represents a row in the claude_sessions table."""

    session_id: str
    user_id: int
    working_dir: str
    created_at: Optional[datetime] = None
    last_used: Optional[datetime] = None
    total_turns: int = 0
    total_cost: float = 0.0
    is_active: bool = True

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "SessionModel":
        data = dict(row)
        data["created_at"] = _parse_dt(data.get("created_at"))
        data["last_used"] = _parse_dt(data.get("last_used"))
        data["is_active"] = bool(data.get("is_active", True))
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for key in ("created_at", "last_used"):
            if d[key] is not None:
                d[key] = d[key].isoformat()
        return d

    def is_expired(self, timeout_hours: int) -> bool:
        """True if the session has not been used within timeout_hours."""
        if self.last_used is None:
            return True
        age = datetime.now(UTC) - self.last_used.replace(tzinfo=UTC)
        return age.total_seconds() > timeout_hours * 3600


# ── Command log ───────────────────────────────────────────────────────────────


@dataclass
class CommandLogModel:
    """Represents a row in the command_log table."""

    user_id: int
    command: str
    result: Literal["ok", "error", "denied"] = "ok"
    args: Optional[str] = None
    logged_at: Optional[datetime] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "CommandLogModel":
        data = dict(row)
        data["logged_at"] = _parse_dt(data.get("logged_at"))
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["logged_at"] is not None:
            d["logged_at"] = d["logged_at"].isoformat()
        return d
