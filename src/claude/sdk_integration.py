"""Claude SDK integration — primary execution path.

Uses claude-agent-sdk if available; falls back to subprocess CLI.
Enforces:
  - Max 120s timeout (configurable)
  - Max 3 concurrent executions via semaphore
  - Subprocess uses list form — never shell=True
"""

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from .exceptions import ClaudeAuthError, ClaudeProcessError, ClaudeTimeoutError

logger = structlog.get_logger(__name__)

_CONCURRENT_LIMIT = 3
_semaphore: asyncio.Semaphore = asyncio.Semaphore(_CONCURRENT_LIMIT)

# Isolated HOME for non-admin Claude subprocess. Keeps the host's
# ~/.claude/ hooks, plugins, MCP servers, and Bitwarden vault injection
# out of guest sessions. Admin (full_access=True) keeps the real HOME.
_SANDBOX_HOME = "/home/hakan/.claude-bot-sandbox"


@dataclass
class ClaudeResponse:
    """Normalised response from Claude (SDK or CLI)."""

    content: str
    session_id: str
    cost: float = 0.0
    duration_ms: int = 0
    num_turns: int = 1
    tools_used: List[Dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    error_type: Optional[str] = None


class ClaudeSDKRunner:
    """Execute Claude prompts via SDK or CLI subprocess.

    Tries claude-agent-sdk first; if not installed or auth fails,
    falls back to `claude` CLI subprocess.
    """

    def __init__(
        self,
        anthropic_api_key: Optional[str] = None,
        claude_model: Optional[str] = None,
        max_turns: int = 10,
        timeout_seconds: int = 120,
        cli_path: Optional[str] = None,
    ) -> None:
        self._api_key = anthropic_api_key
        self._model = claude_model
        self._max_turns = max_turns
        self._timeout = timeout_seconds
        self._cli = cli_path or "claude"

        if anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = anthropic_api_key
            logger.info("Claude SDK: using provided API key")
        else:
            logger.info("Claude SDK: relying on CLI auth")

    async def run(
        self,
        prompt: str,
        working_dir: Path,
        session_id: Optional[str] = None,
        continue_session: bool = False,
        full_access: bool = False,
    ) -> ClaudeResponse:
        """Execute a prompt, honouring the concurrency semaphore and timeout.

        Args:
            full_access: If True, run CLI with --dangerously-skip-permissions
                         enabling MCP servers, plugins, and slash commands.
                         Only set True for admin users.
        """
        start = asyncio.get_event_loop().time()

        async with _semaphore:
            try:
                response = await asyncio.wait_for(
                    self._execute(
                        prompt, working_dir, session_id, continue_session, full_access
                    ),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                int((asyncio.get_event_loop().time() - start) * 1000)
                raise ClaudeTimeoutError(
                    f"Claude timed out after {self._timeout}s"
                ) from None

        response.duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)
        return response

    async def _execute(
        self,
        prompt: str,
        working_dir: Path,
        session_id: Optional[str],
        continue_session: bool,
        full_access: bool = False,
    ) -> ClaudeResponse:
        """Try SDK, fall back to CLI.

        full_access propagates through every path so admin users keep their
        permission bypass on the ImportError/auth fallback as well — otherwise
        an SDK install hiccup silently demotes admin to the gated default mode.
        """
        # Both admin (full_access) and guest paths route through _run_cli.
        # Admin uses --dangerously-skip-permissions + host HOME so MCP servers,
        # plugins, hooks, slash commands all work. Guest uses sandbox HOME
        # (no hooks/MCP/secrets). The SDK stream-json path is bypassed because
        # the bundled CLI 2.1.88 returns exit 1 under the sandboxed env, and
        # the simple JSON `--output-format json -p` mode works reliably in both.
        return await self._run_cli(
            prompt, working_dir, session_id, full_access=full_access
        )

    async def _run_sdk(
        self,
        prompt: str,
        working_dir: Path,
        session_id: Optional[str],
        continue_session: bool,
        full_access: bool = False,
    ) -> ClaudeResponse:
        """Execute via claude-agent-sdk."""
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
        )

        sandbox_env = {} if full_access else {"HOME": _SANDBOX_HOME}
        options = ClaudeAgentOptions(
            max_turns=self._max_turns,
            model=self._model or None,
            cwd=str(working_dir),
            # Admin users bypass permission prompts so writes to ~/.claude/,
            # MCP tool calls, and slash commands are not gated. The CLI fallback
            # uses --dangerously-skip-permissions for the same effect.
            permission_mode="bypassPermissions" if full_access else None,
            env=sandbox_env,
        )
        if session_id and continue_session:
            options.resume = session_id

        messages = []
        async with ClaudeSDKClient(options) as client:
            await client.query(prompt)
            async for msg in client._query.receive_messages():
                messages.append(msg)
                if isinstance(msg, ResultMessage):
                    break

        cost = 0.0
        final_session_id = session_id or ""
        content = ""
        tools_used: List[Dict[str, Any]] = []

        for msg in messages:
            if isinstance(msg, ResultMessage):
                cost = getattr(msg, "total_cost_usd", 0.0) or 0.0
                final_session_id = getattr(msg, "session_id", session_id) or ""
                content = getattr(msg, "result", "") or ""

        return ClaudeResponse(
            content=content,
            session_id=final_session_id,
            cost=cost,
            tools_used=tools_used,
            num_turns=len([m for m in messages if isinstance(m, AssistantMessage)]),
        )

    async def _run_cli(
        self,
        prompt: str,
        working_dir: Path,
        session_id: Optional[str],
        full_access: bool = False,
    ) -> ClaudeResponse:
        """Execute via claude CLI subprocess.

        Args:
            full_access: If True, run with --dangerously-skip-permissions
                         so MCP servers, plugins, and slash commands work.
                         Only enable for admin users.
        """
        # json output carries session_id + cost so the facade can adopt the
        # real Claude session_id for the next --resume. text mode would drop
        # it and every turn would silently start a fresh conversation.
        cmd: List[str] = [self._cli, "--output-format", "json"]

        if full_access:
            cmd += ["--dangerously-skip-permissions"]

        if session_id:
            cmd += ["--resume", session_id]

        if self._model:
            cmd += ["--model", self._model]

        cmd += ["--", prompt]

        # Isolate guest subprocess from host ~/.claude/ (hooks, MCP, secrets).
        # Admin keeps host HOME so their session matches a real Claude Code env.
        subprocess_env = os.environ.copy()
        if not full_access:
            subprocess_env["HOME"] = _SANDBOX_HOME

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(working_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=subprocess_env,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            raise ClaudeProcessError(
                f"Claude CLI not found at '{self._cli}'. "
                "Install: npm install -g @anthropic-ai/claude-code"
            )

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            if "authentication" in err.lower() or "api key" in err.lower():
                raise ClaudeAuthError(f"Claude auth failed: {err}")
            # Stale session — retry without --resume
            if "no conversation found" in err.lower() and session_id:
                logger.warning(
                    "Stale session, retrying without resume", session_id=session_id
                )
                return await self._run_cli(
                    prompt, working_dir, None, full_access=full_access
                )
            raise ClaudeProcessError(f"Claude CLI error (rc={proc.returncode}): {err}")

        raw = stdout.decode(errors="replace").strip()
        try:
            import json as _json

            payload = _json.loads(raw)
            content = (payload.get("result") or "").strip()
            real_sid = payload.get("session_id") or session_id or ""
            cost = float(payload.get("total_cost_usd") or 0.0)
            turns = int(payload.get("num_turns") or 1)
        except (ValueError, TypeError):
            # Defensive: if CLI changes output shape, fall back to raw text
            # so the user still gets a reply, even though session_id is lost
            # for that turn.
            content = raw
            real_sid = session_id or ""
            cost = 0.0
            turns = 1

        return ClaudeResponse(
            content=content,
            session_id=real_sid,
            cost=cost,
            num_turns=turns,
        )
