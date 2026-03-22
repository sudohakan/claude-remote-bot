"""Typed event definitions for the event bus.

Every concrete event inherits from bus.Event and adds payload
fields as dataclass attributes.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .bus import Event


# ── Bot / User events ─────────────────────────────────────────────────────────

@dataclass
class UserMessageEvent(Event):
    """A text message received from a Telegram user."""

    user_id: int = 0
    chat_id: int = 0
    text: str = ""
    working_directory: Path = field(default_factory=lambda: Path("."))
    source: str = "telegram"


@dataclass
class AgentResponseEvent(Event):
    """A Claude agent has produced a response to deliver."""

    chat_id: int = 0
    text: str = ""
    parse_mode: Optional[str] = "HTML"
    reply_to_message_id: Optional[int] = None
    originating_event_id: Optional[str] = None
    source: str = "agent"


# ── Tunnel events ─────────────────────────────────────────────────────────────

@dataclass
class TunnelStateChangeEvent(Event):
    """ngrok tunnel changed state."""

    previous_state: str = "unknown"
    new_state: str = "unknown"
    tunnel_url: Optional[str] = None
    ssh_host: Optional[str] = None
    ssh_port: Optional[int] = None
    source: str = "tunnel"


@dataclass
class TunnelRetryExhaustedEvent(Event):
    """ngrok restart retries exhausted — manual intervention needed."""

    attempts: int = 0
    source: str = "tunnel"


# ── Monitor / alert events ────────────────────────────────────────────────────

@dataclass
class AlertEvent(Event):
    """A monitored metric crossed a threshold for the first time."""

    alert_type: str = ""       # e.g. "cpu_high", "ram_high", "disk_high", "ssh_brute_force"
    value: float = 0.0
    threshold: float = 0.0
    message: str = ""
    source: str = "monitor"


@dataclass
class AlertClearedEvent(Event):
    """A previously active alert has been resolved."""

    alert_type: str = ""
    source: str = "monitor"


# ── Scheduled / webhook events ────────────────────────────────────────────────

@dataclass
class ScheduledEvent(Event):
    """A cron-scheduled trigger."""

    job_id: str = ""
    job_name: str = ""
    prompt: str = ""
    working_directory: Path = field(default_factory=lambda: Path("."))
    target_chat_ids: List[int] = field(default_factory=list)
    skill_name: Optional[str] = None
    source: str = "scheduler"


@dataclass
class WebhookEvent(Event):
    """An inbound webhook payload."""

    provider: str = ""
    event_type_name: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    delivery_id: str = ""
    source: str = "webhook"
