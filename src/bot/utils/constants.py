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

# Welcome message shown to new users after /start with no token
MSG_WELCOME_UNKNOWN = (
    "Hello! This bot requires an invite token to access.\n\n"
    "Use <code>/start YOUR_TOKEN</code> to authenticate."
)

# Welcome message shown after successful invite redemption
MSG_WELCOME_NEW_USER = (
    "Welcome! Your account has been created.\n\n"
    "Use /help to see available commands."
)

# Auth rejection message
MSG_AUTH_REQUIRED = (
    "Access denied. You need an invite token.\n"
    "Contact the admin for an invite."
)

# Rate limit message template
MSG_RATE_LIMITED = "Rate limited — please wait {wait:.0f}s before retrying."

# Generic error
MSG_ERROR = "An error occurred. Please try again."
