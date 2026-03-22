"""Unified Claude interface for the bot handlers.

The facade wires together SDK runner, session manager, cost tracker,
and sanitizer into a single call surface.
"""

from pathlib import Path
from typing import Optional

import structlog

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
        max_cost_per_user: float = 5.0,
    ) -> None:
        self._runner = runner
        self._sessions = session_mgr
        self._costs = cost_tracker
        self._sanitizer = sanitizer
        self._max_cost = max_cost_per_user

    async def execute(
        self,
        user_id: int,
        prompt: str,
        access_level: str = "sandbox",
        username: Optional[str] = None,
        new_session: bool = False,
    ) -> ClaudeResponse:
        """Run a Claude prompt for a user, managing their session.

        Raises ClaudeError subclasses on failure.
        Sanitizes credential patterns from the response.
        """
        # Cost guard
        today_spend = self._costs.today_cost(user_id)
        if today_spend >= self._max_cost:
            from .exceptions import ClaudeAuthError
            raise ClaudeAuthError(
                f"Daily cost limit reached (${today_spend:.2f}/${self._max_cost:.2f})"
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
            )
        except ClaudeError:
            raise
        except Exception as exc:
            from .exceptions import ClaudeProcessError
            raise ClaudeProcessError(f"Unexpected error: {exc}") from exc

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
        self, user_id: int, access_level: str = "sandbox", username: Optional[str] = None
    ) -> UserSession:
        """Force a new session without running a prompt."""
        return self._sessions.reset(user_id, access_level, username)

    def current_session(self, user_id: int) -> Optional[UserSession]:
        return self._sessions.get(user_id)

    def cost_summary(self, user_id: int) -> dict:
        return self._costs.summary(user_id)
