#!/bin/bash
# Claude Remote Bot — Single Instance Runner
# Ensures only one instance runs at a time via PID lock

LOCK_FILE="/tmp/claude-telegram-bot.lock"
LOG_FILE="/mnt/c/dev/claude-telegram-bot/data/bot.log"
BOT_DIR="/mnt/c/dev/claude-telegram-bot"
VENV="$BOT_DIR/.venv/bin/python3"

mkdir -p "$BOT_DIR/data"

# Check for existing instance
if [ -f "$LOCK_FILE" ]; then
    OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Bot already running (PID $OLD_PID). Exiting."
        exit 0
    else
        echo "Stale lock file found. Removing."
        rm -f "$LOCK_FILE"
    fi
fi

# Write PID lock
echo $$ > "$LOCK_FILE"

# Cleanup on exit
cleanup() {
    rm -f "$LOCK_FILE"
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
