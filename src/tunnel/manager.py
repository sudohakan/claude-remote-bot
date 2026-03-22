"""ngrok tunnel lifecycle manager.

Responsibilities:
- Start ngrok as a subprocess: ngrok tcp <ssh_port>
- Poll the local ngrok API (localhost:4040) every poll_interval seconds
- Parse tunnel URL into host + port
- Auto-restart on failure: max_retries attempts with exponential backoff
- Persist state to data/tunnel.json
- Publish TunnelStateChangeEvent / TunnelRetryExhaustedEvent on the bus

Anti-spam: only publish events on actual state CHANGES.
"""

import asyncio
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import structlog

from src.events.bus import EventBus
from src.events.types import TunnelRetryExhaustedEvent, TunnelStateChangeEvent

logger = structlog.get_logger(__name__)

# Exponential backoff delays (seconds) for restart attempts
_BACKOFF: list[int] = [5, 10, 20, 40, 80]

# ngrok local API base
_NGROK_API = "http://localhost:4040/api/tunnels"


@dataclass
class TunnelState:
    """Current snapshot of tunnel state."""

    status: str = "stopped"  # stopped | starting | up | down | error
    url: Optional[str] = None
    ssh_host: Optional[str] = None
    ssh_port: Optional[int] = None
    last_updated: Optional[str] = None
    retry_count: int = 0


class TunnelManager:
    """Manage the ngrok subprocess and health polling."""

    def __init__(
        self,
        event_bus: EventBus,
        ssh_port: int = 22,
        poll_interval: int = 30,
        max_retries: int = 5,
        state_file: Path = Path("data/tunnel.json"),
        ngrok_authtoken: Optional[str] = None,
    ) -> None:
        self._bus = event_bus
        self._ssh_port = ssh_port
        self._poll_interval = poll_interval
        self._max_retries = max_retries
        self._state_file = state_file
        self._authtoken = ngrok_authtoken

        self._state = TunnelState()
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._running: bool = False
        self._poll_task: Optional[asyncio.Task[None]] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start ngrok and the polling loop."""
        if self._running:
            return
        self._running = True
        self._load_state()
        await self._spawn_ngrok()
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Tunnel manager started", ssh_port=self._ssh_port)

    async def stop(self) -> None:
        """Stop polling and terminate ngrok."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._terminate_ngrok()
        logger.info("Tunnel manager stopped")

    # ── Public helpers ────────────────────────────────────────────────────────

    def get_state(self) -> TunnelState:
        return self._state

    def get_ssh_command(self) -> Optional[str]:
        """Return the SSH command for the current tunnel, or None."""
        if self._state.status == "up" and self._state.ssh_host and self._state.ssh_port:
            return f"ssh -p {self._state.ssh_port} user@{self._state.ssh_host}"
        return None

    # ── ngrok process ─────────────────────────────────────────────────────────

    async def _spawn_ngrok(self) -> None:
        try:
            cmd = [
                "ngrok",
                "tcp",
                str(self._ssh_port),
                "--log=stdout",
                "--log-level=warn",
            ]
            if self._authtoken:
                cmd += [f"--authtoken={self._authtoken}"]
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("ngrok spawned", pid=self._process.pid)
        except FileNotFoundError:
            logger.warning("ngrok binary not found — tunnel will not start")
            await self._set_state("error")

    def _terminate_ngrok(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    # ── Polling loop ──────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        # Wait a bit for ngrok to initialise
        await asyncio.sleep(3)
        while self._running:
            await self._check_health()
            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break

    async def _check_health(self) -> None:
        try:
            import aiohttp  # optional dep — tunnel feature requires it

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _NGROK_API, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        await self._handle_api_response(data)
                        return
        except ImportError:
            # aiohttp not installed — try via urllib
            await self._check_health_stdlib()
            return
        except Exception as exc:
            logger.warning("ngrok health check failed", error=str(exc))

        # Health check failed → tunnel may be down
        if self._state.status == "up":
            await self._handle_tunnel_down()

    async def _check_health_stdlib(self) -> None:
        """Fallback health check using stdlib urllib."""
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(_NGROK_API, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                await self._handle_api_response(data)
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            logger.warning("ngrok stdlib health check failed", error=str(exc))
            if self._state.status == "up":
                await self._handle_tunnel_down()

    async def _handle_api_response(self, data: dict) -> None:
        tunnels = data.get("tunnels", [])
        tcp_tunnel = next(
            (t for t in tunnels if t.get("proto") == "tcp"),
            None,
        )
        if tcp_tunnel:
            public_url: str = tcp_tunnel.get("public_url", "")
            host, port = self._parse_url(public_url)
            if self._state.status != "up" or self._state.url != public_url:
                await self._set_state("up", url=public_url, host=host, port=port)
                self._state.retry_count = 0
        else:
            if self._state.status == "up":
                await self._handle_tunnel_down()

    async def _handle_tunnel_down(self) -> None:
        await self._set_state("down")
        if self._state.retry_count >= self._max_retries:
            logger.error("Tunnel retries exhausted", attempts=self._state.retry_count)
            await self._bus.publish(
                TunnelRetryExhaustedEvent(attempts=self._state.retry_count)
            )
            return
        backoff = _BACKOFF[min(self._state.retry_count, len(_BACKOFF) - 1)]
        self._state.retry_count += 1
        logger.info(
            "Scheduling ngrok restart",
            attempt=self._state.retry_count,
            backoff_seconds=backoff,
        )
        self._terminate_ngrok()
        await asyncio.sleep(backoff)
        await self._spawn_ngrok()

    # ── State management ──────────────────────────────────────────────────────

    async def _set_state(
        self,
        new_status: str,
        url: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        old_status = self._state.status
        if old_status == new_status and self._state.url == url:
            return  # no change — do not emit event

        self._state.status = new_status
        self._state.url = url
        self._state.ssh_host = host
        self._state.ssh_port = port
        self._state.last_updated = datetime.now(UTC).isoformat()
        self._save_state()

        logger.info(
            "Tunnel state changed",
            old=old_status,
            new=new_status,
            url=url,
        )
        await self._bus.publish(
            TunnelStateChangeEvent(
                previous_state=old_status,
                new_state=new_status,
                tunnel_url=url,
                ssh_host=host,
                ssh_port=port,
            )
        )

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w") as fh:
                json.dump(asdict(self._state), fh, indent=2)
        except OSError as exc:
            logger.warning("Failed to save tunnel state", error=str(exc))

    def _load_state(self) -> None:
        try:
            if self._state_file.exists():
                with open(self._state_file) as fh:
                    data = json.load(fh)
                self._state = TunnelState(**data)
                # Reset ephemeral status on load
                self._state.status = "stopped"
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to load tunnel state", error=str(exc))

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_url(url: str) -> tuple[Optional[str], Optional[int]]:
        """Parse 'tcp://X.tcp.ngrok.io:12345' → (host, port)."""
        try:
            stripped = url.replace("tcp://", "")
            host, port_str = stripped.rsplit(":", 1)
            return host, int(port_str)
        except (ValueError, AttributeError):
            return None, None
