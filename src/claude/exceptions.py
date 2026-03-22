"""Claude bridge exceptions."""


class ClaudeError(Exception):
    """Base class for all Claude bridge errors."""


class ClaudeTimeoutError(ClaudeError):
    """Claude did not respond within the allowed timeout."""


class ClaudeAuthError(ClaudeError):
    """Authentication / API key error."""


class ClaudeProcessError(ClaudeError):
    """Subprocess or SDK process failure."""


class ClaudeSessionError(ClaudeError):
    """Session creation or management failure."""


class ClaudeParsingError(ClaudeError):
    """Failed to parse Claude's output."""
