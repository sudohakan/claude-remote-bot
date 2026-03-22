"""Token bucket rate limiter.

Separate buckets per user per limit category.
Categories: claude (20/min), commands (5/min), invites (3/hour).
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Dict, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Bucket:
    """Token bucket state for one (user, category) pair."""

    capacity: float
    tokens: float
    refill_rate: float  # tokens per second
    last_refill: datetime = field(default_factory=lambda: datetime.now(UTC))

    def consume(self, amount: float = 1.0) -> bool:
        """Attempt to consume `amount` tokens. Returns True on success."""
        self._refill()
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    def wait_seconds(self, amount: float = 1.0) -> float:
        """How many seconds until `amount` tokens are available."""
        self._refill()
        if self.tokens >= amount:
            return 0.0
        return (amount - self.tokens) / self.refill_rate

    def _refill(self) -> None:
        now = datetime.now(UTC)
        elapsed = (now - self.last_refill).total_seconds()
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now


def _per_min_rate(n: int) -> float:
    """Convert n requests/minute to tokens/second refill rate."""
    return n / 60.0


def _per_hour_rate(n: int) -> float:
    """Convert n requests/hour to tokens/second refill rate."""
    return n / 3600.0


class RateLimiter:
    """Per-user, per-category token bucket limiter.

    Usage:
        allowed, wait = await limiter.check("claude", user_id)
        if not allowed:
            await message.reply(f"Rate limited — wait {wait:.0f}s")
    """

    # Default limits (overridable via constructor)
    DEFAULTS = {
        "claude": {"per_min": 20, "burst": 5},
        "commands": {"per_min": 5, "burst": 3},
        "invites": {"per_hour": 3, "burst": 3},
    }

    def __init__(
        self,
        claude_per_min: int = 20,
        commands_per_min: int = 5,
        invites_per_hour: int = 3,
    ) -> None:
        self._config = {
            "claude": {
                "capacity": float(min(claude_per_min, 20)),
                "refill_rate": _per_min_rate(claude_per_min),
            },
            "commands": {
                "capacity": float(min(commands_per_min, 10)),
                "refill_rate": _per_min_rate(commands_per_min),
            },
            "invites": {
                "capacity": float(invites_per_hour),
                "refill_rate": _per_hour_rate(invites_per_hour),
            },
        }
        # buckets[category][user_id]
        self._buckets: Dict[str, Dict[int, Bucket]] = defaultdict(dict)
        self._locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def check(
        self, category: str, user_id: int, amount: float = 1.0
    ) -> Tuple[bool, float]:
        """Check and consume rate limit.

        Returns:
            (allowed, wait_seconds)  — wait_seconds == 0 when allowed
        """
        async with self._locks[user_id]:
            bucket = self._get_bucket(category, user_id)
            if bucket.consume(amount):
                return True, 0.0
            wait = bucket.wait_seconds(amount)
            logger.warning(
                "Rate limit exceeded",
                user_id=user_id,
                category=category,
                wait_seconds=wait,
            )
            return False, wait

    async def reset(self, user_id: int, category: Optional[str] = None) -> None:
        """Reset buckets for a user (admin function)."""
        async with self._locks[user_id]:
            if category:
                self._buckets[category].pop(user_id, None)
            else:
                for cat in self._buckets:
                    self._buckets[cat].pop(user_id, None)

    def _get_bucket(self, category: str, user_id: int) -> Bucket:
        if user_id not in self._buckets[category]:
            cfg = self._config.get(category, self._config["commands"])
            self._buckets[category][user_id] = Bucket(
                capacity=cfg["capacity"],
                tokens=cfg["capacity"],
                refill_rate=cfg["refill_rate"],
            )
        return self._buckets[category][user_id]
