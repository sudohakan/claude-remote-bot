#Requires -Version 5.1
<#
.SYNOPSIS
    WSL watchdog for claude-remote-bot.

.DESCRIPTION
    Runs as a Windows Task Scheduler job. Checks that:
    1. The WSL distro is running.
    2. The bot process is alive inside WSL.

    If either check fails:
    - Sends a Telegram admin notification.
    - Attempts to restart WSL (up to MAX_RESTART_ATTEMPTS times).

.NOTES
    Required environment variables (set by install.sh or manually):
      TELEGRAM_BOT_TOKEN       - Bot API token
      TELEGRAM_ADMIN_CHAT_ID   - Admin Telegram user ID
      WSL_DISTRO_NAME          - WSL distro (default: Ubuntu)
#>

[CmdletBinding()]
param(
    [int]$MaxRestartAttempts = 5,
    [int]$RetryIntervalSeconds = 300
)

# ── Configuration ──────────────────────────────────────────────────────────────

$BotToken    = $env:TELEGRAM_BOT_TOKEN
$AdminChatId = $env:TELEGRAM_ADMIN_CHAT_ID
$DistroName  = if ($env:WSL_DISTRO_NAME) { $env:WSL_DISTRO_NAME } else { "Ubuntu" }
$BotProcess  = "claude_telegram_bot"
$LogFile     = "$env:TEMP\wsl-watchdog.log"

# ── Helpers ────────────────────────────────────────────────────────────────────

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

function Send-TelegramAlert {
    param([string]$Message)

    if (-not $BotToken -or -not $AdminChatId) {
        Write-Log "TELEGRAM_BOT_TOKEN or TELEGRAM_ADMIN_CHAT_ID not set — skipping notification" "WARN"
        return
    }

    $url  = "https://api.telegram.org/bot$BotToken/sendMessage"
    $body = @{
        chat_id = $AdminChatId
        text    = $Message
        parse_mode = "HTML"
    }

    try {
        $response = Invoke-RestMethod -Uri $url -Method Post -Body $body -ErrorAction Stop
        Write-Log "Telegram alert sent (ok=$($response.ok))"
    } catch {
        Write-Log "Failed to send Telegram alert: $_" "ERROR"
    }
}

function Test-WslRunning {
    $running = & wsl -l --running 2>&1
    return ($running -join "") -match $DistroName
}

function Test-BotRunning {
    try {
        $result = & wsl -d $DistroName -e pgrep -f $BotProcess 2>&1
        return ($LASTEXITCODE -eq 0) -and ($result -match '\d+')
    } catch {
        return $false
    }
}

function Start-BotInWsl {
    Write-Log "Starting bot inside WSL..."
    & wsl -d $DistroName -e bash -c "
        source ~/.profile 2>/dev/null || true
        cd ~/claude-telegram-bot 2>/dev/null || cd ~/projects/claude-telegram-bot 2>/dev/null || true
        nohup python3 -m src.main >> ~/.bot.log 2>&1 &
        disown
    " 2>&1 | ForEach-Object { Write-Log $_ }
}

function Restart-Wsl {
    param([int]$Attempt)
    Write-Log "Restarting WSL (attempt $Attempt / $MaxRestartAttempts)..."
    & wsl --shutdown 2>&1 | Out-Null
    Start-Sleep -Seconds 5
    & wsl -d $DistroName -- echo "WSL started" 2>&1 | Out-Null

    if ($LASTEXITCODE -eq 0) {
        Write-Log "WSL restarted successfully"
        return $true
    }
    Write-Log "WSL failed to start (exit $LASTEXITCODE)" "ERROR"
    return $false
}

# ── Main watchdog loop ────────────────────────────────────────────────────────

Write-Log "Watchdog started (distro=$DistroName, maxAttempts=$MaxRestartAttempts)"

$attempts = 0

while ($true) {
    $wslRunning = Test-WslRunning
    $botRunning = $wslRunning -and (Test-BotRunning)

    if ($botRunning) {
        Write-Log "Bot is running — all OK"
        break
    }

    if (-not $wslRunning) {
        Write-Log "WSL distro '$DistroName' is not running" "WARN"
        Send-TelegramAlert "<b>WSL Watchdog</b>: WSL distro <code>$DistroName</code> is not running. Attempting restart..."
    } else {
        Write-Log "Bot process not found inside WSL" "WARN"
        # Try to start the bot without restarting WSL
        Start-BotInWsl
        Start-Sleep -Seconds 5
        if (Test-BotRunning) {
            Write-Log "Bot restarted successfully inside WSL"
            Send-TelegramAlert "<b>WSL Watchdog</b>: Bot process was dead — restarted successfully."
            break
        }
        Write-Log "Bot failed to start — will restart WSL" "WARN"
    }

    $attempts++
    if ($attempts -gt $MaxRestartAttempts) {
        $msg = "<b>WSL Watchdog</b>: Failed to recover after $MaxRestartAttempts attempts. Manual intervention required."
        Write-Log $msg "ERROR"
        Send-TelegramAlert $msg
        exit 1
    }

    $restarted = Restart-Wsl -Attempt $attempts
    if ($restarted) {
        Start-Sleep -Seconds 10
        Start-BotInWsl
        Start-Sleep -Seconds 5

        if (Test-BotRunning) {
            Write-Log "Recovery successful after $attempts attempt(s)"
            Send-TelegramAlert "<b>WSL Watchdog</b>: Recovered after $attempts attempt(s). Bot is running."
            break
        }
    }

    Write-Log "Waiting $RetryIntervalSeconds seconds before next attempt..."
    Start-Sleep -Seconds $RetryIntervalSeconds
}

Write-Log "Watchdog exiting"
