"""Input validation — path traversal prevention, upload validation, sanitization.

All public methods return (is_valid, error_message | None).
"""

import re
from pathlib import Path
from typing import Optional, Set, Tuple

import structlog

logger = structlog.get_logger(__name__)

# Maximum allowed upload size: 10 MB
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Extensions that are safe to accept
ALLOWED_EXTENSIONS: Set[str] = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".cpp", ".c", ".h", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".swift", ".kt",
    ".md", ".txt", ".json", ".yml", ".yaml", ".toml",
    ".xml", ".html", ".css", ".scss", ".sql",
    ".sh", ".bash", ".zsh", ".ps1",
    ".r", ".scala", ".clj", ".hs", ".elm",
    ".vue", ".svelte", ".lock", ".env.example",
}

# Filenames that should never be uploaded
BLOCKED_FILENAMES: Set[str] = {
    ".env", ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "shadow", "passwd", "sudoers", "hosts",
    ".bash_history", ".zsh_history",
}

# Regex patterns for dangerous content in paths/filenames
_DANGER_PATTERNS = [
    re.compile(r"\.\."),        # parent directory traversal
    re.compile(r"\x00"),        # null byte injection
    re.compile(r"\$\{"),        # variable expansion ${...}
    re.compile(r"`"),           # backtick command substitution
]


class PathValidator:
    """Validate and resolve user-provided paths against a sandboxed boundary."""

    def __init__(self, sandbox_root: Path) -> None:
        self._root = sandbox_root.resolve()

    def validate(
        self, user_path: str, relative_to: Optional[Path] = None
    ) -> Tuple[bool, Optional[Path], Optional[str]]:
        """Validate a user-provided path.

        Returns:
            (ok, resolved_path, error_message)
        """
        if not user_path or not user_path.strip():
            return False, None, "Empty path"

        cleaned = user_path.strip()

        for pat in _DANGER_PATTERNS:
            if pat.search(cleaned):
                logger.warning(
                    "Dangerous pattern in path",
                    path=cleaned,
                    pattern=pat.pattern,
                )
                return False, None, f"Forbidden pattern in path: {pat.pattern}"

        base = relative_to or self._root
        if cleaned.startswith("/"):
            candidate = Path(cleaned)
        else:
            candidate = base / cleaned

        resolved = candidate.resolve()

        if not self._within_root(resolved):
            logger.warning(
                "Path traversal blocked",
                requested=cleaned,
                resolved=str(resolved),
                root=str(self._root),
            )
            return False, None, "Access denied: path outside sandbox"

        return True, resolved, None

    def _within_root(self, path: Path) -> bool:
        try:
            path.relative_to(self._root)
            return True
        except ValueError:
            return False


def validate_filename(filename: str) -> Tuple[bool, Optional[str]]:
    """Validate an uploaded filename.

    Returns (ok, error_message).
    """
    if not filename or not filename.strip():
        return False, "Empty filename"

    name = filename.strip()

    # No path separators in bare filename
    if "/" in name or "\\" in name:
        return False, "Filename must not contain path separators"

    # Null byte injection
    if "\x00" in name:
        return False, "Invalid filename"

    # Block specific sensitive names
    if name.lower() in {f.lower() for f in BLOCKED_FILENAMES}:
        return False, f"Filename not allowed: {name}"

    # Block hidden files (starting with dot) except a few safe ones
    if name.startswith(".") and name not in {".gitignore", ".gitkeep", ".env.example"}:
        return False, "Hidden files are not accepted"

    # Extension check
    suffix = Path(name).suffix.lower()
    if suffix and suffix not in ALLOWED_EXTENSIONS:
        return False, f"File type not allowed: {suffix}"

    if len(name) > 255:
        return False, "Filename too long (max 255 chars)"

    return True, None


def validate_upload_size(size_bytes: int) -> Tuple[bool, Optional[str]]:
    """Return (ok, error_message) based on upload size."""
    if size_bytes > MAX_UPLOAD_BYTES:
        mb = size_bytes / (1024 * 1024)
        return False, f"File too large: {mb:.1f} MB (max 10 MB)"
    return True, None


def sanitize_text(text: str, max_length: int = 4000) -> str:
    """Strip null bytes and truncate to max_length."""
    cleaned = text.replace("\x00", "")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned
