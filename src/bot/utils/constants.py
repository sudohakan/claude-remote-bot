"""Bot-wide constants."""

BOT_VERSION = "0.1.0"

# Telegram hard limits
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024

# Soft limit with buffer for multi-part messages
MESSAGE_CHUNK_SIZE = 4000

# Access level descriptions shown in /help
ACCESS_LABELS = {
    "sandbox": "Sandbox (/tmp isolation)",
    "project": "Project (~/claude-users/)",
    "full": "Full (any directory)",
}

# Role descriptions
ROLE_LABELS = {
    "admin": "Admin",
    "user": "User",
    "viewer": "Viewer (read-only)",
}

# User-visible message templates render through src.bot.utils.messages so
# every reply shares the same visual hierarchy (icon + bold title + sections).
# Always paired with parse_mode="HTML".
from src.bot.utils import messages as _M

MSG_WELCOME_UNKNOWN = _M.msg_welcome_unknown()
MSG_WELCOME_NEW_USER = _M.msg_welcome_new_user()
MSG_AUTH_REQUIRED = _M.msg_auth_required()
MSG_ERROR = _M.msg_error()


def msg_rate_limited(wait: float, *, context: str = "Request") -> str:
    return _M.msg_rate_limited(wait, context=context)
