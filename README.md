<div align="center">

<img src="https://img.shields.io/badge/Claude%20Remote%20Bot-Telegram%20%C3%97%20Claude%20Code-blueviolet?style=for-the-badge&logo=telegram&logoColor=white" alt="Claude Remote Bot" />

# Claude Remote Bot

**Secure multi-user remote access to Claude Code via Telegram.**

SDK-first AI bridge · invite-token auth · SSH tunnel management · system monitoring · cost controls

[![Version](https://img.shields.io/badge/version-1.0.0-blue?style=flat-square)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.12%2B-brightgreen?style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/sudohakan/claude-remote-bot/ci.yml?style=flat-square&label=CI)](https://github.com/sudohakan/claude-remote-bot/actions)
[![Stars](https://img.shields.io/github/stars/sudohakan/claude-remote-bot?style=flat-square)](https://github.com/sudohakan/claude-remote-bot/stargazers)

[Quick Start](#quick-start) · [Features](#features) · [Commands](#commands) · [Architecture](#architecture) · [Configuration](#configuration) · [Contributing](#contributing)

</div>

---

## Why Claude Remote Bot?

> Send a message from your phone, get Claude Code responses back. Manage SSH tunnels, monitor your system, upload files for analysis, export sessions: all from Telegram, with role-based access and cost controls baked in.

| What you get | Details |
|:---|:---|
| **Claude Agent SDK integration** | SDK-first with automatic CLI fallback; multi-turn agentic mode with configurable turn limits |
| **Multi-user auth** | Invite-token system with admin / user / viewer roles and full audit log |
| **SSH tunnel management** | ngrok lifecycle with health polling, auto-restart, retry limits, and admin alerts |
| **System monitoring** | CPU, RAM, disk metrics via psutil; anomaly detection with configurable thresholds |
| **File and image uploads** | Send code, archives (zip/tar), and images directly to Claude for analysis |
| **Cost tracking** | Per-request and per-user monthly caps enforced mid-stream |
| **Session export** | Export chat history as Markdown, JSON, or HTML |
| **Credential sanitizer** | All Claude output is scrubbed for AWS keys, GitHub tokens, secrets before delivery |
| **Anti-spam notifications** | State-change-only alerts, dedup windows, optional hourly reports |
| **WSL watchdog** | PowerShell watchdog restarts the bot if WSL or the process dies |

---

## Quick Start

**1. Create a Telegram bot**

Message [@BotFather](https://t.me/BotFather) on Telegram, run `/newbot`, copy the token.
Get your user ID from [@userinfobot](https://t.me/userinfobot).

**2. Clone and configure**

```bash
git clone https://github.com/sudohakan/claude-remote-bot.git
cd claude-remote-bot
cp .env.example .env
# Set TELEGRAM_BOT_TOKEN and ADMIN_TELEGRAM_ID
```

**3. Install and start**

```bash
bash scripts/install.sh
```

The script installs dependencies, initializes SQLite, creates a systemd user service, and registers the Windows Task Scheduler watchdog.

<details>
<summary><strong>Manual install</strong></summary>

```bash
pip install -e .
python3 -m src.main
```

</details>

<details>
<summary><strong>Development install</strong></summary>

```bash
pip install -e ".[dev]"
pytest
```

Includes: pytest, black, isort, flake8, mypy.

</details>

<details>
<summary><strong>Service management</strong></summary>

```bash
systemctl --user status claude-remote-bot
systemctl --user restart claude-remote-bot
journalctl --user -u claude-remote-bot -f
```

</details>

---

## Features

### Claude Integration

SDK-first architecture with automatic CLI fallback. When `ANTHROPIC_API_KEY` is set, the bot uses the Claude Agent SDK directly. Otherwise, it shells out to the `claude` CLI. Multi-turn agentic mode is enabled by default with configurable turn limits and timeouts.

### Multi-User Authentication

Invite-token system: admin generates single-use tokens via `/invite`, users redeem with `/start <token>`. Three roles:

| Role | Access |
|:-----|:-------|
| `admin` | Full control: user management, broadcasts, tunnel ops, system stats |
| `user` | Chat with Claude, view status, manage own sessions, upload files |
| `viewer` | Read-only: status, history |

All commands are audit-logged to SQLite with timestamp, user ID, and action.

### Cost Controls

Two independent caps enforced by `CostTracker`:
- **Per-request**: rejects mid-stream if `CLAUDE_MAX_COST_PER_REQUEST` exceeded
- **Per-user monthly**: blocks the user until monthly reset

### System Monitoring

psutil-based metrics collector with configurable thresholds:
- CPU, RAM, disk usage with anomaly alerts
- SSH session counting and failure rate tracking
- Optional hourly reports (default off, enable via `/alerts on`)
- State-change-only alerting: fires on transition, not while sustained

### SSH Tunnel Management

Full ngrok lifecycle: start, health polling, auto-restart on failure, configurable retry limits. Admin receives Telegram alerts on status transitions (up/down/retry-exhausted).

### File and Image Processing

Upload code files, zip/tar archives, and images directly in Telegram. The bot forwards them to Claude for analysis with full context.

### Notification Anti-Spam

Silent by default. State-change-only firing, 5-minute dedup windows, one startup message total (not one per module). Hourly reports are opt-in.

---

## Commands

### All Users

| Command | Description |
|:--------|:------------|
| `/start [token]` | Start the bot or redeem an invite token |
| `/help` | Show available commands |
| `/ping` | Health check |
| `/new` | Start a new Claude session |
| `/status` | System status (CPU, RAM, disk) |
| `/ssh` | SSH tunnel info and connection details |
| `/history` | Recent command history |
| `/about` | Bot info and version |

### Admin Only

| Command | Description |
|:--------|:------------|
| `/invite` | Generate a single-use invite token |
| `/users` | List all registered users |
| `/stats` | 24-hour system statistics |
| `/alerts [on\|off]` | Toggle hourly system reports |
| `/broadcast <msg>` | Message all users |
| `/tunnel restart` | Force-restart ngrok tunnel |
| `/sessions` | List active Claude sessions |

---

## Architecture

```
claude-remote-bot/
├── src/
│   ├── main.py                  # Entry point
│   ├── bot/
│   │   ├── handlers/            # Command, message, callback handlers
│   │   ├── middleware/           # Auth and rate limiting middleware
│   │   ├── features/            # File, image, voice, git, session export, quick actions
│   │   ├── orchestrator.py      # Service wiring
│   │   └── core.py              # Bot lifecycle
│   ├── claude/                  # SDK + CLI bridge, session management, cost tracking
│   ├── config/                  # Pydantic settings, feature flags
│   ├── events/                  # Async typed event bus
│   ├── monitor/                 # psutil collector, reporter, anomaly alerts
│   ├── notifications/           # Rate-limited, dedup-aware delivery
│   ├── security/                # Invite auth, rate limiter, path validator, audit log
│   ├── storage/                 # SQLite (WAL mode) + repository pattern
│   └── tunnel/                  # ngrok lifecycle manager + admin notifier
├── tests/                       # 10 test files
├── scripts/
│   ├── install.sh               # Automated setup
│   ├── run-bot.sh               # Run script
│   └── wsl-watchdog.ps1         # Windows Task Scheduler watchdog
└── pyproject.toml
```

```mermaid
graph TB
    subgraph Telegram["Telegram Interface"]
        TG[Bot Handlers<br/>commands + messages + inline]
        MW[Middleware<br/>auth + rate limiting]
    end

    subgraph Core["Core Services"]
        CF[Claude Facade<br/>SDK + CLI bridge]
        SM[Session Manager<br/>multi-turn history]
        CT[Cost Tracker<br/>per-user spend]
        EB[Event Bus<br/>async typed events]
    end

    subgraph Optional["Optional Modules"]
        TM[Tunnel Manager<br/>ngrok lifecycle]
        MC[Metrics Collector<br/>CPU + RAM + disk]
        AM[Alert Manager<br/>anomaly detection]
        NS[Notification Service<br/>rate-limited delivery]
    end

    subgraph Storage["Persistence"]
        DB[(SQLite WAL<br/>users + sessions + audit)]
    end

    TG --> MW
    MW --> CF
    CF --> SM
    CF --> CT
    CF --> EB
    EB --> TM
    EB --> MC
    MC --> AM
    AM --> NS
    NS --> TG
    Core --> DB
```

---

## Configuration

All settings loaded from `.env`. Copy `.env.example` to get started.

### Required

| Variable | Description |
|:---------|:------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_TELEGRAM_ID` | Your Telegram user ID |

### Claude / Anthropic

| Variable | Default | Description |
|:---------|:-------:|:------------|
| `ANTHROPIC_API_KEY` | -- | API key. Falls back to `claude` CLI if unset |
| `CLAUDE_MODEL` | SDK default | Model override (e.g. `claude-sonnet-4-20250514`) |
| `CLAUDE_MAX_TURNS` | `10` | Max agentic turns per request |
| `CLAUDE_TIMEOUT_SECONDS` | `120` | Per-request timeout |
| `CLAUDE_MAX_COST_PER_USER` | `5.0` | Monthly cost cap per user (USD) |
| `CLAUDE_MAX_COST_PER_REQUEST` | `1.0` | Per-request cost cap (USD) |
| `AGENTIC_MODE` | `true` | Multi-turn agentic execution |

### Rate Limiting

| Variable | Default | Description |
|:---------|:-------:|:------------|
| `RATE_LIMIT_CLAUDE_PER_MIN` | `20` | Claude requests per minute per user |
| `RATE_LIMIT_COMMANDS_PER_MIN` | `5` | Bot commands per minute per user |
| `RATE_LIMIT_INVITES_PER_HOUR` | `3` | Invite tokens per hour |

### SSH Tunnel (ngrok)

| Variable | Default | Description |
|:---------|:-------:|:------------|
| `ENABLE_TUNNEL` | `false` | Enable ngrok tunnel manager |
| `NGROK_AUTHTOKEN` | -- | ngrok auth token |
| `SSH_PORT` | `22` | Local SSH port to expose |
| `TUNNEL_POLL_INTERVAL_SECONDS` | `30` | Health check interval |
| `TUNNEL_MAX_RETRIES` | `5` | Restart attempts before giving up |

### System Monitor

| Variable | Default | Description |
|:---------|:-------:|:------------|
| `ENABLE_MONITOR` | `true` | Enable metrics collector |
| `MONITOR_COLLECT_INTERVAL_SECONDS` | `60` | Collection interval |
| `HOURLY_REPORT_ENABLED` | `false` | Scheduled hourly reports |
| `ALERT_CPU_PERCENT` | `90` | CPU alert threshold |
| `ALERT_RAM_PERCENT` | `85` | RAM alert threshold |
| `ALERT_DISK_PERCENT` | `90` | Disk alert threshold |

### Feature Flags

| Variable | Default | Description |
|:---------|:-------:|:------------|
| `ENABLE_FILE_UPLOADS` | `true` | Allow file uploads to Claude |
| `ENABLE_GIT_INTEGRATION` | `true` | Read-only git commands |
| `ENABLE_API_SERVER` | `false` | FastAPI HTTP server |
| `ENABLE_VOICE_MESSAGES` | `false` | Voice message processing |

---

## Security

- **Invite-token auth**: no token, no access. Single-use, admin-generated.
- **Role hierarchy**: admin > user > viewer. Enforced at middleware level.
- **Audit log**: every command and Claude interaction logged to SQLite.
- **Rate limiting**: three independent sliding windows (Claude, commands, invites).
- **Path safety**: git integration restricted to approved directory allowlist. No traversal, no symlink escapes.
- **Credential sanitizer**: Claude output scrubbed for AWS keys, GitHub tokens, private key blocks, common secret patterns.
- **Cost controls**: per-request and per-user monthly caps, enforced mid-stream.

---

## Tech Stack

| Component | Technology |
|:----------|:-----------|
| Runtime | Python 3.12+ |
| Telegram | python-telegram-bot 22 (async, rate-limiter extras) |
| AI | Claude Agent SDK + Anthropic API |
| Database | SQLite (WAL mode) via aiosqlite |
| Settings | Pydantic Settings + python-dotenv |
| Monitoring | psutil |
| HTTP server | FastAPI + Uvicorn (optional) |
| Scheduling | APScheduler |
| Logging | structlog |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

```bash
git clone https://github.com/sudohakan/claude-remote-bot.git
cd claude-remote-bot
pip install -e ".[dev]"
pytest
```

---

## License

[MIT](LICENSE) : 2026 Hakan Topcu

<div align="center">

Built with Python  ·  Powered by [Claude Agent SDK](https://github.com/anthropics/claude-code)  ·  Delivered via [Telegram](https://telegram.org)

</div>
