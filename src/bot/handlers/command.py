"""Command handlers.

Public commands (all authenticated users):
  /start [token]  — register via invite or confirm identity
  /help           — show available commands filtered by role
  /about          — bot architecture info
  /ping           — liveness check
  /new            — reset Claude session
  /status         — system + tunnel + session status
  /ssh            — SSH connection info (tunnel URL)
  /history        — last N messages from command log
  /cwd            — show current working directory

Admin-only commands:
  /invite         — generate invite token
  /promote <id>   — promote user to admin
  /demote <id>    — demote user to user/viewer
  /revoke <token> — revoke an unused invite
  /users          — list active users
  /broadcast <msg>— send message to all users (future)
  /sessions       — list active Claude sessions
  /stats          — 24h usage stats
  /alerts [on|off]— toggle hourly reports
"""

import os

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.utils import messages as M
from src.bot.utils.constants import (
    MSG_WELCOME_NEW_USER,
    MSG_WELCOME_UNKNOWN,
)
from src.bot.utils.formatting import escape_html

logger = structlog.get_logger(__name__)


def _access_mgr(ctx: ContextTypes.DEFAULT_TYPE):
    return ctx.bot_data.get("access_manager")


def _storage(ctx: ContextTypes.DEFAULT_TYPE):
    return ctx.bot_data.get("storage")


def _settings(ctx: ContextTypes.DEFAULT_TYPE):
    return ctx.bot_data.get("settings")


