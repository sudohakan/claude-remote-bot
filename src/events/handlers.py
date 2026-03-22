"""Event handler registry.

AgentHandler — translates ScheduledEvent / WebhookEvent into
Claude executions and re-publishes AgentResponseEvent.

Subclasses or additional handlers can be registered on the
event bus independently.
"""

from pathlib import Path
from typing import Any, Dict, List

import structlog

from .bus import Event, EventBus
from .types import AgentResponseEvent, ScheduledEvent, WebhookEvent

logger = structlog.get_logger(__name__)


class AgentHandler:
    """Bridge between scheduled/webhook events and Claude execution."""

    def __init__(
        self,
        event_bus: EventBus,
        claude_integration: Any,
        default_working_directory: Path,
        default_user_id: int = 0,
    ) -> None:
        self.bus = event_bus
        self.claude = claude_integration
        self.default_dir = default_working_directory
        self.default_user_id = default_user_id

    def register(self) -> None:
        """Subscribe to the event types this handler processes."""
        self.bus.subscribe(WebhookEvent, self.handle_webhook)
        self.bus.subscribe(ScheduledEvent, self.handle_scheduled)

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def handle_webhook(self, event: Event) -> None:
        if not isinstance(event, WebhookEvent):
            return
        logger.info(
            "Processing webhook through agent",
            provider=event.provider,
            event_type=event.event_type_name,
        )
        prompt = self._webhook_prompt(event)
        try:
            response = await self.claude.run_command(
                prompt=prompt,
                working_directory=self.default_dir,
                user_id=self.default_user_id,
            )
            if getattr(response, "content", None):
                await self.bus.publish(
                    AgentResponseEvent(
                        chat_id=0,
                        text=response.content,
                        originating_event_id=event.id,
                    )
                )
        except Exception:
            logger.exception(
                "Agent execution failed for webhook",
                provider=event.provider,
                event_id=event.id,
            )

    async def handle_scheduled(self, event: Event) -> None:
        if not isinstance(event, ScheduledEvent):
            return
        logger.info(
            "Processing scheduled event through agent",
            job_id=event.job_id,
            job_name=event.job_name,
        )
        prompt = event.prompt
        if event.skill_name:
            prompt = f"/{event.skill_name}\n\n{prompt}" if prompt else f"/{event.skill_name}"
        cwd = event.working_directory or self.default_dir
        try:
            response = await self.claude.run_command(
                prompt=prompt,
                working_directory=cwd,
                user_id=self.default_user_id,
            )
            if getattr(response, "content", None):
                targets = event.target_chat_ids or [0]
                for chat_id in targets:
                    await self.bus.publish(
                        AgentResponseEvent(
                            chat_id=chat_id,
                            text=response.content,
                            originating_event_id=event.id,
                        )
                    )
        except Exception:
            logger.exception(
                "Agent execution failed for scheduled event",
                job_id=event.job_id,
                event_id=event.id,
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _webhook_prompt(self, event: WebhookEvent) -> str:
        summary = self._summarize(event.payload)
        return (
            f"A {event.provider} webhook arrived.\n"
            f"Event type: {event.event_type_name}\n"
            f"Payload summary:\n{summary}\n\n"
            "Provide a concise analysis and highlight anything that needs attention."
        )

    def _summarize(self, data: Any, prefix: str = "", depth: int = 0, max_depth: int = 2) -> str:
        lines: List[str] = []
        self._flatten(data, lines, prefix, depth, max_depth)
        summary = "\n".join(lines)
        return summary[:2000] + ("\n... (truncated)" if len(summary) > 2000 else "")

    def _flatten(
        self,
        data: Any,
        lines: list,
        prefix: str,
        depth: int,
        max_depth: int,
    ) -> None:
        if depth >= max_depth:
            lines.append(f"{prefix}: ...")
            return
        if isinstance(data, dict):
            for k, v in data.items():
                fk = f"{prefix}.{k}" if prefix else k
                if isinstance(v, (dict, list)):
                    self._flatten(v, lines, fk, depth + 1, max_depth)
                else:
                    val = str(v)
                    lines.append(f"{fk}: {val[:200]}{'...' if len(val) > 200 else ''}")
        elif isinstance(data, list):
            lines.append(f"{prefix}: [{len(data)} items]")
            for i, item in enumerate(data[:3]):
                self._flatten(item, lines, f"{prefix}[{i}]", depth + 1, max_depth)
        else:
            lines.append(f"{prefix}: {data}")
