"""Unified Telegram message style — single visual language for the bot.

Every reply, notification, and error renders through helpers here so the
look is consistent: same hierarchy (icon + bold title + sections),
same parse mode (HTML), same English copy.

Convention:
    1. Header line: "{icon} <b>{title}</b>"
    2. Blank line separator
    3. Body: <code> for IDs/paths/commands, <i> for hints, key/value rows
    4. Optional footer with hint or next step

Always send with parse_mode="HTML".
"""

from html import escape
from typing import Iterable, Optional, Tuple

# ── Icon palette ──────────────────────────────────────────────────────────────
ICON_SUCCESS = "✅"
ICON_ERROR = "❌"
ICON_WARNING = "⚠️"
ICON_INFO = "💡"
ICON_WORKING = "⏳"
ICON_UP = "🟢"
ICON_DOWN = "🔴"
ICON_LOCK = "🔒"
ICON_KEY = "🔑"
ICON_USER = "👤"
ICON_ADMIN = "🛡️"
ICON_STATS = "📊"
ICON_LINK = "🔗"
ICON_INVITE = "📥"
ICON_NEW = "🆕"
ICON_TUNNEL = "🛰️"
ICON_SSH = "🖥️"
ICON_BOT = "🤖"
ICON_HISTORY = "📜"
ICON_FOLDER = "📁"
ICON_BELL = "🔔"
ICON_PING = "📡"


# ── Inline formatters ─────────────────────────────────────────────────────────
def b(text: str) -> str:
    """Bold."""
    return f"<b>{escape(str(text))}</b>"


def i(text: str) -> str:
    """Italic."""
    return f"<i>{escape(str(text))}</i>"


def code(text: str) -> str:
    """Inline monospace (commands, IDs, paths)."""
    return f"<code>{escape(str(text))}</code>"


def pre(text: str) -> str:
    """Multi-line code block."""
    return f"<pre>{escape(str(text))}</pre>"


# ── Block composers ───────────────────────────────────────────────────────────
def header(icon: str, title: str) -> str:
    """Top line of every message: icon + bold title."""
    return f"{icon} <b>{escape(title)}</b>"


def kv(label: str, value: str, *, value_is_code: bool = False) -> str:
    """Single key-value row: `Label: value`."""
    val = code(value) if value_is_code else escape(str(value))
    return f"<b>{escape(label)}:</b> {val}"


def kv_block(
    rows: Iterable[Tuple[str, str]], *, code_keys: Optional[set] = None
) -> str:
    """Stack of key-value rows. Pass `code_keys` to wrap select values in <code>."""
    code_keys = code_keys or set()
    return "\n".join(kv(k, v, value_is_code=(k in code_keys)) for k, v in rows)


def section(title: str, body: str) -> str:
    """Optional sub-section under the main header."""
    return f"<b>{escape(title)}</b>\n{body}"


def footer_hint(text: str) -> str:
    """Trailing italic hint, e.g. next step or context."""
    return f"<i>{escape(text)}</i>"


def compose(*parts: str) -> str:
    """Join non-empty blocks with a blank line between them."""
    return "\n\n".join(p for p in parts if p)


# ── Pre-built common messages ─────────────────────────────────────────────────
def msg_welcome_unknown() -> str:
    return compose(
        header(ICON_LOCK, "Welcome"),
        "This bot is invite-only.\n"
        f"If you have a token: {code('/start YOUR_TOKEN')}",
        footer_hint("Contact the admin for an invite."),
    )


def msg_welcome_new_user() -> str:
    return compose(
        header(ICON_SUCCESS, "Account created"),
        f"You're in. Type {code('/help')} for the command list.",
        footer_hint("Send any text and Claude will reply."),
    )


def msg_auth_required() -> str:
    return compose(
        header(ICON_ERROR, "Access denied"),
        f"This bot requires an invite.\n"
        f"Use {code('/start YOUR_TOKEN')} to sign in.",
        footer_hint("Contact the admin for an invite."),
    )


def msg_rate_limited(wait_seconds: float, *, context: str = "Request") -> str:
    return compose(
        header(ICON_WARNING, f"{context} rate limited"),
        f"Wait {b(f'{wait_seconds:.0f}s')} before retrying.",
    )


def msg_admin_only() -> str:
    return compose(
        header(ICON_LOCK, "Admin only"),
        "This command is restricted to admins.",
    )


def msg_error(detail: Optional[str] = None) -> str:
    body = "Something went wrong. Please try again."
    if detail:
        body += f"\n\n{code(detail)}"
    return compose(header(ICON_ERROR, "Error"), body)


def msg_unavailable(component: str) -> str:
    return compose(
        header(ICON_ERROR, "Service unavailable"),
        f"{b(component)} is not responding. Try again later.",
    )


# ── Domain helpers ────────────────────────────────────────────────────────────
def msg_invite_token(token: str, ttl_hours: int = 24) -> str:
    return compose(
        header(ICON_INVITE, "Invite created"),
        kv_block(
            [
                ("Token", token),
                ("Valid for", f"{ttl_hours}h"),
            ],
            code_keys={"Token"},
        ),
        f"Share this with the new user:\n{code(f'/start {token}')}",
    )


def msg_invite_revoked(token: str) -> str:
    return compose(
        header(ICON_SUCCESS, "Invite revoked"),
        kv("Token", f"{token[:4]}****", value_is_code=True),
    )


def msg_tunnel_up(url: str, host: str, port: int) -> str:
    return compose(
        header(ICON_TUNNEL, "Tunnel up"),
        kv_block(
            [
                ("Status", "🟢 Active"),
                ("URL", url),
                ("Host", host),
                ("Port", str(port)),
            ],
            code_keys={"URL", "Host", "Port"},
        ),
        f"SSH:\n{code(f'ssh -p {port} hakan@{host}')}",
    )


def msg_tunnel_down(prev_url: Optional[str] = None) -> str:
    rows = [("Status", "🔴 Offline")]
    if prev_url:
        rows.append(("Last URL", prev_url))
    return compose(
        header(ICON_TUNNEL, "Tunnel down"),
        kv_block(rows, code_keys={"Last URL"}),
        footer_hint("Auto-reconnect will be attempted."),
    )


def msg_tunnel_retries_exhausted(attempts: int) -> str:
    return compose(
        header(ICON_ERROR, "Tunnel reconnect exhausted"),
        f"ngrok failed to restart after {b(f'{attempts} attempts')}.",
        footer_hint("Manual intervention required."),
    )


def msg_session_started() -> str:
    return compose(
        header(ICON_NEW, "New Claude session"),
        "Previous conversation cleared. Fresh start.",
    )


def msg_pong(latency_ms: Optional[int] = None) -> str:
    lat = f" ({latency_ms} ms)" if latency_ms is not None else ""
    return f"{ICON_PING} <b>pong</b>{lat}"
