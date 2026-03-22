"""Entry point.

Wires config, storage, security, claude bridge, event bus,
tunnel manager, system monitor, notification service, and bot together.
Graceful shutdown on SIGTERM/SIGINT.
"""

import asyncio
import logging
import signal
from pathlib import Path
from typing import Optional

import structlog

from src.bot.core import RemoteBot
from src.claude.facade import ClaudeFacade
from src.claude.monitor import CostTracker
from src.claude.sanitizer import CredentialSanitizer
from src.claude.sdk_integration import ClaudeSDKRunner
from src.claude.session import SessionManager
from src.config.settings import Settings
from src.events.bus import EventBus
from src.security.auth import AccessManager
from src.security.rate_limiter import RateLimiter
from src.storage.facade import StorageFacade

logger = structlog.get_logger(__name__)

_VERSION = "1.0.0"


def run() -> None:
    """Entry point for pyproject.toml script."""
    asyncio.run(_main())


async def _main() -> None:
    settings = Settings()

    logging.basicConfig(level=settings.log_level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        )
    )

    logger.info("claude-remote-bot starting", version=_VERSION)

    # ── Storage ───────────────────────────────────────────────────────────────
    storage = StorageFacade(settings.database_url)
    await storage.initialize()

    # ── Security ──────────────────────────────────────────────────────────────
    access = AccessManager(storage=storage, admin_id=settings.admin_telegram_id)
    await access.ensure_admin()

    limiter = RateLimiter(
        claude_per_min=settings.rate_limit_claude_per_min,
        commands_per_min=settings.rate_limit_commands_per_min,
        invites_per_hour=settings.rate_limit_invites_per_hour,
    )

    # ── Claude bridge ─────────────────────────────────────────────────────────
    runner = ClaudeSDKRunner(
        anthropic_api_key=settings.anthropic_api_key_str,
        claude_model=settings.claude_model,
        max_turns=settings.claude_max_turns,
        timeout_seconds=settings.claude_timeout_seconds,
        cli_path=settings.claude_cli_path,
    )
    session_mgr = SessionManager(timeout_hours=settings.session_timeout_hours)
    cost_tracker = CostTracker()
    sanitizer = CredentialSanitizer()

    claude_facade = ClaudeFacade(
        runner=runner,
        session_mgr=session_mgr,
        cost_tracker=cost_tracker,
        sanitizer=sanitizer,
        max_cost_per_user=settings.claude_max_cost_per_user,
    )

    # ── Event bus ─────────────────────────────────────────────────────────────
    event_bus = EventBus()
    await event_bus.start()

    # ── Tunnel manager (optional) ─────────────────────────────────────────────
    tunnel_manager = None
    if settings.enable_tunnel:
        # Lazy-import telegram Bot for notifier
        from telegram import Bot as TelegramBot

        from src.tunnel.manager import TunnelManager
        from src.tunnel.notifier import TunnelNotifier

        tg_bot_for_notifier = TelegramBot(token=settings.telegram_token_str)

        tunnel_manager = TunnelManager(
            event_bus=event_bus,
            ssh_port=settings.ssh_port,
            poll_interval=settings.tunnel_poll_interval_seconds,
            max_retries=settings.tunnel_max_retries,
            state_file=Path("data/tunnel.json"),
            ngrok_authtoken=settings.ngrok_authtoken_str,
        )
        tunnel_notifier = TunnelNotifier(
            event_bus=event_bus,
            bot=tg_bot_for_notifier,
            admin_chat_id=settings.admin_telegram_id,
        )
        tunnel_notifier.register()
        await tunnel_manager.start()
        logger.info("Tunnel manager started")

    # ── System monitor (optional) ─────────────────────────────────────────────
    monitor_collector = None
    alert_manager = None
    if settings.enable_monitor:
        from src.monitor.alerts import AlertManager
        from src.monitor.collector import MetricsCollector

        monitor_collector = MetricsCollector(
            history_file=Path("data/metrics.json"),
            tunnel_manager=tunnel_manager,
            storage=storage,
        )
        alert_manager = AlertManager(
            event_bus=event_bus,
            cpu_threshold=settings.alert_cpu_percent,
            ram_threshold=settings.alert_ram_percent,
            disk_threshold=settings.alert_disk_percent,
            ssh_failure_threshold=settings.alert_ssh_failures_per_min,
        )
        await monitor_collector.start(
            interval_seconds=settings.monitor_collect_interval_seconds
        )
        logger.info("System monitor started")

    # ── Notification service ──────────────────────────────────────────────────
    # (wired later once the Telegram Application is available)

    # ── Bot ───────────────────────────────────────────────────────────────────
    deps = {
        "storage": storage,
        "access_manager": access,
        "rate_limiter": limiter,
        "claude_facade": claude_facade,
        "event_bus": event_bus,
        "tunnel_manager": tunnel_manager,
        "monitor_collector": monitor_collector,
        "alert_manager": alert_manager,
        "version": _VERSION,
    }

    bot = RemoteBot(settings=settings, deps=deps)

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows

    # Run bot until shutdown signal
    bot_task = asyncio.create_task(bot.start())
    try:
        await asyncio.wait(
            [bot_task, asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        pass
    finally:
        await bot.stop()
        if tunnel_manager:
            await tunnel_manager.stop()
        if monitor_collector:
            await monitor_collector.stop()
        await event_bus.stop()
        await storage.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    run()
