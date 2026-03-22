"""Credential masking for Claude output.

Applies regex patterns to redact API keys, tokens, passwords, and other
secrets that might leak through Claude's tool output or responses.
"""

import re
from typing import List

import structlog

logger = structlog.get_logger(__name__)

# Each pattern is (regex, replacement_template)
# The replacement preserves a short prefix of the match where possible.
_PATTERNS: List[re.Pattern[str]] = [
    # Anthropic API keys: sk-ant-api03-...
    re.compile(r"sk-ant-api\d*-[A-Za-z0-9_-]{10,}", re.ASCII),
    # Generic sk- keys (OpenAI, etc.)
    re.compile(r"sk-[A-Za-z0-9_-]{20,}", re.ASCII),
    # GitHub tokens
    re.compile(r"ghp_[A-Za-z0-9]{10,}", re.ASCII),
    re.compile(r"gho_[A-Za-z0-9]{10,}", re.ASCII),
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}", re.ASCII),
    # Slack
    re.compile(r"xoxb-[A-Za-z0-9-]{10,}", re.ASCII),
    # AWS access key IDs
    re.compile(r"AKIA[0-9A-Z]{16}", re.ASCII),
    # Bearer tokens in headers
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9+/_.:-]{8,}"),
    # Basic auth
    re.compile(r"(?i)(Basic\s+)[A-Za-z0-9+/=]{8,}"),
    # --token / --secret / --password / --api-key flags
    re.compile(
        r"(?i)(--(?:token|secret|password|api[-_]?key|auth)[= ])['\"]?[A-Za-z0-9+/_.:-]{8,}['\"]?"
    ),
    # KEY=value inline env assignments
    re.compile(
        r"(?i)((?:TOKEN|SECRET|PASSWORD|API_KEY|APIKEY|AUTH_TOKEN|ACCESS_KEY"
        r"|CLIENT_SECRET|WEBHOOK_SECRET|PRIVATE_KEY)=)['\"]?[^\s'\"]{8,}['\"]?"
    ),
    # Telegram bot token pattern
    re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}", re.ASCII),
    # Connection string credentials: user:pass@host
    re.compile(r"://([^:@\s]+:)[^@\s]{4,}@"),
]

_PLACEHOLDER = "***REDACTED***"


class CredentialSanitizer:
    """Mask credentials from text using pattern-based replacement."""

    def sanitize(self, text: str) -> str:
        """Return `text` with all recognised secrets replaced by placeholders."""
        if not text:
            return text

        result = text
        for pattern in _PATTERNS:
            try:
                result = pattern.sub(self._replacer, result)
            except re.error:
                continue  # malformed input; skip pattern

        if result != text:
            logger.debug("Credentials redacted from output")

        return result

    @staticmethod
    def _replacer(match: re.Match) -> str:
        """Preserve capture groups (prefixes) and mask the rest."""
        groups = match.groups()
        if groups:
            # Preserve the first non-None group (the prefix/flag)
            prefix = next((g for g in groups if g is not None), "")
            return prefix + _PLACEHOLDER
        return _PLACEHOLDER
