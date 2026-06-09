"""Message orchestrator — registers all handlers with the Application."""

from typing import Any, Dict

import structlog
from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
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
    cmd_epic,
    cmd_help,
    cmd_history,
    cmd_invite,
    cmd_limit,
    cmd_new,
    cmd_ping,
    cmd_promote,
    cmd_remote,
    cmd_revoke,
    cmd_sessions,
    cmd_ssh,
    cmd_start,
    cmd_stats,
    cmd_status,
    cmd_users,
)
from .handlers.document import handle_document
from .handlers.media import handle_media
from .handlers.message import handle_message
from .handlers.photo import handle_photo

logger = structlog.get_logger(__name__)

# Commands shown to ALL users (including non-admin).
# Telegram applies BotCommandScopeDefault, so this is what unauthenticated
# / non-admin chats see in the autocomplete menu.
_USER_BOT_COMMANDS = [
    BotCommand("start", "Register or log in"),
    BotCommand("help", "Show available commands"),
    BotCommand("about", "Bot info and architecture"),
    BotCommand("ping", "Check bot is alive"),
    BotCommand("new", "Start new Claude session"),
    BotCommand("status", "System status"),
    BotCommand("ssh", "SSH tunnel info"),
    BotCommand("history", "Recent command history"),
    BotCommand("cwd", "Current working directory"),
    BotCommand("remote", "Active remote control sessions"),
]

# Admin-only commands — appended to the admin's per-chat scope so they
# show up in autocomplete ONLY for the admin chat. Non-admins still get
# the "Admin only." reply at handler level if they somehow type one.
_ADMIN_EXTRA_COMMANDS = [
    BotCommand("invite", "Admin: generate invite token"),
    BotCommand("users", "Admin: list active users"),
    BotCommand("promote", "Admin: promote user to admin"),
    BotCommand("demote", "Admin: demote user (default → user)"),
    BotCommand("revoke", "Admin: revoke an unused invite"),
    BotCommand("limit", "Admin: per-user daily cost cap"),
    BotCommand("stats", "Admin: 24h usage statistics"),
    BotCommand("sessions", "Admin: active Claude sessions"),
    BotCommand("alerts", "Admin: toggle hourly reports"),
    BotCommand("epic", "Admin: fresh Epic free-game claim link"),
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
        app.add_handler(CommandHandler("remote", cmd_remote))
        app.add_handler(CommandHandler("epic", cmd_epic))
        app.add_handler(CommandHandler("alerts", cmd_alerts))
        app.add_handler(CommandHandler("limit", cmd_limit))

        # Inline keyboards
        app.add_handler(CallbackQueryHandler(handle_callback))

        # Photo messages → Claude vision (download to temp file, inspect by path)
        app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

        # Document/file uploads → Claude (download to temp file, inspect by path)
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

        # Voice/audio/video → Claude (download, transcribe via transcribeLocal,
        # inspect by path). Previously these media types were dropped silently.
        app.add_handler(
            MessageHandler(
                filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE,
                handle_media,
            )
        )

        # Unknown slash commands → Claude passthrough (preserves leading "/")
        # Known commands above already consume their updates; only unmatched
        # /foo reaches here and is forwarded verbatim so Claude can dispatch
        # native slash commands (/dreamy, /loop, /team, /finekra-task, ...).
        app.add_handler(MessageHandler(filters.COMMAND, handle_message))

        # Text messages → Claude (must be last)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("Handlers registered")

    async def set_bot_commands(self, app: Application) -> None:
        """Publish role-scoped command menus.

        - Default scope → user commands only (every chat).
        - Admin chat scope → user + admin commands (autocomplete is
          role-aware, so non-admins do not see admin-only entries).
        - Additional DB-promoted admins also receive the admin menu in
          their own chat scope.
        """
        # Default (every user)
        await app.bot.set_my_commands(
            _USER_BOT_COMMANDS, scope=BotCommandScopeDefault()
        )

        # Admin scopes: bootstrap admin + any DB role='admin' user
        admin_ids = set()
        settings = self._deps.get("settings")
        if settings is not None:
            admin_ids.add(int(settings.admin_telegram_id))

        storage = self._deps.get("storage")
        if storage is not None:
            try:
                users = await storage.users.list_active()
                admin_ids.update(u.user_id for u in users if u.role == "admin")
            except Exception as exc:  # storage may not be ready
                logger.warning("Could not enumerate DB admins", error=str(exc))

        admin_commands = _USER_BOT_COMMANDS + _ADMIN_EXTRA_COMMANDS
        for chat_id in admin_ids:
            try:
                await app.bot.set_my_commands(
                    admin_commands, scope=BotCommandScopeChat(chat_id=chat_id)
                )
            except Exception as exc:
                logger.warning(
                    "Could not set admin scope commands",
                    chat_id=chat_id,
                    error=str(exc),
                )

        logger.info(
            "Bot commands menu set",
            user_count=len(_USER_BOT_COMMANDS),
            admin_chats=len(admin_ids),
        )
