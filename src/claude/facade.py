"""Unified Claude interface for the bot handlers.

The facade wires together SDK runner, session manager, cost tracker,
and sanitizer into a single call surface.
"""

from typing import Optional

import structlog

from src.storage.repositories import UserRepository

from .exceptions import ClaudeError
from .monitor import CostTracker
from .sanitizer import CredentialSanitizer
from .sdk_integration import ClaudeResponse, ClaudeSDKRunner
from .session import SessionManager, UserSession

logger = structlog.get_logger(__name__)


class ClaudeFacade:
    """Single entry point for Claude execution from bot handlers."""

    def __init__(
        self,
        runner: ClaudeSDKRunner,
        session_mgr: SessionManager,
        cost_tracker: CostTracker,
        sanitizer: CredentialSanitizer,
        admin_id: int,
        user_repo: UserRepository,
        default_user_limit: float = 5.0,
    ) -> None:
        self._runner = runner
        self._sessions = session_mgr
        self._costs = cost_tracker
        self._sanitizer = sanitizer
        self._admin_id = admin_id
        self._user_repo = user_repo
        self._default_limit = default_user_limit

    async def _resolve_limit(self, user_id: int) -> Optional[float]:
        """Return the daily cost cap (USD) for a non-admin user.

        - None  → no cap applies (user effectively unlimited; admin treatment).
        - >= 0  → hard daily cap.
        """
        row = await self._user_repo.get(user_id)
        per_user = row.daily_cost_limit if row else None
        effective = per_user if per_user is not None else self._default_limit
        if effective is None or effective < 0:
            return None
        return effective

    async def execute(
        self,
        user_id: int,
        prompt: str,
        access_level: str = "sandbox",
        username: Optional[str] = None,
        new_session: bool = False,
        role: str = "user",
    ) -> ClaudeResponse:
        """Run a Claude prompt for a user, managing their session.

        Raises ClaudeError subclasses on failure.
        Sanitizes credential patterns from the response.
        """
        # Cost guard — admin (telegram admin_id or DB role='admin') is exempt.
        # CLI/subscription mode tracks fiat-equivalent cost only for visibility.
        is_admin = user_id == self._admin_id or role == "admin"
        if not is_admin:
            limit = await self._resolve_limit(user_id)
            if limit is not None:
                today_spend = self._costs.today_cost(user_id)
                if today_spend >= limit:
                    from .exceptions import ClaudeAuthError

                    raise ClaudeAuthError(
                        f"Günlük kullanım limiti aşıldı "
                        f"(${today_spend:.2f}/${limit:.2f}). "
                        f"Admin'e başvur."
                    )

        # Session management
        if new_session:
            session = self._sessions.reset(user_id, access_level, username)
        else:
            session = self._sessions.get_or_create(user_id, access_level, username)

        try:
            response = await self._runner.run(
                prompt=prompt,
                working_dir=session.working_dir,
                session_id=session.session_id,
                continue_session=not new_session,
                full_access=(role == "admin"),
            )
        except ClaudeError:
            raise
        except Exception as exc:
            from .exceptions import ClaudeProcessError

            raise ClaudeProcessError(f"Unexpected error: {exc}") from exc

        # Adopt Claude's real session_id so the next turn can --resume it.
        # Without this, the in-memory UUID stays random and every message
        # silently starts a fresh Claude conversation (context-loss bug).
        if response.session_id and response.session_id != session.session_id:
            session.session_id = response.session_id

        # Update session stats
        session.touch(cost_delta=response.cost, turns_delta=response.num_turns)
        if response.tools_used:
            session.add_tools([t.get("name", "") for t in response.tools_used])

        # Track costs
        self._costs.record(user_id, response.cost, response.num_turns)

        # Sanitize output
        response.content = self._sanitizer.sanitize(response.content)

        return response

    def new_session(
        self,
        user_id: int,
        access_level: str = "sandbox",
        username: Optional[str] = None,
    ) -> UserSession:
        """Force a new session without running a prompt."""
        return self._sessions.reset(user_id, access_level, username)

    def current_session(self, user_id: int) -> Optional[UserSession]:
        return self._sessions.get(user_id)

    def cost_summary(self, user_id: int) -> dict:
        return self._costs.summary(user_id)
