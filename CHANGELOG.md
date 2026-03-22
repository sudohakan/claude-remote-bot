# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-03-22

### Added

**Core bot**
- Multi-user Telegram bot with invite-token authentication
- Admin / user / viewer role hierarchy
- Context-aware inline keyboard quick actions
- `/start`, `/help`, `/ping`, `/new`, `/status`, `/ssh`, `/history`, `/about` commands
- Admin-only: `/invite`, `/users`, `/stats`, `/alerts`, `/broadcast`, `/tunnel`, `/sessions`

**Claude integration**
- `ClaudeSDKRunner` — SDK-first execution with automatic `claude` CLI fallback
- `SessionManager` — multi-turn conversation history with configurable timeout
- `CostTracker` — per-user and per-request spend caps
- `CredentialSanitizer` — strips secrets from all Claude output before delivery
- Agentic mode with configurable max turns and per-request timeout
- Session export as Markdown, JSON, or HTML

**Security**
- Invite-token based access control (`AccessManager`)
- Three-layer rate limiting: Claude requests, commands, invite generation
- Path validator restricting git operations to approved directories
- Full audit log persisted to SQLite

**System monitor**
- `MetricsCollector` — CPU, RAM, disk via psutil; SSH session counting
- `AlertManager` — threshold-based anomaly detection with state-change-only firing
- Configurable alert thresholds via environment variables

**SSH tunnel**
- `TunnelManager` — ngrok lifecycle management (start, health poll, auto-restart)
- `TunnelNotifier` — admin alerts on state transitions only
- Configurable retry count and poll interval

**Notification service**
- Rate-limited, dedup-aware delivery
- 5-minute dedup window per message type
- Hourly reports default OFF, toggled by admin with `/alerts on`

**Storage**
- SQLite in WAL mode via `aiosqlite`
- Repository pattern for users, sessions, audit log
- `StorageFacade` — unified async interface

**Event system**
- Async typed event bus
- Decoupled communication between tunnel, monitor, and notification modules

**Infrastructure**
- Pydantic v2 settings with full validation
- Feature flags for tunnel, monitor, API server, file uploads, git integration, voice messages
- `structlog` structured logging
- FastAPI HTTP server (optional, disabled by default)
- `scripts/install.sh` — automated setup: deps, database, systemd service, watchdog
- `scripts/wsl-watchdog.ps1` — Windows Task Scheduler watchdog for WSL resilience
- `pyproject.toml` with Poetry, Black, isort, flake8, mypy, pytest configuration
- 8 pytest test files covering auth, bot core, Claude bridge, config, monitor, storage, tunnel, validators

---

[1.0.0]: https://github.com/sudohakan/claude-remote-bot/releases/tag/v1.0.0