# ── /start ────────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Register or greet a user."""
    user = update.effective_user
    if user is None:
        return

    args = ctx.args or []
    access = _access_mgr(ctx)
    settings = _settings(ctx)

    # Admin always welcome
    if settings and user.id == settings.admin_telegram_id:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_ADMIN, "Welcome back"),
                f"Command list: {M.code('/help')}",
            ),
            parse_mode="HTML",
        )
        return

    # If no token given, check if already registered
    if not args:
        if access and await access.is_authorised(user.id):
            await update.message.reply_text(
                M.compose(
                    M.header(M.ICON_USER, f"Welcome back, {user.first_name}"),
                    f"Type {M.code('/help')} for the command list.",
                ),
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(MSG_WELCOME_UNKNOWN, parse_mode="HTML")
        return

    # Token redemption
    token = args[0].strip()
    if access:
        ok = await access.redeem_invite(
            token=token,
            user_id=user.id,
            username=user.username,
        )
        if ok:
            await update.message.reply_text(MSG_WELCOME_NEW_USER, parse_mode="HTML")
        else:
            await update.message.reply_text(
                M.compose(
                    M.header(M.ICON_ERROR, "Invalid invite"),
                    "Token is expired, already used, or wrong.",
                    M.footer_hint("Ask the admin for a new one."),
                ),
                parse_mode="HTML",
            )
    else:
        await update.message.reply_text(
            M.msg_unavailable("Authentication system"), parse_mode="HTML"
        )


# ── /help ─────────────────────────────────────────────────────────────────────


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show commands filtered by the caller's role."""
    user = update.effective_user
    if user is None:
        return

    settings = _settings(ctx)
    access = _access_mgr(ctx)

    is_admin = settings and user.id == settings.admin_telegram_id
    if access and not is_admin:
        is_admin = await access.is_admin(user.id)

    user_lines = [
        f"{M.code('/start')} — Register with an invite",
        f"{M.code('/help')} — Command list",
        f"{M.code('/about')} — Bot info",
        f"{M.code('/ping')} — Liveness check",
        f"{M.code('/new')} — Start a new Claude session",
        f"{M.code('/status')} — System and tunnel status",
        f"{M.code('/ssh')} — SSH connection info",
        f"{M.code('/history')} — Recent commands",
        f"{M.code('/cwd')} — Working directory",
    ]
    user_block = M.section("General commands", "\n".join(user_lines))

    blocks = [M.header(M.ICON_BOT, "Commands"), user_block]

    if is_admin:
        admin_lines = [
            f"{M.code('/invite')} — Generate an invite",
            f"{M.code('/promote <id> <role>')} — Change role up",
            f"{M.code('/demote <id>')} — Change role down",
            f"{M.code('/revoke <token>')} — Cancel an invite",
            f"{M.code('/users')} — List users",
            f"{M.code('/sessions')} — Active Claude sessions",
            f"{M.code('/stats')} — 24h usage",
            f"{M.code('/alerts [on|off]')} — Hourly reports",
            f"{M.code('/limit')} — Per-user daily cost caps",
        ]
        blocks.append(M.section("Admin commands", "\n".join(admin_lines)))

    await update.message.reply_text(M.compose(*blocks), parse_mode="HTML")


# ── /about ────────────────────────────────────────────────────────────────────


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    features = []
    if settings:
        if settings.enable_tunnel:
            features.append("ngrok tunnel manager")
        if settings.enable_monitor:
            features.append("system monitor")
        if settings.enable_file_uploads:
            features.append("file uploads")
        if settings.enable_git_integration:
            features.append("git integration")

    feature_str = ", ".join(features) if features else "base mode"

    arch = (
        "• Python 3.12 + python-telegram-bot 22\n"
        "• anthropic SDK + claude-agent-sdk\n"
        "• SQLite WAL (invite auth, sessions, audit)\n"
        "• Token-bucket rate limiter\n"
        "• Path-traversal validator"
    )
    await update.message.reply_text(
        M.compose(
            M.header(M.ICON_BOT, "Claude Remote Bot"),
            M.section("Architecture", arch),
            M.kv("Active features", feature_str),
        ),
        parse_mode="HTML",
    )


# ── /ping ─────────────────────────────────────────────────────────────────────


async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(M.msg_pong(), parse_mode="HTML")


# ── /new ─────────────────────────────────────────────────────────────────────


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset Claude session for the calling user."""
    user = update.effective_user
    if user is None:
        return

    claude = ctx.bot_data.get("claude_facade")
    access = _access_mgr(ctx)

    access_level = "sandbox"
    if access:
        level = await access.get_access_level(user.id)
        if level:
            access_level = level

    if claude:
        claude.new_session(
            user_id=user.id,
            access_level=access_level,
            username=user.username,
        )
        await update.message.reply_text(M.msg_session_started(), parse_mode="HTML")
    else:
        await update.message.reply_text(
            M.msg_unavailable("Claude bridge"), parse_mode="HTML"
        )


# ── /status ───────────────────────────────────────────────────────────────────


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show basic system status."""
    storage = _storage(ctx)
    claude = ctx.bot_data.get("claude_facade")
    user = update.effective_user

    rows: list[tuple[str, str]] = []
    code_keys: set[str] = set()

    if storage:
        db_ok = await storage.health_check()
        rows.append(("Database", "🟢 Online" if db_ok else "🔴 Error"))

    session_block = None
    if claude and user:
        session = claude.current_session(user.id)
        if session:
            cost = claude.cost_summary(user.id)["today_cost"]
            session_rows = [
                ("Working dir", str(session.working_dir)),
                ("Turns", str(session.total_turns)),
                ("Today's cost", f"${cost:.4f}"),
            ]
            session_block = M.section(
                "Active Claude session",
                M.kv_block(session_rows, code_keys={"Working dir"}),
            )
        else:
            session_block = M.footer_hint(
                f"No active session. Start one with {M.code('/new')}."
            )

    blocks = [M.header(M.ICON_STATS, "Bot status")]
    if rows:
        blocks.append(M.kv_block(rows, code_keys=code_keys))
    if session_block:
        blocks.append(session_block)

    await update.message.reply_text(M.compose(*blocks), parse_mode="HTML")


# ── /ssh ─────────────────────────────────────────────────────────────────────


async def _ssh_from_ngrok_api() -> tuple[str | None, int | None]:
    """Best-effort fetch of (host, port) from the local ngrok agent.

    Used when the bot's tunnel_manager is disabled (ngrok is run as a
    separate PM2 service so it survives bot restarts and keeps the
    user's SSH session alive).
    """
    import asyncio
    import json
    from urllib.request import urlopen

    def _read() -> dict:
        with urlopen("http://localhost:4040/api/tunnels", timeout=2) as resp:
            return json.loads(resp.read())

    try:
        data = await asyncio.to_thread(_read)
    except Exception:
        return None, None

    for tun in data.get("tunnels", []):
        url = tun.get("public_url", "")
        # ngrok TCP tunnels: tcp://0.tcp.eu.ngrok.io:25859
        if url.startswith("tcp://"):
            host_port = url[len("tcp://") :]
            if ":" in host_port:
                host, port = host_port.rsplit(":", 1)
                try:
                    return host, int(port)
                except ValueError:
                    continue
    return None, None


async def cmd_ssh(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show SSH tunnel status and connection info.

    Two paths:
    1. Bot-managed tunnel (legacy: ENABLE_TUNNEL=true) → tunnel_manager state.
    2. External ngrok (recommended: PM2-managed) → query localhost:4040 API
       so /ssh keeps working without restarting whenever the bot restarts.
    """
    tunnel_mgr = ctx.bot_data.get("tunnel_manager")

    host: str | None = None
    port: int | None = None

    if tunnel_mgr is not None:
        state = tunnel_mgr.get_state()
        if state.status == "up":
            host = state.ssh_host
            port = state.ssh_port

    if host is None or port is None:
        host, port = await _ssh_from_ngrok_api()

    if host and port:
        await update.message.reply_text(
            M.msg_tunnel_up(url=f"tcp://{host}:{port}", host=host, port=port),
            parse_mode="HTML",
        )
        return

    if tunnel_mgr is not None:
        state = tunnel_mgr.get_state()
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_TUNNEL, "SSH tunnel"),
                M.kv("Status", state.status.upper()),
            ),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_ERROR, "SSH tunnel not found"),
                f"Is ngrok running? Check: {M.code('pm2 status ngrok-ssh-tunnel')}",
            ),
            parse_mode="HTML",
        )


# ── /history ──────────────────────────────────────────────────────────────────


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    storage = _storage(ctx)
    if storage is None:
        await update.message.reply_text(
            M.msg_unavailable("Storage"), parse_mode="HTML"
        )
        return

    entries = await storage.commands.recent_for_user(user.id, limit=10)
    if not entries:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_HISTORY, "Command history"),
                "No entries yet.",
            ),
            parse_mode="HTML",
        )
        return

    body_lines = []
    for entry in entries:
        ts = entry.logged_at.strftime("%H:%M") if entry.logged_at else "--:--"
        icon = M.ICON_SUCCESS if entry.result == "ok" else M.ICON_ERROR
        body_lines.append(f"{icon} {M.code(ts)} {escape_html(entry.command)}")

    await update.message.reply_text(
        M.compose(
            M.header(M.ICON_HISTORY, "Last 10 commands"),
            "\n".join(body_lines),
        ),
        parse_mode="HTML",
    )


# ── /cwd ──────────────────────────────────────────────────────────────────────


async def cmd_cwd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    claude = ctx.bot_data.get("claude_facade")
    if claude is None:
        await update.message.reply_text(
            M.msg_unavailable("Claude bridge"), parse_mode="HTML"
        )
        return

    session = claude.current_session(user.id)
    if session:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_FOLDER, "Working directory"),
                M.code(str(session.working_dir)),
            ),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_INFO, "No active session"),
                f"Start one with {M.code('/new')}.",
            ),
            parse_mode="HTML",
        )


# ── Admin: /invite ────────────────────────────────────────────────────────────


async def cmd_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)

    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    if access is None:
        await update.message.reply_text(
            M.msg_unavailable("Access manager"), parse_mode="HTML"
        )
        return

    # Rate check: invites_per_hour
    limiter = ctx.bot_data.get("rate_limiter")
    if limiter:
        allowed, wait = await limiter.check("invites", user.id)
        if not allowed:
            await update.message.reply_text(
                M.msg_rate_limited(wait, context="Invite"), parse_mode="HTML"
            )
            return

    invite = await access.create_invite(created_by=user.id, ttl_hours=24)
    await update.message.reply_text(
        M.msg_invite_token(invite.token, ttl_hours=24), parse_mode="HTML"
    )


# ── Admin: /users ─────────────────────────────────────────────────────────────


async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)
    storage = _storage(ctx)

    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    if storage is None:
        await update.message.reply_text(
            M.msg_unavailable("Storage"), parse_mode="HTML"
        )
        return

    users = await storage.users.list_active()
    if not users:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_USER, "Active users"),
                "No users yet.",
            ),
            parse_mode="HTML",
        )
        return

    body_lines = []
    for u in users[:20]:
        name = f"@{u.username}" if u.username else f"id:{u.user_id}"
        body_lines.append(
            f"• {escape_html(name)} — {M.code(str(u.user_id))} — "
            f"{escape_html(u.role)} / {escape_html(u.access_level)}"
        )

    await update.message.reply_text(
        M.compose(
            M.header(M.ICON_USER, f"Active users ({len(users)})"),
            "\n".join(body_lines),
        ),
        parse_mode="HTML",
    )


# ── Admin: /promote ──────────────────────────────────────────────────────────


async def cmd_promote(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)
    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_INFO, "Usage"),
                M.code("/promote <user_id>"),
            ),
            parse_mode="HTML",
        )
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text(
            M.msg_error("Invalid user_id."), parse_mode="HTML"
        )
        return

    if access and await access.promote(target_id, role="admin"):
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_ADMIN, "User promoted"),
                M.kv("User", str(target_id), value_is_code=True) + "\n" +
                M.kv("Role", "admin"),
            ),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_ERROR, "User not found"),
                M.kv("ID", str(target_id), value_is_code=True),
            ),
            parse_mode="HTML",
        )


# ── Admin: /demote ───────────────────────────────────────────────────────────


async def cmd_demote(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)
    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_INFO, "Usage"),
                M.code("/demote <user_id> [user|viewer]"),
                M.footer_hint("Default role = user. Enough to drop admin to user."),
            ),
            parse_mode="HTML",
        )
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text(
            M.msg_error("Invalid user_id."), parse_mode="HTML"
        )
        return

    role = args[1] if len(args) > 1 else "user"
    if role not in ("user", "viewer", "admin"):
        await update.message.reply_text(
            M.msg_error("Role must be user, viewer, or admin."), parse_mode="HTML"
        )
        return

    if access and await access.demote(target_id, role=role):  # type: ignore[arg-type]
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_SUCCESS, "Role updated"),
                M.kv("User", str(target_id), value_is_code=True) + "\n" +
                M.kv("Role", role),
            ),
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_ERROR, "User not found"),
                M.kv("ID", str(target_id), value_is_code=True),
            ),
            parse_mode="HTML",
        )


# ── Admin: /revoke ───────────────────────────────────────────────────────────


async def cmd_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)
    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_INFO, "Usage"),
                M.code("/revoke <token>"),
            ),
            parse_mode="HTML",
        )
        return

    token = args[0].strip()
    if access:
        await access.revoke_invite(token)
        await update.message.reply_text(
            M.msg_invite_revoked(token), parse_mode="HTML"
        )


# ── Admin: /stats ─────────────────────────────────────────────────────────────


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)
    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    claude = ctx.bot_data.get("claude_facade")
    if claude is None:
        await update.message.reply_text(
            M.msg_unavailable("Claude bridge"), parse_mode="HTML"
        )
        return

    summaries = claude._costs.all_summaries()
    if not summaries:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_STATS, "Usage stats"),
                "No usage data yet.",
            ),
            parse_mode="HTML",
        )
        return

    total_cost = sum(s["lifetime_cost"] for s in summaries)
    total_requests = sum(s["lifetime_requests"] for s in summaries)

    await update.message.reply_text(
        M.compose(
            M.header(M.ICON_STATS, "Usage stats"),
            M.kv_block(
                [
                    ("Active users", str(len(summaries))),
                    ("Total requests", str(total_requests)),
                    ("Total cost", f"${total_cost:.4f}"),
                ]
            ),
        ),
        parse_mode="HTML",
    )


# ── Admin: /sessions ──────────────────────────────────────────────────────────


async def cmd_sessions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)
    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    claude = ctx.bot_data.get("claude_facade")
    if claude is None:
        await update.message.reply_text(
            M.msg_unavailable("Claude bridge"), parse_mode="HTML"
        )
        return

    count = claude._sessions.active_count()
    await update.message.reply_text(
        M.compose(
            M.header(M.ICON_NEW, "Active Claude sessions"),
            M.kv("Count", str(count)),
        ),
        parse_mode="HTML",
    )


# ── Admin: /alerts ────────────────────────────────────────────────────────────


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)
    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    args = ctx.args or []
    sub = args[0].lower() if args else ""

    if sub == "on":
        if settings:
            settings.hourly_report_enabled = True
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_BELL, "Hourly reports enabled"),
                M.kv("Status", "🟢 ON"),
            ),
            parse_mode="HTML",
        )
    elif sub == "off":
        if settings:
            settings.hourly_report_enabled = False
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_BELL, "Hourly reports disabled"),
                M.kv("Status", "🔴 OFF"),
            ),
            parse_mode="HTML",
        )
    else:
        is_on = bool(settings and settings.hourly_report_enabled)
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_BELL, "Hourly reports"),
                M.kv("Status", "🟢 ON" if is_on else "🔴 OFF"),
                f"Toggle: {M.code('/alerts on')} | {M.code('/alerts off')}",
            ),
            parse_mode="HTML",
        )


# ── /remote ──────────────────────────────────────────────────────────────────


async def cmd_remote(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show active Claude sessions, tmux state, and remote control link."""
    import json as _json
    import subprocess

    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)
    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    lines = []

    # 1. Active claude CLI process count (excludes dashboard/bot/peers)
    try:
        result = subprocess.run(
            ["bash", "-c", "pgrep -x claude | wc -l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        count = result.stdout.strip()
        lines.append(f"⚡ <b>Active Claude sessions:</b> {count}")
    except Exception:
        lines.append("⚡ <b>Active Claude sessions:</b> ?")

    # 2. Tmux session'ları (attached/unattached)
    try:
        result = subprocess.run(
            [
                "bash",
                "-c",
                "tmux ls -F '#{session_name} #{session_attached}' 2>/dev/null",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.stdout.strip():
            tmux_lines = result.stdout.strip().split("\n")
            attached = [ln.split()[0] for ln in tmux_lines if ln.endswith(" 1")]
            detached = [ln.split()[0] for ln in tmux_lines if ln.endswith(" 0")]
            lines.append(
                f"\n🖥 <b>Tmux:</b> {len(attached)} active, {len(detached)} background"
            )
            for a in attached:
                lines.append(f"  ✅ tmux {a}")
            for d in detached:
                lines.append(f"  💤 tmux {d}")
    except Exception:
        pass

    # 3. Recent session files (topic info)
    try:
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"ls -t {os.path.expanduser('~')}/.claude/sessions/*.md 2>/dev/null | head -5",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        session_files = [f for f in result.stdout.strip().split("\n") if f.strip()]
        if session_files:
            lines.append("\n📋 <b>Recent sessions:</b>")
            for sf in session_files[:5]:
                try:
                    fname = sf.split("/")[-1].replace(".md", "")
                    head_result = subprocess.run(
                        ["head", "-5", sf],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    topic = ""
                    for line in head_result.stdout.split("\n"):
                        if line.startswith("# "):
                            topic = line[2:].strip()
                            break
                    display = f"{escape_html(topic[:50])}" if topic else "no topic"
                    lines.append(f"  • <code>{fname[:25]}</code> — {display}")
                except Exception:
                    pass
    except Exception:
        pass

    # 4. Remote control links — extract from active JSONL transcripts
    try:
        result = subprocess.run(
            [
                "bash",
                "-c",
                r"""
python3 -c "
import os, glob, time, re, json
base = os.path.expanduser('~/.claude/projects/-mnt-c-Users-Hakan/')
files = sorted(glob.glob(base + '*.jsonl'), key=os.path.getmtime, reverse=True)[:5]
results = []
for f in files:
    mtime = os.path.getmtime(f)
    age_h = (time.time() - mtime) / 3600
    if age_h > 24:
        continue
    with open(f) as fh:
        content = fh.read()
    links = set(re.findall(r'session_0[A-Za-z0-9]{20,30}', content))
    if not links:
        continue
    # Extract topic from first user message
    topic = ''
    for line in content.split('\n'):
        try:
            d = json.loads(line)
            if d.get('type') == 'human' and d.get('message',{}).get('content'):
                c = d['message']['content']
                if isinstance(c, str):
                    topic = c[:50]
                elif isinstance(c, list):
                    for block in c:
                        if isinstance(block, dict) and block.get('text'):
                            topic = block['text'][:50]
                            break
                if topic:
                    break
        except: pass
    age_str = f'{int(age_h*60)}m' if age_h < 1 else f'{age_h:.1f}h'
    for link in links:
        results.append(json.dumps({'link': link, 'topic': topic, 'age': age_str}))
for r in results:
    print(r)
"
""",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        rc_items = []
        seen_links = set()
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                item = _json.loads(line)
                if item["link"] not in seen_links:
                    seen_links.add(item["link"])
                    rc_items.append(item)
            except Exception:
                pass

        if rc_items:
            lines.append("\n🔗 <b>Remote Control</b>")
            for i, item in enumerate(rc_items, 1):
                topic = escape_html(item.get("topic", "")[:45])
                age = item.get("age", "?")
                link = f"https://claude.ai/code/{item['link']}"
                lines.append(
                    f"\n<b>{i}.</b> <a href=\"{link}\">Session {item['link'][-8:]}</a> · {age} ago"
                )
                if topic:
                    lines.append(f"   📝 <i>{topic}</i>")
        else:
            lines.append("\n🔗 <b>Remote Control:</b> no active session")
    except Exception:
        lines.append("\n🔗 <b>Remote Control:</b>\nhttps://claude.ai/code")

    # 5. Orphan process warning
    try:
        result = subprocess.run(
            ["bash", "-c", "pgrep -f 'bun.*claude-peers/server.ts' | wc -l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        bun_count = int(result.stdout.strip())
        claude_count = int(
            subprocess.run(
                ["bash", "-c", "pgrep -x claude | wc -l"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
        if bun_count > claude_count:
            orphans = bun_count - claude_count
            lines.append(f"\n⚠️ {orphans} orphan peers process detected")
    except Exception:
        pass

    await update.message.reply_text(
        "\n".join(lines) if lines else M.compose(
            M.header(M.ICON_INFO, "Remote control"),
            "No active sessions.",
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ── Admin: /limit ────────────────────────────────────────────────────────────


def _fmt_limit_value(
    per_user: float | None, default_limit: float | None
) -> str:
    """Render a user's effective daily cost cap for display."""
    if per_user is None:
        if default_limit is None or default_limit < 0:
            return "unlimited (default)"
        return f"${default_limit:.2f}/day (default)"
    if per_user < 0:
        return "unlimited (override)"
    return f"${per_user:.2f}/day (override)"


async def cmd_limit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only daily cost limit management.

    /limit                       — list every active user + effective cap + today's spend
    /limit <user_id> <amount>    — set per-user daily cap in USD (e.g. 2.5)
    /limit <user_id> off         — mark user as unlimited (admin treatment)
    /limit <user_id> reset       — clear override; user falls back to default
    """
    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)
    storage = _storage(ctx)
    claude = ctx.bot_data.get("claude_facade")

    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    if storage is None:
        await update.message.reply_text(
            M.msg_unavailable("Storage"), parse_mode="HTML"
        )
        return

    default_limit = None
    if claude is not None:
        # Mirror ClaudeFacade._resolve_limit fallback rules for display only.
        default_limit = getattr(claude, "_default_limit", None)
        if default_limit is not None and default_limit < 0:
            default_limit = None

    args = ctx.args or []

    # /limit  → list everyone
    if not args:
        users = await storage.users.list_active()
        if not users:
            await update.message.reply_text(
                M.compose(
                    M.header(M.ICON_STATS, "Daily cost limits"),
                    "No active users yet.",
                ),
                parse_mode="HTML",
            )
            return

        admin_id = settings.admin_telegram_id if settings else None
        default_str = (
            "unlimited" if default_limit is None else f"${default_limit:.2f}/day"
        )

        body_lines = []
        for u in users[:30]:
            name = f"@{u.username}" if u.username else f"id:{u.user_id}"
            today = (
                claude._costs.today_cost(u.user_id) if claude is not None else 0.0
            )
            if u.user_id == admin_id or u.role == "admin":
                cap_str = "unlimited (admin)"
            else:
                cap_str = _fmt_limit_value(u.daily_cost_limit, default_limit)
            body_lines.append(
                f"• {escape_html(name)} {M.code(str(u.user_id))} — "
                f"today ${today:.2f} / {cap_str}"
            )

        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_STATS, "Daily cost limits"),
                M.kv("Default", default_str),
                "\n".join(body_lines),
            ),
            parse_mode="HTML",
        )
        return

    # /limit <user_id> <amount|off|reset>
    if len(args) < 2:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_INFO, "Usage"),
                "\n".join(
                    [
                        f"{M.code('/limit')} — list all user limits",
                        f"{M.code('/limit <user_id> <USD>')} — set cap (e.g. 2.5)",
                        f"{M.code('/limit <user_id> off')} — unlimited (admin-style)",
                        f"{M.code('/limit <user_id> reset')} — back to default",
                    ]
                ),
            ),
            parse_mode="HTML",
        )
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text(
            M.msg_error("user_id must be a number."), parse_mode="HTML"
        )
        return

    target = await storage.users.get(target_id)
    if target is None:
        await update.message.reply_text(
            M.compose(
                M.header(M.ICON_ERROR, "User not found"),
                M.kv("ID", str(target_id), value_is_code=True),
            ),
            parse_mode="HTML",
        )
        return

    raw = args[1].strip().lower()
    if raw in ("reset", "default", "clear"):
        new_value: float | None = None
        title = "Limit reset"
        detail = "Back to default."
    elif raw in ("off", "unlimited", "none"):
        new_value = -1.0
        title = "Limit removed"
        detail = "Unlimited (admin-style)."
    else:
        try:
            new_value = float(raw.replace(",", "."))
        except ValueError:
            await update.message.reply_text(
                M.msg_error("Invalid value. Pass a USD number, or off/reset."),
                parse_mode="HTML",
            )
            return
        if new_value < 0:
            await update.message.reply_text(
                M.msg_error("For negative values use 'off'."), parse_mode="HTML"
            )
            return
        title = "Limit updated"
        detail = f"Daily cap set to ${new_value:.2f}."

    await storage.users.set_cost_limit(target_id, new_value)
    logger.info(
        "Cost limit updated",
        admin_id=user.id,
        target_id=target_id,
        new_value=new_value,
    )
    await update.message.reply_text(
        M.compose(
            M.header(M.ICON_SUCCESS, title),
            M.kv("User", str(target_id), value_is_code=True),
            detail,
        ),
        parse_mode="HTML",
    )


# ── /epic ──────────────────────────────────────────────────────────────────


async def cmd_epic(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a FRESH Epic free-game claim link on demand.

    Epic exchange codes live ~5 min (Epic-side, not configurable) — the cron's
    link goes stale before it's clicked. This mints a fresh one the moment the
    user asks. claim.py --notify-only auto-discovers the currently-free games,
    builds the exchange-code URL, and sends it via Telegram itself.
    """
    import asyncio as _asyncio

    user = update.effective_user
    if user is None:
        return

    access = _access_mgr(ctx)
    settings = _settings(ctx)
    is_admin = (settings and user.id == settings.admin_telegram_id) or (
        access and await access.is_admin(user.id)
    )
    if not is_admin:
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return

    await update.message.reply_text(
        "🎮 Taze Epic linki üretiliyor (birkaç saniye)...", parse_mode="HTML"
    )

    py = "/home/hakan/.local/share/pipx/venvs/playwright/bin/python"
    script = "/home/hakan/.claude/skills/epic-free-game-claim/claim.py"
    try:
        proc = await _asyncio.create_subprocess_exec(
            py, script, "--notify-only",
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.STDOUT,
        )
        out, _ = await _asyncio.wait_for(proc.communicate(), timeout=90)
        text = (out or b"").decode(errors="replace")
        if proc.returncode == 0:
            if "no free games currently" in text:
                await update.message.reply_text(
                    "ℹ️ Şu an Epic'te ücretsiz oyun yok.", parse_mode="HTML"
                )
            elif "all owned — no link sent" in text:
                # claim.py already sent "zaten kütüphanende — alman gereken yok"
                pass
            else:
                await update.message.reply_text(
                    "✅ Link gönderildi — 5 dk içinde tıkla.", parse_mode="HTML"
                )
        else:
            tail = escape_html(text[-300:]) if text else "(çıktı yok)"
            await update.message.reply_text(
                f"⚠️ Epic linki üretilemedi (kod {proc.returncode}):\n<pre>{tail}</pre>",
                parse_mode="HTML",
            )
    except _asyncio.TimeoutError:
        await update.message.reply_text(
            "⚠️ Epic link üretimi zaman aşımına uğradı (90s).", parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Hata: {escape_html(str(e))}", parse_mode="HTML"
        )
