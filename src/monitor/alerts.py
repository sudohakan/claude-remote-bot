"""Anomaly detection and threshold-based alerting.

Rules:
- CPU > 90% for the last 5 minutes (sustained high)
- RAM > 85%
- Disk > 90%
- SSH brute force: >= 5 failures/min
- Tunnel instability: >= 3 state changes in 10 minutes

Anti-spam:
- Alert fires ONLY when threshold is FIRST crossed (leading edge).
- While the condition is sustained, no further events are emitted.
- When the condition clears, the alert is reset so it can fire again next time.
"""

import time
from typing import Any, Dict, Optional, Set

import structlog

from src.events.bus import EventBus
from src.events.types import AlertClearedEvent, AlertEvent

from .collector import Metrics

logger = structlog.get_logger(__name__)


class AlertManager:
    """Detect threshold crossings and emit one-shot alert events."""

    def __init__(
        self,
        event_bus: EventBus,
        cpu_threshold: float = 90.0,
        ram_threshold: float = 85.0,
        disk_threshold: float = 90.0,
        ssh_failure_threshold: int = 5,
        tunnel_drop_threshold: int = 3,
    ) -> None:
        self._bus = event_bus
        self._cpu_threshold = cpu_threshold
        self._ram_threshold = ram_threshold
        self._disk_threshold = disk_threshold
        self._ssh_failure_threshold = ssh_failure_threshold
        self._tunnel_drop_threshold = tunnel_drop_threshold

        # Currently active alerts (alert_type → True)
        self._active: Set[str] = set()

        # Sustained CPU tracking: list of (timestamp, cpu_percent)
        self._cpu_samples: list[tuple[float, float]] = []

        # Tunnel drop tracking: list of drop timestamps
        self._tunnel_drops: list[float] = []

    # ── Public API ────────────────────────────────────────────────────────────

    async def evaluate(self, metrics: Metrics) -> None:
        """Evaluate *metrics* against all thresholds."""
        await self._check_cpu(metrics.cpu_percent)
        await self._check_ram(metrics.ram_percent)
        await self._check_disk(metrics.disk_percent)
        await self._check_ssh_failures(metrics.ssh_auth_failures_last_min)

    def record_tunnel_drop(self) -> None:
        """Call this each time the tunnel drops."""
        self._tunnel_drops.append(time.monotonic())

    async def check_tunnel_instability(self) -> None:
        """Check if there have been too many drops in the last 10 minutes."""
        cutoff = time.monotonic() - 600
        self._tunnel_drops = [t for t in self._tunnel_drops if t > cutoff]
        count = len(self._tunnel_drops)
        alert_type = "tunnel_instability"
        if count >= self._tunnel_drop_threshold:
            await self._fire(
                alert_type,
                value=float(count),
                threshold=float(self._tunnel_drop_threshold),
                message=f"Tunnel dropped {count} times in the last 10 minutes.",
            )
        else:
            await self._clear(alert_type)

    # ── Threshold checks ──────────────────────────────────────────────────────

    async def _check_cpu(self, cpu: float) -> None:
        now = time.monotonic()
        self._cpu_samples.append((now, cpu))
        # Keep only last 5 minutes
        cutoff = now - 300
        self._cpu_samples = [(t, v) for t, v in self._cpu_samples if t > cutoff]

        # Alert only if ALL samples in the window exceed the threshold
        if len(self._cpu_samples) >= 2:
            all_high = all(v > self._cpu_threshold for _, v in self._cpu_samples)
        else:
            all_high = cpu > self._cpu_threshold

        if all_high:
            avg = sum(v for _, v in self._cpu_samples) / len(self._cpu_samples)
            await self._fire(
                "cpu_high",
                value=avg,
                threshold=self._cpu_threshold,
                message=f"CPU has been above {self._cpu_threshold:.0f}% for 5+ minutes (avg: {avg:.1f}%).",
            )
        else:
            await self._clear("cpu_high")

    async def _check_ram(self, ram: float) -> None:
        if ram > self._ram_threshold:
            await self._fire(
                "ram_high",
                value=ram,
                threshold=self._ram_threshold,
                message=f"RAM usage is {ram:.1f}% (threshold: {self._ram_threshold:.0f}%).",
            )
        else:
            await self._clear("ram_high")

    async def _check_disk(self, disk: float) -> None:
        if disk > self._disk_threshold:
            await self._fire(
                "disk_high",
                value=disk,
                threshold=self._disk_threshold,
                message=f"Disk usage is {disk:.1f}% (threshold: {self._disk_threshold:.0f}%).",
            )
        else:
            await self._clear("disk_high")

    async def _check_ssh_failures(self, failures: int) -> None:
        if failures >= self._ssh_failure_threshold:
            await self._fire(
                "ssh_brute_force",
                value=float(failures),
                threshold=float(self._ssh_failure_threshold),
                message=f"SSH brute-force detected: {failures} failures in the last minute.",
            )
        else:
            await self._clear("ssh_brute_force")

    # ── Event helpers ─────────────────────────────────────────────────────────

    async def _fire(
        self,
        alert_type: str,
        value: float,
        threshold: float,
        message: str,
    ) -> None:
        """Emit an AlertEvent only when first crossing the threshold."""
        if alert_type in self._active:
            return  # already firing — suppress
        self._active.add(alert_type)
        logger.warning("Alert triggered", alert_type=alert_type, value=value)
        await self._bus.publish(
            AlertEvent(
                alert_type=alert_type,
                value=value,
                threshold=threshold,
                message=message,
            )
        )

    async def _clear(self, alert_type: str) -> None:
        """Emit an AlertClearedEvent and reset the active flag."""
        if alert_type not in self._active:
            return
        self._active.discard(alert_type)
        logger.info("Alert cleared", alert_type=alert_type)
        await self._bus.publish(AlertClearedEvent(alert_type=alert_type))

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def active_alerts(self) -> Set[str]:
        return frozenset(self._active)
