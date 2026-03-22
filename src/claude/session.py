"""Per-user Claude session management.

Working directory assignment by access level:
  sandbox  → /tmp/claude-sandbox/<user_id>
  project  → ~/claude-users/<username>
  full     → any path (admin sets it)
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

_SANDBOX_BASE = Path("/tmp/claude-sandbox")
_PROJECT_BASE = Path.home() / "claude-users"


@dataclass
class UserSession:
    """In-memory Claude session state."""

    session_id: str
    user_id: int
    working_dir: Path
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used: datetime = field(default_factory=lambda: datetime.now(UTC))
    total_turns: int = 0
    total_cost: float = 0.0
    tools_used: List[str] = field(default_factory=list)

    def touch(self, cost_delta: float = 0.0, turns_delta: int = 1) -> None:
        """Update activity timestamp and accumulate usage."""
        self.last_used = datetime.now(UTC)
        self.total_cost += cost_delta
        self.total_turns += turns_delta

    def add_tools(self, tool_names: List[str]) -> None:
        for name in tool_names:
            if name not in self.tools_used:
                self.tools_used.append(name)

    def is_expired(self, timeout_hours: int) -> bool:
        age = datetime.now(UTC) - self.last_used.replace(tzinfo=UTC)
        return age > timedelta(hours=timeout_hours)


class SessionManager:
    """Create and manage per-user Claude sessions.

    Uses in-memory store only; the storage facade persists metadata separately.
    """

    def __init__(self, timeout_hours: int = 24) -> None:
        self._timeout = timeout_hours
        self._sessions: Dict[int, UserSession] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def get_or_create(
        self,
        user_id: int,
        access_level: str = "sandbox",
        username: Optional[str] = None,
    ) -> UserSession:
        """Return existing active session or create a new one."""
        existing = self._sessions.get(user_id)
        if existing and not existing.is_expired(self._timeout):
            return existing

        session = self._new_session(user_id, access_level, username)
        self._sessions[user_id] = session
        logger.info(
            "Session created",
            user_id=user_id,
            working_dir=str(session.working_dir),
            access_level=access_level,
        )
        return session

    def reset(self, user_id: int, access_level: str = "sandbox", username: Optional[str] = None) -> UserSession:
        """Force-create a fresh session, discarding any existing one."""
        session = self._new_session(user_id, access_level, username)
        self._sessions[user_id] = session
        logger.info("Session reset", user_id=user_id)
        return session

    def get(self, user_id: int) -> Optional[UserSession]:
        """Return active session or None."""
        session = self._sessions.get(user_id)
        if session and session.is_expired(self._timeout):
            del self._sessions[user_id]
            return None
        return session

    def end(self, user_id: int) -> None:
        """Remove session."""
        self._sessions.pop(user_id, None)

    def active_count(self) -> int:
        return len(self._sessions)

    # ── Working directory resolution ──────────────────────────────────────────

    @staticmethod
    def working_dir_for(
        user_id: int,
        access_level: str,
        username: Optional[str] = None,
    ) -> Path:
        """Resolve working directory based on access level."""
        if access_level == "sandbox":
            d = _SANDBOX_BASE / str(user_id)
        elif access_level == "project":
            safe_name = username or str(user_id)
            d = _PROJECT_BASE / safe_name
        else:
            # full access: use home directory
            d = Path.home()

        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Internal ──────────────────────────────────────────────────────────────

    def _new_session(
        self,
        user_id: int,
        access_level: str,
        username: Optional[str],
    ) -> UserSession:
        working_dir = self.working_dir_for(user_id, access_level, username)
        return UserSession(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            working_dir=working_dir,
        )
