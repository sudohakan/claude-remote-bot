"""Safe git integration for repository operations.

Only read-only commands are allowed. Dangerous flags and patterns
are blocked. The working directory is validated to stay within
the configured approved directory.
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

import structlog

from src.config.settings import Settings

logger = structlog.get_logger(__name__)


class GitError(Exception):
    """Git operation error."""


class SecurityError(Exception):
    """Security violation in git operation."""


@dataclass
class GitStatus:
    """Git repository status snapshot."""

    branch: str
    modified: List[str]
    added: List[str]
    deleted: List[str]
    untracked: List[str]
    ahead: int
    behind: int

    @property
    def is_clean(self) -> bool:
        return not any([self.modified, self.added, self.deleted, self.untracked])


@dataclass
class CommitInfo:
    """Metadata for a single git commit."""

    hash: str
    author: str
    date: datetime
    message: str
    files_changed: int
    insertions: int
    deletions: int


class GitIntegration:
    """Execute safe, read-only git commands within an approved directory."""

    SAFE_COMMANDS: Set[str] = {
        "status",
        "log",
        "diff",
        "branch",
        "remote",
        "show",
        "ls-files",
        "ls-tree",
        "rev-parse",
        "rev-list",
        "describe",
    }

    DANGEROUS_PATTERNS: List[str] = [
        r"--exec",
        r"--upload-pack",
        r"--receive-pack",
        r"-c\s*core\.gitProxy",
        r"-c\s*core\.sshCommand",
    ]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        approved = getattr(settings, "approved_directory", str(Path.home()))
        self._approved = Path(approved).resolve()

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_status(self, repo_path: Path) -> GitStatus:
        """Return the current status of *repo_path*."""
        branch_out, _ = await self._run(["git", "branch", "--show-current"], repo_path)
        branch = branch_out.strip() or "HEAD"

        status_out, _ = await self._run(["git", "status", "--porcelain=v1"], repo_path)
        modified, added, deleted, untracked = [], [], [], []
        for line in status_out.strip().splitlines():
            if not line:
                continue
            xy, fname = line[:2], line[3:]
            if xy == "??":
                untracked.append(fname)
            elif "M" in xy:
                modified.append(fname)
            elif "A" in xy:
                added.append(fname)
            elif "D" in xy:
                deleted.append(fname)

        ahead = behind = 0
        try:
            rev_out, _ = await self._run(
                ["git", "rev-list", "--count", "--left-right", "HEAD...@{upstream}"],
                repo_path,
            )
            parts = rev_out.strip().split("\t")
            if len(parts) == 2:
                ahead, behind = int(parts[0]), int(parts[1])
        except GitError:
            pass

        return GitStatus(
            branch=branch,
            modified=modified,
            added=added,
            deleted=deleted,
            untracked=untracked,
            ahead=ahead,
            behind=behind,
        )

    async def get_diff(
        self,
        repo_path: Path,
        staged: bool = False,
        file_path: Optional[str] = None,
    ) -> str:
        """Return formatted diff output."""
        cmd = ["git", "diff", "--no-color", "--minimal"]
        if staged:
            cmd.append("--staged")
        if file_path:
            fp_obj = (repo_path / file_path).resolve()
            if not fp_obj.is_relative_to(repo_path.resolve()):
                raise SecurityError("File path outside repository")
            cmd.append(file_path)
        out, _ = await self._run(cmd, repo_path)
        if not out.strip():
            return "No changes to show"
        lines = []
        for line in out.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                lines.append(f"+ {line[1:]}")
            elif line.startswith("-") and not line.startswith("---"):
                lines.append(f"- {line[1:]}")
            else:
                lines.append(line)
        return "\n".join(lines)

    async def get_file_history(
        self, repo_path: Path, file_path: str, limit: int = 10
    ) -> List[CommitInfo]:
        """Return commit history for a specific file."""
        fp_obj = (repo_path / file_path).resolve()
        if not fp_obj.is_relative_to(repo_path.resolve()):
            raise SecurityError("File path outside repository")
        log_out, _ = await self._run(
            [
                "git",
                "log",
                f"--max-count={limit}",
                "--pretty=format:%H|%an|%aI|%s",
                "--numstat",
                "--",
                file_path,
            ],
            repo_path,
        )
        commits: List[CommitInfo] = []
        current: Optional[CommitInfo] = None
        for line in log_out.strip().splitlines():
            if not line:
                continue
            if "|" in line and len(line.split("|")) == 4:
                if current:
                    commits.append(current)
                parts = line.split("|")
                current = CommitInfo(
                    hash=parts[0][:8],
                    author=parts[1],
                    date=datetime.fromisoformat(parts[2].replace("Z", "+00:00")),
                    message=parts[3],
                    files_changed=0,
                    insertions=0,
                    deletions=0,
                )
            elif current and "\t" in line:
                parts2 = line.split("\t")
                if len(parts2) == 3:
                    try:
                        current.insertions += int(parts2[0]) if parts2[0] != "-" else 0
                        current.deletions += int(parts2[1]) if parts2[1] != "-" else 0
                        current.files_changed += 1
                    except ValueError:
                        pass
        if current:
            commits.append(current)
        return commits

    async def execute_git_command(
        self, command: List[str], cwd: Path
    ) -> Tuple[str, str]:
        """Execute an arbitrary validated git command."""
        return await self._run(command, cwd)

    # ── Formatting ────────────────────────────────────────────────────────────

    def format_status(self, status: GitStatus) -> str:
        lines = [f"Branch: {status.branch}"]
        if status.ahead or status.behind:
            tracking = []
            if status.ahead:
                tracking.append(f"ahead {status.ahead}")
            if status.behind:
                tracking.append(f"behind {status.behind}")
            lines.append(f"Tracking: {', '.join(tracking)}")
        if status.is_clean:
            lines.append("Working tree clean")
        else:
            for label, files in [
                ("Modified", status.modified),
                ("Added", status.added),
                ("Deleted", status.deleted),
                ("Untracked", status.untracked),
            ]:
                if files:
                    lines.append(f"{label}: {len(files)} file(s)")
                    for f in files[:5]:
                        lines.append(f"  • {f}")
                    if len(files) > 5:
                        lines.append(f"  ... and {len(files) - 5} more")
        return "\n".join(lines)

    def format_history(self, commits: List[CommitInfo]) -> str:
        if not commits:
            return "No commit history found"
        lines = ["Commit History:"]
        for c in commits:
            lines.append(f"\n{c.hash} — {c.date.strftime('%Y-%m-%d %H:%M')}")
            lines.append(f"  Author: {c.author}")
            lines.append(f"  {c.message}")
            if c.files_changed:
                stats = []
                if c.insertions:
                    stats.append(f"+{c.insertions}")
                if c.deletions:
                    stats.append(f"-{c.deletions}")
                lines.append(f"  {c.files_changed} file(s) changed {' '.join(stats)}")
        return "\n".join(lines)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _run(self, command: List[str], cwd: Path) -> Tuple[str, str]:
        """Validate and execute a git command."""
        if not command or command[0] != "git":
            raise SecurityError("Only git commands allowed")
        if len(command) < 2 or command[1] not in self.SAFE_COMMANDS:
            raise SecurityError(
                f"Unsafe git subcommand: {command[1] if len(command) > 1 else '(none)'}"
            )
        cmd_str = " ".join(command)
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, cmd_str, re.IGNORECASE):
                raise SecurityError(f"Dangerous pattern blocked: {pattern}")
        resolved_cwd = cwd.resolve()
        try:
            if not resolved_cwd.is_relative_to(self._approved):
                raise SecurityError("Repository outside approved directory")
        except ValueError:
            raise SecurityError("Invalid repository path")
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=resolved_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise GitError(f"git command failed: {stderr.decode().strip()}")
            return stdout.decode(), stderr.decode()
        except asyncio.CancelledError:
            raise
        except GitError:
            raise
        except SecurityError:
            raise
        except Exception as exc:
            raise GitError(f"Failed to execute git command: {exc}") from exc
