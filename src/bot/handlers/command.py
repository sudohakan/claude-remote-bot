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

import structlog
from telegram import Update
from telegram.ext import ContextTypes

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
            "Welcome back, admin.\nUse /help for commands.",
            parse_mode="HTML",
        )
        return

    # If no token given, check if already registered
    if not args:
        if access and await access.is_authorised(user.id):
            await update.message.reply_text(
                f"Welcome back, {escape_html(user.first_name)}!\n"
                "Use /help to see available commands.",
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
                "Invalid, expired, or already-used invite token.\n"
                "Contact the admin for a new one."
            )
    else:
        await update.message.reply_text("Authentication system unavailable.")


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

    user_commands = (
        "/start — Register with an invite token\n"
        "/help — This help message\n"
        "/about — Bot info\n"
        "/ping — Check bot is alive\n"
        "/new — Start a new Claude session\n"
        "/status — System and tunnel status\n"
        "/ssh — SSH connection info\n"
        "/history — Recent command history\n"
        "/cwd — Current working directory"
    )

    admin_commands = (
        "\n\n<b>Admin Commands:</b>\n"
        "/invite — Generate invite token\n"
        "/promote &lt;id&gt; — Promote user to admin\n"
        "/demote &lt;id&gt; — Demote user\n"
        "/revoke &lt;token&gt; — Revoke invite\n"
        "/users — List active users\n"
        "/sessions — List active Claude sessions\n"
        "/stats — 24h usage statistics\n"
        "/alerts [on|off] — Toggle hourly reports\n"
        "/broadcast &lt;msg&gt; — Message all users"
    )

    text = "<b>Available Commands</b>\n\n" + user_commands
    if is_admin:
        text += admin_commands

    await update.message.reply_text(text, parse_mode="HTML")


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

    text = (
        "<b>Claude Remote Bot</b>\n\n"
        "<b>Architecture:</b>\n"
        "• Python 3.12 + python-telegram-bot 22\n"
        "• anthropic SDK + claude-agent-sdk\n"
        "• SQLite WAL (invite auth, sessions, audit log)\n"
        "• Token-bucket rate limiter\n"
        "• Path-traversal path validator\n"
        f"\n<b>Active features:</b> {escape_html(feature_str)}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ── /ping ─────────────────────────────────────────────────────────────────────


async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong")


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
        await update.message.reply_text("New Claude session started.")
    else:
        await update.message.reply_text("Claude bridge not available.")


# ── /status ───────────────────────────────────────────────────────────────────


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show basic system status."""
    storage = _storage(ctx)
    claude = ctx.bot_data.get("claude_facade")
    user = update.effective_user

    lines = ["<b>Bot Status</b>"]

    if storage:
        db_ok = await storage.health_check()
        lines.append(
            f"{'✅' if db_ok else '❌'} Database: {'OK' if db_ok else 'ERROR'}"
        )

    if claude and user:
        session = claude.current_session(user.id)
        if session:
            lines.append(
                f"📂 Working dir: <code>{escape_html(str(session.working_dir))}</code>"
            )
            lines.append(f"💬 Session turns: {session.total_turns}")
            lines.append(
                f"💰 Today cost: ${claude.cost_summary(user.id)['today_cost']:.4f}"
            )
        else:
            lines.append("No active Claude session — use /new to start one.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /ssh ─────────────────────────────────────────────────────────────────────


async def cmd_ssh(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show SSH tunnel status and connection info."""
    tunnel_mgr = ctx.bot_data.get("tunnel_manager")
    settings = _settings(ctx)

    if tunnel_mgr is None:
        if settings and not settings.enable_tunnel:
            await update.message.reply_text(
                "Tunnel manager is disabled.\n"
                "Enable it with ENABLE_TUNNEL=true in .env"
            )
        else:
            await update.message.reply_text("Tunnel manager not available.")
        return

    state = tunnel_mgr.get_state()
    if state.status == "up":
        host = state.ssh_host or "?"
        port = state.ssh_port or "?"
        await update.message.reply_text(
            f"<b>SSH Tunnel: UP</b>\n\n"
            f"<code>ssh hakan@{escape_html(str(host))} -p {port}</code>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"<b>SSH Tunnel: {escape_html(state.status.upper())}</b>",
            parse_mode="HTML",
        )


# ── /history ──────────────────────────────────────────────────────────────────


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    storage = _storage(ctx)
    if storage is None:
        await update.message.reply_text("Storage unavailable.")
        return

    entries = await storage.commands.recent_for_user(user.id, limit=10)
    if not entries:
        await update.message.reply_text("No command history.")
        return

    lines = ["<b>Recent Commands:</b>"]
    for entry in entries:
        ts = entry.logged_at.strftime("%H:%M") if entry.logged_at else "--:--"
        icon = "✅" if entry.result == "ok" else "❌"
        lines.append(f"{icon} <code>{ts}</code> {escape_html(entry.command)}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /cwd ──────────────────────────────────────────────────────────────────────


async def cmd_cwd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    claude = ctx.bot_data.get("claude_facade")
    if claude is None:
        await update.message.reply_text("Claude bridge not available.")
        return

    session = claude.current_session(user.id)
    if session:
        await update.message.reply_text(
            f"<code>{escape_html(str(session.working_dir))}</code>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text("No active session. Use /new to start one.")


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
        await update.message.reply_text("Admin only.")
        return

    if access is None:
        await update.message.reply_text("Access manager unavailable.")
        return

    # Rate check: invites_per_hour
    limiter = ctx.bot_data.get("rate_limiter")
    if limiter:
        allowed, wait = await limiter.check("invites", user.id)
        if not allowed:
            await update.message.reply_text(f"Invite rate limit — wait {wait:.0f}s.")
            return

    invite = await access.create_invite(created_by=user.id, ttl_hours=24)
    await update.message.reply_text(
        f"<b>Invite Token</b> (24h)\n\n"
        f"<code>/start {invite.token}</code>\n\n"
        f"Share this with the new user.",
        parse_mode="HTML",
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
        await update.message.reply_text("Admin only.")
        return

    if storage is None:
        await update.message.reply_text("Storage unavailable.")
        return

    users = await storage.users.list_active()
    if not users:
        await update.message.reply_text("No active users.")
        return

    lines = [f"<b>Active Users ({len(users)})</b>"]
    for u in users[:20]:
        name = f"@{u.username}" if u.username else f"id:{u.user_id}"
        lines.append(
            f"• {escape_html(name)} — {escape_html(u.role)} / {escape_html(u.access_level)}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


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
        await update.message.reply_text("Admin only.")
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text("Usage: /promote <user_id>")
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    if access and await access.promote(target_id, role="admin"):
        await update.message.reply_text(f"User {target_id} promoted to admin.")
    else:
        await update.message.reply_text(f"User {target_id} not found.")


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
        await update.message.reply_text("Admin only.")
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text("Usage: /demote <user_id> [role]")
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    role = args[1] if len(args) > 1 else "user"
    if role not in ("user", "viewer", "admin"):
        await update.message.reply_text("Role must be: user, viewer, or admin")
        return

    if access and await access.demote(target_id, role=role):  # type: ignore[arg-type]
        await update.message.reply_text(f"User {target_id} set to {role}.")
    else:
        await update.message.reply_text(f"User {target_id} not found.")


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
        await update.message.reply_text("Admin only.")
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text("Usage: /revoke <token>")
        return

    token = args[0].strip()
    if access:
        await access.revoke_invite(token)
        await update.message.reply_text(f"Invite {token[:4]}**** revoked.")


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
        await update.message.reply_text("Admin only.")
        return

    claude = ctx.bot_data.get("claude_facade")
    if claude is None:
        await update.message.reply_text("Claude bridge not available.")
        return

    summaries = claude._costs.all_summaries()
    if not summaries:
        await update.message.reply_text("No usage data yet.")
        return

    total_cost = sum(s["lifetime_cost"] for s in summaries)
    total_requests = sum(s["lifetime_requests"] for s in summaries)

    await update.message.reply_text(
        f"<b>Usage Stats</b>\n\n"
        f"Total users with activity: {len(summaries)}\n"
        f"Total requests: {total_requests}\n"
        f"Total cost: ${total_cost:.4f}",
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
        await update.message.reply_text("Admin only.")
        return

    claude = ctx.bot_data.get("claude_facade")
    if claude is None:
        await update.message.reply_text("Claude bridge not available.")
        return

    count = claude._sessions.active_count()
    await update.message.reply_text(
        f"<b>Active Claude Sessions:</b> {count}",
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
        await update.message.reply_text("Admin only.")
        return

    args = ctx.args or []
    sub = args[0].lower() if args else ""

    if sub == "on":
        if settings:
            settings.hourly_report_enabled = True
        await update.message.reply_text("Hourly reports enabled.")
    elif sub == "off":
        if settings:
            settings.hourly_report_enabled = False
        await update.message.reply_text("Hourly reports disabled.")
    else:
        state = "ON" if (settings and settings.hourly_report_enabled) else "OFF"
        await update.message.reply_text(
            f"Hourly reports are currently <b>{state}</b>.\n"
            "Use /alerts on or /alerts off",
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
        await update.message.reply_text("Admin only.")
        return

    lines = []

    # 1. Aktif claude CLI process sayısı (dashboard/bot/peers hariç)
    try:
        result = subprocess.run(
            ["bash", "-c", "pgrep -x claude | wc -l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        count = result.stdout.strip()
        lines.append(f"⚡ <b>Aktif Claude session:</b> {count}")
    except Exception:
        lines.append("⚡ <b>Aktif Claude session:</b> ?")

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
            attached = [l.split()[0] for l in tmux_lines if l.endswith(" 1")]
            detached = [l.split()[0] for l in tmux_lines if l.endswith(" 0")]
            lines.append(
                f"\n🖥 <b>Tmux:</b> {len(attached)} aktif, {len(detached)} arka plan"
            )
            for a in attached:
                lines.append(f"  ✅ tmux {a}")
            for d in detached:
                lines.append(f"  💤 tmux {d}")
    except Exception:
        pass

    # 3. Son session dosyaları (konu bilgisi)
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
            lines.append("\n📋 <b>Son session'lar:</b>")
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
                    display = f"{escape_html(topic[:50])}" if topic else "konu yok"
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
    # İlk user mesajından konu çıkar
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
    age_str = f'{int(age_h*60)}dk' if age_h < 1 else f'{age_h:.1f}sa'
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
            lines.append(f"\n🔗 <b>Remote Control</b>")
            for i, item in enumerate(rc_items, 1):
                topic = escape_html(item.get("topic", "")[:45])
                age = item.get("age", "?")
                link = f"https://claude.ai/code/{item['link']}"
                lines.append(
                    f"\n<b>{i}.</b> <a href=\"{link}\">Session {item['link'][-8:]}</a> · {age} önce"
                )
                if topic:
                    lines.append(f"   📝 <i>{topic}</i>")
        else:
            lines.append("\n🔗 <b>Remote Control:</b> aktif session yok")
    except Exception:
        lines.append("\n🔗 <b>Remote Control:</b>\nhttps://claude.ai/code")

    # 5. Orphan process uyarısı
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
            lines.append(f"\n⚠️ {orphans} orphan peers process tespit edildi")
    except Exception:
        pass

    await update.message.reply_text(
        "\n".join(lines) if lines else "Aktif session bulunamadı.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
