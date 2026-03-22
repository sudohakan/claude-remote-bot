# claude-remote-bot

Telegram bot for remote Claude Code access with SSH tunnel management and system monitoring.

## Tech Stack

- Python 3.12+, python-telegram-bot 22+
- anthropic SDK + claude-agent-sdk
- aiosqlite (WAL mode), pydantic-settings, structlog
- psutil (system monitoring), aiohttp (ngrok API)

## Project Layout

- `src/config/` — Pydantic settings + feature flags
- `src/storage/` — SQLite database, repositories, facade
- `src/security/` — Invite auth, rate limiting, path validation, audit log
- `src/claude/` — SDK integration, session management, cost tracking, sanitizer
- `src/bot/` — Telegram bot core, handlers, middleware, utilities
- `src/tunnel/` — ngrok lifecycle manager + notifications
- `src/monitor/` — psutil metrics, system alerts, reporting
- `src/events/` — Event bus for decoupled module communication
- `src/notifications/` — Telegram notification service
- `tests/` — pytest tests (asyncio_mode=auto)

## Dev Commands

```bash
make install-dev   # install deps
make test          # run tests
make lint          # flake8 + mypy
make format        # black + isort
make run           # start bot
```

## Auth Model

- Admin: hardcoded ADMIN_TELEGRAM_ID in .env
- Users: invite token flow (/invite generates 8-char token, /start <token> redeems)
- Roles: admin / user / viewer
- Access levels: sandbox / project / full

## Notification Rules

1. State-change only — never repeat same status
2. 5-minute dedup window per message type
3. Hourly reports default OFF (admin enables via /alerts on)
4. Tunnel: notify only on state transitions
5. Anomaly alerts: only on threshold crossing, not while sustained
