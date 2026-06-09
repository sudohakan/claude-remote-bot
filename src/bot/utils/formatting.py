"""Message formatting utilities for Telegram.

Key responsibilities:
- Split long messages at 4000-char boundaries without breaking code blocks
- Escape HTML special characters
- Format common bot responses (status, user info, etc.)
"""

import html
import re
from typing import List

from .constants import MESSAGE_CHUNK_SIZE


def escape_html(text: str) -> str:
    """Escape text for safe inclusion in Telegram HTML messages."""
    return html.escape(text)


_CODE_BLOCK_RE = re.compile(r"```([\w+-]*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")


def claude_to_telegram_html(text: str) -> str:
    """Render Claude's Markdown-ish output as Telegram-safe HTML.

    Escapes <, >, & first so literal angle brackets in Claude output
    (e.g. <uuid>, <session_id>) can't be misread as HTML tags.  Then
    re-applies the small subset of tags Telegram actually supports:
    <pre><code>, <code>, <b>, <i>.
    """
    placeholders: List[str] = []

    def _stash(rendered: str) -> str:
        placeholders.append(rendered)
        return f"\x00TG_HTML_{len(placeholders) - 1}\x00"

    def _code_block(match: "re.Match[str]") -> str:
        lang = match.group(1).strip()
        body = html.escape(match.group(2))
        if lang:
            return _stash(f'<pre><code class="language-{lang}">{body}</code></pre>')
        return _stash(f"<pre><code>{body}</code></pre>")

    def _inline_code(match: "re.Match[str]") -> str:
        return _stash(f"<code>{html.escape(match.group(1))}</code>")

    work = _CODE_BLOCK_RE.sub(_code_block, text)
    work = _INLINE_CODE_RE.sub(_inline_code, work)
    work = html.escape(work)
    work = _BOLD_RE.sub(r"<b>\1</b>", work)
    work = _ITALIC_RE.sub(r"<i>\1</i>", work)

    def _restore(match: "re.Match[str]") -> str:
        return placeholders[int(match.group(1))]

    return re.sub(r"\x00TG_HTML_(\d+)\x00", _restore, work)


def split_message(text: str, chunk_size: int = MESSAGE_CHUNK_SIZE) -> List[str]:
    """Split `text` into chunks of at most `chunk_size` characters.

    Tries to split at paragraph boundaries first, then at newlines,
    then hard-cuts as a last resort.  Code blocks that span a chunk
    boundary get closing/opening ``` fences added.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks: List[str] = []
    remaining = text
    in_code_block = False
    code_fence = ""

    while len(remaining) > chunk_size:
        window = remaining[:chunk_size]

        # Track code fence state so we can close/open blocks across chunks
        fences = re.findall(r"```(\w*)", window)
        for fence in fences:
            if in_code_block:
                in_code_block = False
                code_fence = ""
            else:
                in_code_block = True
                code_fence = fence

        # Find a good split point: paragraph > newline > space > hard cut
        split_at = chunk_size
        for pattern in ["\n\n", "\n", " "]:
            idx = window.rfind(pattern, chunk_size // 2)
            if idx != -1:
                split_at = idx + len(pattern)
                break

        chunk = remaining[:split_at]
        if in_code_block:
            chunk += "\n```"  # close open block

        chunks.append(chunk)
        remaining = remaining[split_at:]

        if in_code_block and remaining:
            remaining = f"```{code_fence}\n" + remaining

    if remaining:
        chunks.append(remaining)

    return chunks


def format_code_block(code: str, language: str = "") -> str:
    """Wrap code in a Telegram-friendly HTML pre/code block."""
    escaped = escape_html(code)
    if language:
        return f'<pre><code class="language-{language}">{escaped}</code></pre>'
    return f"<pre>{escaped}</pre>"


def format_status_line(label: str, value: str, ok: bool = True) -> str:
    """Format a single status key=value line with a traffic-light indicator."""
    icon = "" if ok else ""
    return f"{icon} <b>{escape_html(label)}:</b> {escape_html(value)}"


def format_user_info(user_id: int, username: str | None, role: str, access: str) -> str:
    """Format a compact user summary."""
    name = f"@{username}" if username else f"id:{user_id}"
    return (
        f"<b>{escape_html(name)}</b>\n"
        f"  Role: <code>{escape_html(role)}</code>\n"
        f"  Access: <code>{escape_html(access)}</code>"
    )
