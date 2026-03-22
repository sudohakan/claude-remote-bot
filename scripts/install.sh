#!/usr/bin/env bash
# install.sh — Set up claude-remote-bot on WSL / Ubuntu
#
# Usage: bash scripts/install.sh
#
# What this script does:
#   1. Installs Python dependencies (pip install -e .)
#   2. Creates data/ directory and initialises the SQLite DB
#   3. Creates a systemd user service and enables it
#   4. (Optional) Registers the Windows Task Scheduler watchdog via PowerShell
#   5. (Optional) Sets Windows environment variables for the watchdog

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_NAME="claude-remote-bot"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
DATA_DIR="${PROJECT_DIR}/data"
ENV_FILE="${PROJECT_DIR}/.env"

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Pre-flight ────────────────────────────────────────────────────────────────

info "Installing claude-remote-bot..."
cd "$PROJECT_DIR"

[[ -f "$ENV_FILE" ]] || error ".env file not found at $ENV_FILE. Copy .env.example and fill in values."

command -v python3 &>/dev/null || error "python3 is required but not found."

# ── Step 1: Python dependencies ───────────────────────────────────────────────

info "Installing Python dependencies..."
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet -e .
info "Dependencies installed."

# ── Step 2: Data directory + DB initialisation ────────────────────────────────

info "Creating data directory..."
mkdir -p "$DATA_DIR"

info "Initialising SQLite database..."
python3 - <<'PYEOF'
import asyncio, sys, pathlib
sys.path.insert(0, ".")
from src.storage.facade import StorageFacade
from src.config.settings import Settings

async def init():
    s = Settings()
    db = StorageFacade(s.database_url)
    await db.initialize()
    await db.close()
    print("Database initialised at:", s.database_path)

asyncio.run(init())
PYEOF

# ── Step 3: Systemd user service ─────────────────────────────────────────────

info "Creating systemd user service..."
mkdir -p "$HOME/.config/systemd/user"

PYTHON_BIN="$(which python3)"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Claude Remote Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${PYTHON_BIN} -m src.main
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

info "Reloading systemd and enabling service..."
systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
systemctl --user start  "$SERVICE_NAME"

STATUS=$(systemctl --user is-active "$SERVICE_NAME" 2>/dev/null || echo "unknown")
info "Service status: $STATUS"

# ── Step 4: Windows Task Scheduler (optional — requires PowerShell) ───────────

WATCHDOG_PS="${SCRIPT_DIR}/wsl-watchdog.ps1"

if command -v powershell.exe &>/dev/null || command -v pwsh &>/dev/null; then
    info "PowerShell detected — registering Windows Task Scheduler watchdog..."

    PS_BIN="$(command -v powershell.exe 2>/dev/null || command -v pwsh)"

    # Convert WSL path to Windows path
    WIN_WATCHDOG=$(wslpath -w "$WATCHDOG_PS" 2>/dev/null || echo "$WATCHDOG_PS")

    $PS_BIN -NoProfile -NonInteractive -Command "
\$action  = New-ScheduledTaskAction -Execute 'PowerShell.exe' \
    -Argument '-NonInteractive -File \"${WIN_WATCHDOG}\"'
\$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) \
    -Once -At (Get-Date)
\$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew
\$principal = New-ScheduledTaskPrincipal -UserId \$env:USERNAME -RunLevel Highest

Register-ScheduledTask -TaskName 'ClaudeRemoteBotWatchdog' \
    -Action \$action -Trigger \$trigger \
    -Settings \$settings -Principal \$principal \
    -Description 'WSL watchdog for claude-remote-bot' \
    -Force | Out-Null

Write-Host 'Task Scheduler watchdog registered.'
" 2>/dev/null || warn "Failed to register Windows Task Scheduler job (non-fatal)."

    # ── Step 5: Windows env vars for the watchdog ──────────────────────────────
    if [[ -f "$ENV_FILE" ]]; then
        BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'" || true)
        ADMIN_ID=$(grep -E '^ADMIN_TELEGRAM_ID=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'" || true)
        DISTRO=$(wsl.exe -l --running 2>/dev/null | head -2 | tail -1 | tr -d '\r' || echo "Ubuntu")

        if [[ -n "$BOT_TOKEN" && -n "$ADMIN_ID" ]]; then
            $PS_BIN -NoProfile -NonInteractive -Command "
[System.Environment]::SetEnvironmentVariable('TELEGRAM_BOT_TOKEN',    '$BOT_TOKEN', 'User')
[System.Environment]::SetEnvironmentVariable('TELEGRAM_ADMIN_CHAT_ID', '$ADMIN_ID', 'User')
[System.Environment]::SetEnvironmentVariable('WSL_DISTRO_NAME',        '$DISTRO',   'User')
Write-Host 'Windows environment variables set.'
" 2>/dev/null || warn "Failed to set Windows env vars (non-fatal)."
        else
            warn "Could not extract TELEGRAM_BOT_TOKEN or ADMIN_TELEGRAM_ID from .env — skipping Windows env var setup."
        fi
    fi
else
    warn "PowerShell not found — skipping Windows Task Scheduler registration."
    info "To register the watchdog manually, run scripts/wsl-watchdog.ps1 from PowerShell."
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
info "Installation complete!"
echo ""
echo "  Service:   systemctl --user status $SERVICE_NAME"
echo "  Logs:      journalctl --user -u $SERVICE_NAME -f"
echo "  Stop:      systemctl --user stop $SERVICE_NAME"
echo "  Restart:   systemctl --user restart $SERVICE_NAME"
echo ""
