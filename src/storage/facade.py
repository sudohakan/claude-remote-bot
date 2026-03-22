"""Unified storage interface.

Callers import StorageFacade and use it directly instead of juggling
individual repositories.  The facade owns the DatabaseManager lifecycle.
"""

import structlog

from .database import DatabaseManager
from .repositories import (
    CommandLogRepository,
    InviteRepository,
    SessionRepository,
    UserRepository,
)

logger = structlog.get_logger(__name__)


class StorageFacade:
    """Single entry point for all persistence operations."""

    def __init__(self, database_url: str) -> None:
        self._db = DatabaseManager(database_url)
        self.users = UserRepository(self._db)
        self.invites = InviteRepository(self._db)
        self.sessions = SessionRepository(self._db)
        self.commands = CommandLogRepository(self._db)

    async def initialize(self) -> None:
        """Initialize database and run migrations."""
        await self._db.initialize()

    async def close(self) -> None:
        """Close all connections."""
        await self._db.close()

    async def health_check(self) -> bool:
        """Delegate to database health check."""
        return await self._db.health_check()
