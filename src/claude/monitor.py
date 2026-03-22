"""Cost tracking and usage statistics per user."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Dict, List

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class DailyUsage:
    """Aggregated usage for one user on one date."""

    date: str  # YYYY-MM-DD
    total_cost: float = 0.0
    request_count: int = 0
    turns: int = 0


@dataclass
class UserUsage:
    """Running totals + daily history for one user."""

    user_id: int
    lifetime_cost: float = 0.0
    lifetime_requests: int = 0
    daily: List[DailyUsage] = field(default_factory=list)

    def today_cost(self) -> float:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        for d in self.daily:
            if d.date == today:
                return d.total_cost
        return 0.0


class CostTracker:
    """Track per-user cost and request counts in memory.

    Designed to be lightweight.  For persistence, the bot should
    periodically flush totals to the storage layer.
    """

    def __init__(self) -> None:
        self._usage: Dict[int, UserUsage] = defaultdict(
            lambda: UserUsage(user_id=0)  # placeholder, overwritten on first record()
        )

    def record(self, user_id: int, cost: float, turns: int = 1) -> None:
        """Record a completed Claude request."""
        if user_id not in self._usage:
            self._usage[user_id] = UserUsage(user_id=user_id)

        usage = self._usage[user_id]
        usage.lifetime_cost += cost
        usage.lifetime_requests += 1

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        for daily in usage.daily:
            if daily.date == today:
                daily.total_cost += cost
                daily.request_count += 1
                daily.turns += turns
                return

        usage.daily.append(
            DailyUsage(date=today, total_cost=cost, request_count=1, turns=turns)
        )

    def today_cost(self, user_id: int) -> float:
        if user_id not in self._usage:
            return 0.0
        return self._usage[user_id].today_cost()

    def lifetime_cost(self, user_id: int) -> float:
        if user_id not in self._usage:
            return 0.0
        return self._usage[user_id].lifetime_cost

    def summary(self, user_id: int) -> Dict:
        if user_id not in self._usage:
            return {
                "user_id": user_id,
                "lifetime_cost": 0.0,
                "lifetime_requests": 0,
                "today_cost": 0.0,
            }
        u = self._usage[user_id]
        return {
            "user_id": user_id,
            "lifetime_cost": round(u.lifetime_cost, 4),
            "lifetime_requests": u.lifetime_requests,
            "today_cost": round(u.today_cost(), 4),
        }

    def all_summaries(self) -> List[Dict]:
        return [self.summary(uid) for uid in self._usage]
