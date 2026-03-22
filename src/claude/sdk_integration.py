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
        """Try SDK, fall back to CLI."""
        if full_access:
            # Full access mode always uses CLI with --dangerously-skip-permissions
            # so MCP servers, plugins, hooks, and slash commands all work
            return await self._run_cli(
                prompt, working_dir, session_id, full_access=True
            )

        try:
            return await self._run_sdk(
                prompt, working_dir, session_id, continue_session
            )
        except ImportError:
            logger.info("claude-agent-sdk not available, using CLI fallback")
            return await self._run_cli(prompt, working_dir, session_id)
        except Exception as exc:
            if "auth" in str(exc).lower() or "api_key" in str(exc).lower():
                logger.warning("SDK auth error, trying CLI fallback", error=str(exc))
                return await self._run_cli(prompt, working_dir, session_id)
            raise

    async def _run_sdk(
        self,
        prompt: str,
        working_dir: Path,
        session_id: Optional[str],
        continue_session: bool,
    ) -> ClaudeResponse:
        """Execute via claude-agent-sdk."""
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
        )

        options = ClaudeAgentOptions(
            max_turns=self._max_turns,
            model=self._model or None,
            cwd=str(working_dir),
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
        cmd: List[str] = [self._cli, "--output-format", "text"]

        if full_access:
            cmd += ["--dangerously-skip-permissions"]

        if session_id:
            cmd += ["--resume", session_id]

        if self._model:
            cmd += ["--model", self._model]

        cmd += ["--", prompt]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(working_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
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
                logger.warning("Stale session, retrying without resume", session_id=session_id)
                return await self._run_cli(prompt, working_dir, None, full_access=full_access)
            raise ClaudeProcessError(f"Claude CLI error (rc={proc.returncode}): {err}")

        return ClaudeResponse(
            content=stdout.decode(errors="replace").strip(),
            session_id=session_id or "",
        )
