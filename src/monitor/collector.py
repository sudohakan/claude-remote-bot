"""System metrics collector.

Collects:
- CPU usage (overall + per-core) via psutil
- RAM usage
- Disk usage
- Network I/O counters
- SSH sessions via `who`
- SSH auth failures via journalctl
- Tunnel status (via TunnelManager if provided)
- Claude session count (via StorageFacade if provided)

Stores a rolling 24-hour buffer in data/metrics.json.
"""

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

# Rolling history length (samples × 60s interval = hours coverage)
_MAX_HISTORY = 1440  # 24 hours at 1 sample/min


@dataclass
class NetworkStats:
    bytes_sent: int = 0
    bytes_recv: int = 0
    packets_sent: int = 0
    packets_recv: int = 0


@dataclass
class Metrics:
    """A single point-in-time metric sample."""

    timestamp: str = ""
    cpu_percent: float = 0.0
    cpu_per_core: List[float] = field(default_factory=list)
    ram_percent: float = 0.0
    ram_used_mb: float = 0.0
    ram_total_mb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    net: NetworkStats = field(default_factory=NetworkStats)
    ssh_sessions: int = 0
    claude_sessions: int = 0
    tunnel_status: str = "unknown"
    ssh_auth_failures_last_min: int = 0


class MetricsCollector:
    """Collect system metrics and maintain a rolling 24h buffer."""

    def __init__(
        self,
        history_file: Path = Path("data/metrics.json"),
        tunnel_manager: Optional[Any] = None,
        storage: Optional[Any] = None,
    ) -> None:
        self._history_file = history_file
        self._tunnel_manager = tunnel_manager
        self._storage = storage
        self._history: List[Dict[str, Any]] = []
        self._running: bool = False
        self._task: Optional[asyncio.Task[None]] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, interval_seconds: int = 60) -> None:
        """Start periodic collection."""
        if self._running:
            return
        self._running = True
        self._load_history()
        self._task = asyncio.create_task(self._collect_loop(interval_seconds))
        logger.info("Metrics collector started", interval=interval_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Metrics collector stopped")

    # ── Public API ────────────────────────────────────────────────────────────

    async def collect_once(self) -> Metrics:
        """Collect a fresh sample and append it to history."""
        m = await self._sample()
        self._history.append(asdict(m))
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]
        self._save_history()
        return m

    def get_latest(self) -> Optional[Metrics]:
        """Return the most recent sample, or None."""
        if not self._history:
            return None
        try:
            return Metrics(
                **{
                    k: v
                    for k, v in self._history[-1].items()
                    if k in Metrics.__dataclass_fields__
                }
            )
        except (TypeError, KeyError):
            return None

    def get_history(self, hours: int = 1) -> List[Dict[str, Any]]:
        """Return up to *hours* hours of history."""
        limit = hours * 60  # assuming 1 sample/min
        return self._history[-limit:]

    # ── Collection ────────────────────────────────────────────────────────────

    async def _collect_loop(self, interval: int) -> None:
        while self._running:
            try:
                await self.collect_once()
            except Exception as exc:
                logger.warning("Metrics collection error", error=str(exc))
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def _sample(self) -> Metrics:
        import psutil  # imported here so tests can mock easily

        # CPU
        cpu_total = psutil.cpu_percent(interval=None)
        cpu_cores = psutil.cpu_percent(interval=None, percpu=True)

        # RAM
        ram = psutil.virtual_memory()
        ram_pct = ram.percent
        ram_used = ram.used / 1024 / 1024
        ram_total = ram.total / 1024 / 1024

        # Disk (root partition)
        try:
            disk = psutil.disk_usage("/")
            disk_pct = disk.percent
            disk_used = disk.used / 1024 / 1024 / 1024
            disk_total = disk.total / 1024 / 1024 / 1024
        except PermissionError:
            disk_pct = disk_used = disk_total = 0.0

        # Network
        net_io = psutil.net_io_counters()
        net = NetworkStats(
            bytes_sent=net_io.bytes_sent,
            bytes_recv=net_io.bytes_recv,
            packets_sent=net_io.packets_sent,
            packets_recv=net_io.packets_recv,
        )

        # SSH sessions
        ssh_sessions = await self._count_ssh_sessions()

        # Claude sessions
        claude_sessions = await self._count_claude_sessions()

        # Tunnel status
        tunnel_status = "unknown"
        if self._tunnel_manager:
            state = self._tunnel_manager.get_state()
            tunnel_status = state.status

        # SSH auth failures
        ssh_failures = await self._count_ssh_failures()

        return Metrics(
            timestamp=datetime.now(UTC).isoformat(),
            cpu_percent=cpu_total,
            cpu_per_core=list(cpu_cores),
            ram_percent=ram_pct,
            ram_used_mb=round(ram_used, 1),
            ram_total_mb=round(ram_total, 1),
            disk_percent=disk_pct,
            disk_used_gb=round(disk_used, 2),
            disk_total_gb=round(disk_total, 2),
            net=net,
            ssh_sessions=ssh_sessions,
            claude_sessions=claude_sessions,
            tunnel_status=tunnel_status,
            ssh_auth_failures_last_min=ssh_failures,
        )

    async def _count_ssh_sessions(self) -> int:
        """Count active SSH sessions by parsing `who` output."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "who",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            lines = [ln for ln in stdout.decode().splitlines() if ln.strip()]
            return len(lines)
        except Exception:
            return 0

    async def _count_claude_sessions(self) -> int:
        if self._storage is None:
            return 0
        try:
            # StorageFacade.sessions may expose an active-count method
            count_fn = getattr(self._storage.sessions, "count_active", None)
            if callable(count_fn):
                return await count_fn()
        except Exception:
            pass
        return 0

    async def _count_ssh_failures(self) -> int:
        """Count SSH auth failures in the last minute via journalctl."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl",
                "-u",
                "ssh",
                "--since",
                "1 minute ago",
                "--no-pager",
                "-q",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode()
            return text.count("Failed password") + text.count("Invalid user")
        except Exception:
            return 0

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_history(self) -> None:
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_file, "w") as fh:
                json.dump(self._history, fh)
        except OSError as exc:
            logger.warning("Failed to save metrics", error=str(exc))

    def _load_history(self) -> None:
        try:
            if self._history_file.exists():
                with open(self._history_file) as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    self._history = data[-_MAX_HISTORY:]
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load metrics history", error=str(exc))
