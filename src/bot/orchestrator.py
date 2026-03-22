"""Message orchestrator — registers all handlers with the Application."""

from typing import Any, Dict

import structlog
from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .handlers.callback import handle_callback
from .handlers.command import (
    cmd_about,
    cmd_alerts,
    cmd_cwd,
    cmd_demote,
    cmd_help,
    cmd_history,
    cmd_invite,
    cmd_new,
    cmd_ping,
    cmd_promote,
    cmd_revoke,
    cmd_sessions,
    cmd_ssh,
    cmd_start,
    cmd_stats,
    cmd_status,
    cmd_users,
)
from .handlers.message import handle_message

logger = structlog.get_logger(__name__)

_BOT_COMMANDS = [
    BotCommand("start", "Register or log in"),
    BotCommand("help", "Show available commands"),
    BotCommand("about", "Bot info and architecture"),
    BotCommand("ping", "Check bot is alive"),
    BotCommand("new", "Start new Claude session"),
    BotCommand("status", "System status"),
    BotCommand("ssh", "SSH tunnel info"),
    BotCommand("history", "Recent command history"),
    BotCommand("cwd", "Current working directory"),
]


class BotOrchestrator:
    """Register handlers and inject dependencies."""

    def __init__(self, deps: Dict[str, Any]) -> None:
        self._deps = deps

    def register(self, app: Application) -> None:
        """Add all handlers to the Application."""
        # Inject deps into bot_data
        for key, val in self._deps.items():
            app.bot_data[key] = val

        # Command handlers
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("help", cmd_help))
        app.add_handler(CommandHandler("about", cmd_about))
        app.add_handler(CommandHandler("ping", cmd_ping))
        app.add_handler(CommandHandler("new", cmd_new))
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CommandHandler("ssh", cmd_ssh))
        app.add_handler(CommandHandler("history", cmd_history))
        app.add_handler(CommandHandler("cwd", cmd_cwd))

        # Admin commands
        app.add_handler(CommandHandler("invite", cmd_invite))
        app.add_handler(CommandHandler("users", cmd_users))
        app.add_handler(CommandHandler("promote", cmd_promote))
        app.add_handler(CommandHandler("demote", cmd_demote))
        app.add_handler(CommandHandler("revoke", cmd_revoke))
        app.add_handler(CommandHandler("stats", cmd_stats))
        app.add_handler(CommandHandler("sessions", cmd_sessions))
        app.add_handler(CommandHandler("alerts", cmd_alerts))

        # Inline keyboards
        app.add_handler(CallbackQueryHandler(handle_callback))

        # Text messages → Claude (must be last)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("Handlers registered")

    async def set_bot_commands(self, app: Application) -> None:
        await app.bot.set_my_commands(_BOT_COMMANDS)
        logger.info("Bot commands menu set")
