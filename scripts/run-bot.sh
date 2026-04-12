#!/bin/bash
# Claude Remote Bot — Single Instance Runner
# Ensures only one instance runs at a time via PID lock

LOG_FILE="/mnt/c/dev/claude-telegram-bot/data/bot.log"
BOT_DIR="/mnt/c/dev/claude-telegram-bot"
VENV="$BOT_DIR/.venv/bin/python3"

mkdir -p "$BOT_DIR/data"

# PM2 manages single-instance — no lock file needed.
# Lock file mechanism removed: caused infinite restart loop (14.8M restarts)
# when PM2 and a direct-started instance conflicted on the same lock.

# Cleanup on exit
cleanup() {
    echo "[$(date)] Bot stopped." >> "$LOG_FILE"
}
trap cleanup EXIT INT TERM

# Start SSH server if not running
if ! pgrep -x sshd > /dev/null 2>&1; then
    sudo service ssh start 2>/dev/null
fi

# Run bot
echo "[$(date)] Bot starting (PID $$)..." >> "$LOG_FILE"
cd "$BOT_DIR"
exec "$VENV" -m src.main >> "$LOG_FILE" 2>&1
