"""System status reporter.

Formats metrics into human-readable Telegram messages:
- /status  — current snapshot (all users)
- /stats   — 24h admin summary
- hourly   — admin opt-in hourly digest (default OFF)
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from .collector import Metrics, MetricsCollector


class StatusReporter:
    """Format system metrics into Telegram-ready messages."""

    def __init__(self, collector: MetricsCollector) -> None:
        self._collector = collector

    # ── /status ───────────────────────────────────────────────────────────────

    def format_status(self, m: Optional[Metrics] = None) -> str:
        """Return /status response (concise, all users)."""
        if m is None:
            m = self._collector.get_latest()
        if m is None:
            return "No metrics collected yet. System monitor may still be starting."

        lines = [
            "<b>System Status</b>",
            "",
            f"CPU:  {m.cpu_percent:.1f}%",
            f"RAM:  {m.ram_percent:.1f}%  ({m.ram_used_mb:.0f} / {m.ram_total_mb:.0f} MB)",
            f"Disk: {m.disk_percent:.1f}%  ({m.disk_used_gb:.1f} / {m.disk_total_gb:.1f} GB)",
            f"Tunnel: {m.tunnel_status}",
            f"SSH sessions: {m.ssh_sessions}",
            f"Claude sessions: {m.claude_sessions}",
        ]
        if m.timestamp:
            try:
                ts = datetime.fromisoformat(m.timestamp).strftime("%H:%M:%S UTC")
                lines.append(f"\nLast updated: {ts}")
            except ValueError:
                pass
        return "\n".join(lines)

    # ── /stats ────────────────────────────────────────────────────────────────

    def format_stats(self) -> str:
        """Return /stats response — 24h admin summary."""
        history = self._collector.get_history(hours=24)
        if not history:
            return "No historical data available yet."

        cpu_values = [s.get("cpu_percent", 0.0) for s in history]
        ram_values = [s.get("ram_percent", 0.0) for s in history]

        cpu_avg = sum(cpu_values) / len(cpu_values)
        cpu_max = max(cpu_values)
        ram_avg = sum(ram_values) / len(ram_values)
        ram_max = max(ram_values)

        ssh_totals = [s.get("ssh_sessions", 0) for s in history]
        ssh_max = max(ssh_totals)

        lines = [
            "<b>24h Statistics</b>",
            "",
            f"CPU  — avg: {cpu_avg:.1f}%  max: {cpu_max:.1f}%",
            f"RAM  — avg: {ram_avg:.1f}%  max: {ram_max:.1f}%",
            f"SSH sessions peak: {ssh_max}",
            f"Samples collected: {len(history)}",
        ]
        return "\n".join(lines)

    # ── Hourly report ─────────────────────────────────────────────────────────

    def format_hourly_report(self) -> str:
        """Return an hourly digest (admin opt-in, default OFF)."""
        m = self._collector.get_latest()
        if m is None:
            return "Hourly report: no data available."

        history_1h = self._collector.get_history(hours=1)
        cpu_values = [s.get("cpu_percent", 0.0) for s in history_1h]
        cpu_avg = sum(cpu_values) / len(cpu_values) if cpu_values else 0.0

        lines = [
            "<b>Hourly Report</b>",
            "",
            f"CPU avg (1h): {cpu_avg:.1f}%  current: {m.cpu_percent:.1f}%",
            f"RAM: {m.ram_percent:.1f}%",
            f"Disk: {m.disk_percent:.1f}%",
            f"Tunnel: {m.tunnel_status}",
            f"SSH sessions: {m.ssh_sessions}",
        ]
        return "\n".join(lines)
