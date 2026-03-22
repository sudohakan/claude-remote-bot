"""Security event audit log.

Keeps an in-memory ring buffer (configurable max size) and emits
structured log entries for each event type.  Can be extended to
persist events to the database in a future task.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

import structlog

logger = structlog.get_logger(__name__)

RiskLevel = Literal["low", "medium", "high", "critical"]


@dataclass
class AuditEvent:
    """Single security event record."""

    user_id: int
    event_type: str
    success: bool
    details: Dict[str, Any]
    timestamp: datetime
    risk_level: RiskLevel = "low"
    session_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


class AuditLogger:
    """Collect and query security audit events."""

    def __init__(self, max_events: int = 5000) -> None:
        self._events: List[AuditEvent] = []
        self._max = max_events

    # ── Logging helpers ───────────────────────────────────────────────────────

    async def log_auth(
        self,
        user_id: int,
        success: bool,
        method: str,
        reason: Optional[str] = None,
    ) -> None:
        risk: RiskLevel = "low" if success else "medium"
        await self._store(
            AuditEvent(
                user_id=user_id,
                event_type="auth",
                success=success,
                details={"method": method, "reason": reason},
                timestamp=datetime.now(UTC),
                risk_level=risk,
            )
        )

    async def log_invite(
        self, user_id: int, action: str, token_prefix: str, success: bool
    ) -> None:
        await self._store(
            AuditEvent(
                user_id=user_id,
                event_type="invite",
                success=success,
                details={"action": action, "token_prefix": token_prefix},
                timestamp=datetime.now(UTC),
                risk_level="low",
            )
        )

    async def log_path_traversal(self, user_id: int, attempted_path: str) -> None:
        await self._store(
            AuditEvent(
                user_id=user_id,
                event_type="path_traversal",
                success=False,
                details={"attempted_path": attempted_path},
                timestamp=datetime.now(UTC),
                risk_level="high",
            )
        )

    async def log_rate_limit(
        self, user_id: int, category: str, wait_seconds: float
    ) -> None:
        await self._store(
            AuditEvent(
                user_id=user_id,
                event_type="rate_limit",
                success=False,
                details={"category": category, "wait_seconds": wait_seconds},
                timestamp=datetime.now(UTC),
                risk_level="low",
            )
        )

    async def log_security_event(
        self,
        user_id: int,
        event_type: str,
        details: Dict[str, Any],
        risk_level: RiskLevel = "medium",
        success: bool = False,
    ) -> None:
        await self._store(
            AuditEvent(
                user_id=user_id,
                event_type=event_type,
                success=success,
                details=details,
                timestamp=datetime.now(UTC),
                risk_level=risk_level,
            )
        )

    # ── Queries ───────────────────────────────────────────────────────────────

    def recent(
        self,
        hours: int = 24,
        user_id: Optional[int] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEvent]:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        result = [e for e in self._events if e.timestamp >= cutoff]
        if user_id is not None:
            result = [e for e in result if e.user_id == user_id]
        if event_type is not None:
            result = [e for e in result if e.event_type == event_type]
        result.sort(key=lambda e: e.timestamp, reverse=True)
        return result[:limit]

    def violations(
        self, hours: int = 24, user_id: Optional[int] = None
    ) -> List[AuditEvent]:
        high_risk = {"high", "critical"}
        events = self.recent(hours=hours, user_id=user_id)
        return [e for e in events if e.risk_level in high_risk]

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _store(self, event: AuditEvent) -> None:
        self._events.append(event)
        if len(self._events) > self._max:
            self._events = self._events[-self._max :]

        if event.risk_level in ("high", "critical"):
            logger.warning(
                "High-risk security event",
                event_type=event.event_type,
                user_id=event.user_id,
                risk_level=event.risk_level,
                details=event.details,
            )
