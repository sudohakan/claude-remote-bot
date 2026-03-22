"""Context-aware inline keyboard quick actions.

Analyses the active session and suggests relevant development
shortcuts as Telegram inline keyboard buttons.
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


@dataclass
class QuickAction:
    """A single quick-action shortcut."""

    id: str
    name: str
    description: str
    command: str
    icon: str
    category: str
    context_required: List[str]
    priority: int = 0


# Default action registry
_DEFAULT_ACTIONS: Dict[str, QuickAction] = {
    "test": QuickAction(
        id="test",
        name="Run Tests",
        description="Run project tests",
        command="test",
        icon="T",
        category="testing",
        context_required=["has_tests"],
        priority=10,
    ),
    "install": QuickAction(
        id="install",
        name="Install Deps",
        description="Install dependencies",
        command="install",
        icon="P",
        category="setup",
        context_required=["has_package_manager"],
        priority=9,
    ),
    "format": QuickAction(
        id="format",
        name="Format Code",
        description="Format with project formatter",
        command="format",
        icon="F",
        category="quality",
        context_required=["has_formatter"],
        priority=7,
    ),
    "lint": QuickAction(
        id="lint",
        name="Lint Code",
        description="Check code quality",
        command="lint",
        icon="L",
        category="quality",
        context_required=["has_linter"],
        priority=8,
    ),
    "security": QuickAction(
        id="security",
        name="Security Scan",
        description="Vulnerability scan",
        command="security",
        icon="S",
        category="security",
        context_required=["has_dependencies"],
        priority=6,
    ),
    "optimize": QuickAction(
        id="optimize",
        name="Optimize",
        description="Optimize code performance",
        command="optimize",
        icon="O",
        category="performance",
        context_required=["has_code"],
        priority=5,
    ),
    "document": QuickAction(
        id="document",
        name="Generate Docs",
        description="Generate documentation",
        command="document",
        icon="D",
        category="docs",
        context_required=["has_code"],
        priority=4,
    ),
    "refactor": QuickAction(
        id="refactor",
        name="Refactor",
        description="Suggest code improvements",
        command="refactor",
        icon="R",
        category="quality",
        context_required=["has_code"],
        priority=3,
    ),
}


class QuickActionManager:
    """Manage and surface context-aware quick actions."""

    def __init__(self) -> None:
        self.actions: Dict[str, QuickAction] = dict(_DEFAULT_ACTIONS)

    async def get_suggestions(self, session: Any, limit: int = 6) -> List[QuickAction]:
        """Return up to *limit* suggested actions based on session context."""
        try:
            context = await self._analyze_context(session)
            available = [
                a for a in self.actions.values() if self._is_available(a, context)
            ]
            available.sort(key=lambda x: x.priority, reverse=True)
            return available[:limit]
        except Exception as exc:
            logger.error("Error getting quick action suggestions: %s", exc)
            return []

    async def _analyze_context(self, session: Any) -> Dict[str, Any]:
        ctx: Dict[str, Any] = {
            "has_code": True,
            "has_tests": False,
            "has_package_manager": False,
            "has_formatter": False,
            "has_linter": False,
            "has_dependencies": False,
        }
        session_ctx = getattr(session, "context", None) or {}
        if isinstance(session_ctx, dict):
            for msg in session_ctx.get("recent_messages", []):
                content = (msg.get("content") or "").lower()
                if any(w in content for w in ["test", "pytest", "unittest"]):
                    ctx["has_tests"] = True
                if any(w in content for w in ["pip", "poetry", "npm", "yarn"]):
                    ctx["has_package_manager"] = True
                    ctx["has_dependencies"] = True
                if any(w in content for w in ["black", "prettier", "format"]):
                    ctx["has_formatter"] = True
                if any(w in content for w in ["flake8", "pylint", "eslint", "mypy"]):
                    ctx["has_linter"] = True
        return ctx

    def _is_available(self, action: QuickAction, context: Dict[str, Any]) -> bool:
        return all(context.get(k, False) for k in action.context_required)

    def create_inline_keyboard(
        self,
        actions: List[QuickAction],
        columns: int = 2,
    ) -> InlineKeyboardMarkup:
        """Build an inline keyboard from *actions*."""
        keyboard: List[List[InlineKeyboardButton]] = []
        row: List[InlineKeyboardButton] = []
        for i, action in enumerate(actions):
            btn = InlineKeyboardButton(
                text=f"[{action.icon}] {action.name}",
                callback_data=f"quick_action:{action.id}",
            )
            row.append(btn)
            if len(row) >= columns or i == len(actions) - 1:
                keyboard.append(row)
                row = []
        return InlineKeyboardMarkup(keyboard)

    async def execute_action(
        self,
        action_id: str,
        session: Any,
        callback: Optional[Callable[..., Any]] = None,
    ) -> str:
        """Return the command string for *action_id*."""
        action = self.actions.get(action_id)
        if not action:
            raise ValueError(f"Unknown quick action: {action_id}")
        logger.info(
            "Quick action requested: %s (session=%s)",
            action.name,
            getattr(session, "id", "?"),
        )
        return action.command
