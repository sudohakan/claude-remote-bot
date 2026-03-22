"""Async SQLite connection manager with WAL mode and schema migrations.

Design choices:
- WAL journal mode for better concurrent read performance
- Connection pool (5 connections default)
- Versioned migrations — never destructive
- Explicit datetime adapters to avoid Python 3.12 deprecation warnings
"""

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, List, Tuple

import aiosqlite
import structlog

logger = structlog.get_logger(__name__)

# Register explicit adapters so datetime round-trips cleanly in Python 3.12+
sqlite3.register_adapter(datetime, lambda v: v.isoformat())
sqlite3.register_converter("TIMESTAMP", lambda b: datetime.fromisoformat(b.decode()))
sqlite3.register_converter("DATETIME", lambda b: datetime.fromisoformat(b.decode()))

_SCHEMA_V1 = """
-- Users with role + access level
CREATE TABLE users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    role        TEXT NOT NULL DEFAULT 'user'
                CHECK (role IN ('admin', 'user', 'viewer')),
    access_level TEXT NOT NULL DEFAULT 'sandbox'
                CHECK (access_level IN ('sandbox', 'project', 'full')),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    total_cost  REAL NOT NULL DEFAULT 0.0,
    message_count INTEGER NOT NULL DEFAULT 0
);

-- Invite tokens (8-char, 24-hour expiry)
CREATE TABLE invites (
    token       TEXT PRIMARY KEY,
    created_by  INTEGER NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP NOT NULL,
    redeemed_by INTEGER,
    redeemed_at TIMESTAMP,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    FOREIGN KEY (created_by) REFERENCES users(user_id)
);

-- Per-user Claude sessions
CREATE TABLE claude_sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    working_dir   TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_turns   INTEGER NOT NULL DEFAULT 0,
    total_cost    REAL NOT NULL DEFAULT 0.0,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Command audit log (30-day rolling)
CREATE TABLE command_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    command     TEXT NOT NULL,
    args        TEXT,
    result      TEXT NOT NULL DEFAULT 'ok'
                CHECK (result IN ('ok', 'error', 'denied')),
    logged_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Performance indexes
CREATE INDEX idx_invites_token      ON invites(token);
CREATE INDEX idx_invites_created_by ON invites(created_by);
CREATE INDEX idx_sessions_user      ON claude_sessions(user_id, is_active);
CREATE INDEX idx_command_log_user   ON command_log(user_id, logged_at);
CREATE INDEX idx_command_log_time   ON command_log(logged_at);
"""

_SCHEMA_V2 = """
-- Enable WAL mode for better concurrent writes
PRAGMA journal_mode=WAL;
"""


class DatabaseManager:
    """Manage SQLite connections with a simple pool."""

    _POOL_SIZE = 5

    def __init__(self, database_url: str) -> None:
        self.db_path = self._parse_url(database_url)
        self._pool: List[aiosqlite.Connection] = []
        self._lock = asyncio.Lock()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create DB file, run migrations, fill connection pool."""
        logger.info("Initializing database", path=str(self.db_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await self._run_migrations()
        await self._fill_pool()
        logger.info("Database ready")

    async def close(self) -> None:
        """Close all pooled connections."""
        async with self._lock:
            for conn in self._pool:
                await conn.close()
            self._pool.clear()
        logger.info("Database connections closed")

    async def health_check(self) -> bool:
        """Return True if database responds to a simple query."""
        try:
            async with self.get_connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception as exc:
            logger.error("Database health check failed", error=str(exc))
            return False

    # ── Connection management ─────────────────────────────────────────────────

    @asynccontextmanager
    async def get_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield a connection from the pool (or create an overflow connection)."""
        async with self._lock:
            if self._pool:
                conn = self._pool.pop()
            else:
                conn = await self._new_connection()

        try:
            yield conn
        finally:
            async with self._lock:
                if len(self._pool) < self._POOL_SIZE:
                    self._pool.append(conn)
                else:
                    await conn.close()

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_url(url: str) -> Path:
        if url.startswith("sqlite:///"):
            return Path(url[10:])
        if url.startswith("sqlite://"):
            return Path(url[9:])
        return Path(url)

    async def _new_connection(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        )
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def _fill_pool(self) -> None:
        async with self._lock:
            for _ in range(self._POOL_SIZE):
                self._pool.append(await self._new_connection())

    async def _run_migrations(self) -> None:
        """Apply pending schema migrations."""
        async with aiosqlite.connect(
            self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
        ) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON")
            current = await self._schema_version(conn)
            logger.info("Schema version", current=current)
            for version, sql in self._migrations():
                if version > current:
                    logger.info("Applying migration", version=version)
                    await conn.executescript(sql)
                    await conn.execute(
                        "INSERT INTO schema_version (version) VALUES (?)", (version,)
                    )
            await conn.commit()

    async def _schema_version(self, conn: aiosqlite.Connection) -> int:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
        )
        cursor = await conn.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else 0

    @staticmethod
    def _migrations() -> List[Tuple[int, str]]:
        return [
            (1, _SCHEMA_V1),
            (2, _SCHEMA_V2),
        ]
