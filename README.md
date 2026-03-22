# Claude Remote Bot

A multi-user Telegram bot for remote Claude Code access, SSH tunnel management,
and system monitoring. Built on Python 3.12+ with python-telegram-bot 22+.

## Features

- **Claude integration** — Conversational access to Claude Code via SDK or CLI fallback.
- **Multi-user support** — Invite-token based auth with admin/user/viewer roles.
- **SSH tunnel** — ngrok lifecycle management with auto-restart and admin notifications.
- **System monitor** — CPU/RAM/disk metrics, SSH session counting, anomaly alerts.
- **File handling** — Upload code files, archives (zip/tar), and images for Claude analysis.
- **Git integration** — Safe read-only git operations within approved directories.
- **Session export** — Export chat history as Markdown, JSON, or HTML.
- **Quick actions** — Context-aware inline keyboard shortcuts.
- **WSL watchdog** — PowerShell watchdog to restart the bot if WSL dies.

## Architecture

```
src/
├── bot/            Telegram handlers, middleware, features
├── claude/         SDK + CLI bridge, session management, cost tracking
├── config/         Pydantic settings, feature flags
├── events/         Async event bus + typed events
├── monitor/        psutil metrics collector, reporter, anomaly alerts
├── notifications/  Rate-limited, dedup-aware notification delivery
├── security/       Invite auth, rate limiter, path validator, audit log
├── storage/        SQLite (WAL mode) + repositories
└── tunnel/         ngrok lifecycle manager + admin notifier
```

## Quick Start

### Prerequisites

- Python 3.12+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID (admin)
- `ngrok` binary (optional, for tunnel feature)

### Installation

```bash
git clone https://github.com/yourname/claude-remote-bot
cd claude-remote-bot
cp .env.example .env
# Edit .env — at minimum set TELEGRAM_BOT_TOKEN and ADMIN_TELEGRAM_ID
bash scripts/install.sh
```

The install script:
1. Installs Python dependencies (`pip install -e .`)
2. Initialises the SQLite database
3. Creates and starts a systemd user service
4. Registers the Windows Task Scheduler watchdog (if PowerShell is available)

### Manual start

```bash
python3 -m src.main
```

### Service management

```bash
systemctl --user status claude-remote-bot
systemctl --user restart claude-remote-bot
journalctl --user -u claude-remote-bot -f
```

## Configuration

All settings are loaded from `.env` (see `.env.example`).

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from BotFather |
| `ADMIN_TELEGRAM_ID` | Yes | Your Telegram user ID |
| `ANTHROPIC_API_KEY` | No | Anthropic API key (falls back to `claude` CLI) |
| `NGROK_AUTHTOKEN` | No | ngrok auth token (required for tunnel feature) |
| `SSH_PORT` | No | Local SSH port (default: 22) |
| `ENABLE_TUNNEL` | No | Enable ngrok tunnel manager (default: false) |
| `ENABLE_MONITOR` | No | Enable system monitor (default: true) |

## Commands

### All users

| Command | Description |
|---|---|
| `/start [token]` | Start the bot or redeem an invite token |
| `/help` | Show available commands |
| `/ping` | Check bot is alive |
| `/new` | Start a new Claude session |
| `/status` | Current system status |
| `/ssh` | SSH tunnel info |
| `/history` | Recent commands |
| `/about` | Bot info and architecture |

### Admin only

| Command | Description |
|---|---|
| `/invite` | Generate an invite token |
| `/users` | List registered users |
| `/stats` | 24h system statistics |
| `/alerts [on/off]` | Toggle hourly reports |
| `/broadcast <msg>` | Send message to all users |
| `/tunnel restart` | Force restart ngrok |
| `/sessions` | Active Claude sessions |

## Notification Anti-Spam Rules

These rules prevent notification floods:

1. **State-change only** — only notify when status actually changes.
2. **Dedup window** — same message type suppressed for 5 minutes.
3. **Hourly reports** — default OFF; admin enables with `/alerts on`.
4. **Tunnel** — only notifies on up→down, down→up, and retry-exhausted.
5. **Anomaly alerts** — fire only on first threshold crossing, not while sustained.
6. **Bot start** — sends ONE status message total, not per-module.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=src --cov-report=term-missing

# Format
black src tests
isort src tests

# Lint
flake8 src tests
mypy src
```

## WSL Watchdog

`scripts/wsl-watchdog.ps1` is a PowerShell script intended for Windows Task Scheduler.
It checks that the WSL distro and the bot process are running, restarts them if not,
and sends a Telegram alert on failure.

Required Windows environment variables (set by `install.sh`):
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_CHAT_ID`
- `WSL_DISTRO_NAME`

## License

MIT
