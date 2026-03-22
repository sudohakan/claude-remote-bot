"""Session export — Markdown, JSON, and HTML formats.

The exporter queries the storage facade for session metadata and
messages, then renders them into the requested format.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from src.storage.facade import StorageFacade


class ExportFormat(Enum):
    """Supported export formats."""

    MARKDOWN = "markdown"
    JSON = "json"
    HTML = "html"


@dataclass
class ExportedSession:
    """Exported session content ready for delivery."""

    format: ExportFormat
    content: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime


class SessionExporter:
    """Export chat sessions to Markdown, JSON, or HTML."""

    def __init__(self, storage: StorageFacade) -> None:
        self.storage = storage

    async def export_session(
        self,
        user_id: int,
        session_id: str,
        fmt: ExportFormat = ExportFormat.MARKDOWN,
    ) -> ExportedSession:
        """Export *session_id* belonging to *user_id* in *fmt*."""
        session = await self.storage.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Retrieve messages — fall back to an empty list if the method
        # is not yet implemented by the storage layer.
        messages: List[Dict[str, Any]] = []
        get_msgs = getattr(self.storage.sessions, "get_messages", None)
        if callable(get_msgs):
            messages = await get_msgs(session_id, limit=500) or []

        session_dict = (
            session if isinstance(session, dict) else session.__dict__
        )

        if fmt == ExportFormat.MARKDOWN:
            content = self._to_markdown(session_dict, messages)
            mime, ext = "text/markdown", "md"
        elif fmt == ExportFormat.JSON:
            content = self._to_json(session_dict, messages)
            mime, ext = "application/json", "json"
        elif fmt == ExportFormat.HTML:
            content = self._to_html(session_dict, messages)
            mime, ext = "text/html", "html"
        else:
            raise ValueError(f"Unsupported format: {fmt}")

        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"session_{session_id[:8]}_{ts}.{ext}"

        return ExportedSession(
            format=fmt,
            content=content,
            filename=filename,
            mime_type=mime,
            size_bytes=len(content.encode()),
            created_at=datetime.now(UTC),
        )

    # ── Renderers ─────────────────────────────────────────────────────────────

    def _to_markdown(
        self, session: Dict[str, Any], messages: List[Dict[str, Any]]
    ) -> str:
        lines = [
            "# Claude Code Session Export",
            f"\n**Session ID:** `{session.get('id', 'unknown')}`",
            f"**Created:** {session.get('created_at', '')}",
        ]
        updated = session.get("updated_at")
        if updated:
            lines.append(f"**Last Updated:** {updated}")
        lines.append(f"**Message Count:** {len(messages)}")
        lines.append("\n---\n")
        for msg in messages:
            ts = msg.get("created_at", "")
            role = "You" if msg.get("role") == "user" else "Claude"
            lines.append(f"### {role} — {ts}")
            lines.append(f"\n{msg.get('content', '')}\n")
            lines.append("---\n")
        return "\n".join(lines)

    def _to_json(
        self, session: Dict[str, Any], messages: List[Dict[str, Any]]
    ) -> str:
        def _iso(v: Any) -> Optional[str]:
            if v is None:
                return None
            return v.isoformat() if hasattr(v, "isoformat") else str(v)

        data = {
            "session": {
                "id": session.get("id"),
                "user_id": session.get("user_id"),
                "created_at": _iso(session.get("created_at")),
                "updated_at": _iso(session.get("updated_at")),
                "message_count": len(messages),
            },
            "messages": [
                {
                    "id": m.get("id"),
                    "role": m.get("role"),
                    "content": m.get("content"),
                    "created_at": _iso(m.get("created_at")),
                }
                for m in messages
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    def _to_html(
        self, session: Dict[str, Any], messages: List[Dict[str, Any]]
    ) -> str:
        md = self._to_markdown(session, messages)
        body = self._md_to_html(md)
        sid = str(session.get("id", ""))[:8]
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Session — {sid}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
line-height:1.6;color:#333;max-width:800px;margin:0 auto;padding:20px;
background:#f5f5f5}}
.container{{background:#fff;padding:30px;border-radius:10px;
box-shadow:0 2px 10px rgba(0,0,0,.1)}}
h1{{color:#2c3e50;border-bottom:3px solid #3498db;padding-bottom:10px}}
h3{{color:#34495e;margin-top:20px}}
code{{background:#f8f8f8;padding:2px 6px;border-radius:3px;font-family:monospace}}
pre{{background:#f8f8f8;padding:15px;border-radius:5px;overflow-x:auto;
border:1px solid #e1e4e8}}
hr{{border:none;border-top:1px solid #e1e4e8;margin:30px 0}}
</style>
</head>
<body>
<div class="container">
{body}
</div>
</body>
</html>"""

    def _md_to_html(self, md: str) -> str:
        html = md
        # Code blocks (must come before inline code)
        html = re.sub(r"```[\w]*\n(.*?)```", r"<pre><code>\1</code></pre>", html, flags=re.DOTALL)
        # Inline code
        html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)
        # Bold
        html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", html)
        # Headers
        html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
        html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
        html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
        # HR
        html = html.replace("\n---\n", "\n<hr>\n")
        # Paragraphs
        html = re.sub(r"\n{2,}", "</p><p>", html)
        html = f"<p>{html}</p>"
        html = re.sub(r"<p>\s*</p>", "", html)
        return html
