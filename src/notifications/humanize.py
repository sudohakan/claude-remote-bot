"""Every message the bot sends to Hakan on its own (notifications, tunnel notices) passes the shared humanizer
in ~/.claude/scripts/lib/tg_humanize.py (local model, fail-soft: on any problem the original text goes out)."""

import asyncio
import os
import sys

LIB = os.path.expanduser("~/.claude/scripts/lib")


def _humanize_sync(text: str) -> str:
    try:
        if LIB not in sys.path:
            sys.path.insert(0, LIB)
        import tg_humanize  # type: ignore[import-not-found]

        return tg_humanize.humanize(text)
    except Exception:
        return text


async def humanize(text: str) -> str:
    """Off the event loop: the local model can take a few seconds."""
    if os.environ.get("TG_HUMANIZE") == "0":
        return text
    return await asyncio.to_thread(_humanize_sync, text)
