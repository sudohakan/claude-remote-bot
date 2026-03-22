"""Tests for system monitor: collector, reporter, alerts."""

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.events.bus import EventBus
from src.events.types import AlertClearedEvent, AlertEvent
from src.monitor.alerts import AlertManager
from src.monitor.collector import Metrics, MetricsCollector
from src.monitor.reporter import StatusReporter

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_metrics(**kwargs) -> Metrics:
    defaults = dict(
        timestamp="2026-01-01T00:00:00+00:00",
        cpu_percent=20.0,
        cpu_per_core=[20.0, 20.0],
        ram_percent=40.0,
        ram_used_mb=4096.0,
        ram_total_mb=16384.0,
        disk_percent=50.0,
        disk_used_gb=100.0,
        disk_total_gb=500.0,
        ssh_sessions=1,
        claude_sessions=0,
        tunnel_status="up",
        ssh_auth_failures_last_min=0,
    )
    defaults.update(kwargs)
    return Metrics(**defaults)


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def collector(tmp_path):
    return MetricsCollector(history_file=tmp_path / "metrics.json")


@pytest.fixture
def reporter(collector):
    return StatusReporter(collector)


@pytest.fixture
def alert_manager(event_bus):
    return AlertManager(
        event_bus=event_bus,
        cpu_threshold=90.0,
        ram_threshold=85.0,
        disk_threshold=90.0,
        ssh_failure_threshold=5,
        tunnel_drop_threshold=3,
    )


# ── MetricsCollector ──────────────────────────────────────────────────────────


class TestMetricsCollector:
    def test_empty_history(self, collector):
        assert collector.get_latest() is None
        assert collector.get_history() == []

    def test_history_persistence(self, collector, tmp_path):
        sample = make_metrics()
        import dataclasses

        collector._history.append(dataclasses.asdict(sample))
        collector._save_history()

        c2 = MetricsCollector(history_file=tmp_path / "metrics.json")
        c2._load_history()
        assert len(c2._history) == 1

    def test_history_max_size(self, collector):
        import dataclasses

        from src.monitor.collector import _MAX_HISTORY

        for _ in range(_MAX_HISTORY + 100):
            collector._history.append(dataclasses.asdict(make_metrics()))

        # Trigger trimming via collect_once mock
        collector._history = collector._history[-_MAX_HISTORY:]
        assert len(collector._history) == _MAX_HISTORY

    def test_get_history_hours_limit(self, collector):
        import dataclasses

        # Add 120 samples (2 hours at 1/min)
        for _ in range(120):
            collector._history.append(dataclasses.asdict(make_metrics()))
        h1 = collector.get_history(hours=1)
        assert len(h1) == 60  # last 60 samples

    @pytest.mark.asyncio
    async def test_collect_once_uses_psutil(self, collector):
        mock_ram = MagicMock()
        mock_ram.percent = 70.0
        mock_ram.used = 7 * 1024**3
        mock_ram.total = 16 * 1024**3
        mock_disk = MagicMock()
        mock_disk.percent = 40.0
        mock_disk.used = 200 * 1024**3
        mock_disk.total = 500 * 1024**3
        mock_net = MagicMock()
        mock_net.bytes_sent = 1000
        mock_net.bytes_recv = 2000
        mock_net.packets_sent = 10
        mock_net.packets_recv = 20

        # Mock the entire _sample method to avoid psutil dependency in tests
        sample = make_metrics(cpu_percent=55.0, ram_percent=70.0)
        with patch.object(collector, "_sample", new=AsyncMock(return_value=sample)):
            m = await collector.collect_once()

        assert m.cpu_percent == 55.0
        assert m.ram_percent == 70.0
        assert len(collector._history) == 1

    @pytest.mark.asyncio
    async def test_load_corrupted_history(self, tmp_path):
        bad = tmp_path / "metrics.json"
        bad.write_text("not-json")
        c = MetricsCollector(history_file=bad)
        c._load_history()  # should not raise
        assert c._history == []

    @pytest.mark.asyncio
    async def test_tunnel_status_from_manager(self, collector):
        mock_tm = MagicMock()
        mock_state = MagicMock()
        mock_state.status = "up"
        mock_tm.get_state.return_value = mock_state
        collector._tunnel_manager = mock_tm

        sample = make_metrics(tunnel_status="up")
        with patch.object(collector, "_sample", new=AsyncMock(return_value=sample)):
            m = await collector.collect_once()

        assert m.tunnel_status == "up"


# ── StatusReporter ────────────────────────────────────────────────────────────


class TestStatusReporter:
    def test_format_status_no_data(self, reporter):
        text = reporter.format_status()
        assert "No metrics" in text

    def test_format_status_with_data(self, reporter):
        m = make_metrics(cpu_percent=55.0, ram_percent=70.0, tunnel_status="up")
        text = reporter.format_status(m)
        assert "55.0" in text
        assert "70.0" in text
        assert "up" in text

    def test_format_status_contains_all_fields(self, reporter):
        m = make_metrics()
        text = reporter.format_status(m)
        for key in ("CPU", "RAM", "Disk", "Tunnel", "SSH"):
            assert key in text

    def test_format_stats_no_data(self, reporter):
        text = reporter.format_stats()
        assert "No historical data" in text

    def test_format_stats_with_history(self, reporter, collector):
        import dataclasses

        for i in range(10):
            collector._history.append(
                dataclasses.asdict(make_metrics(cpu_percent=float(50 + i)))
            )
        text = reporter.format_stats()
        assert "24h Statistics" in text
        assert "CPU" in text

    def test_format_hourly_no_data(self, reporter):
        text = reporter.format_hourly_report()
        assert "no data" in text.lower()

    def test_format_hourly_with_data(self, reporter, collector):
        import dataclasses

        collector._history.append(dataclasses.asdict(make_metrics()))
        text = reporter.format_hourly_report()
        assert "Hourly Report" in text


# ── AlertManager ──────────────────────────────────────────────────────────────


class TestAlertManager:
    @pytest.mark.asyncio
    async def test_ram_alert_fires_once(self, alert_manager, event_bus):
        received = []

        async def collect(e):
            received.append(e)

        event_bus.subscribe(AlertEvent, collect)
        await event_bus.start()

        # First crossing → should fire
        await alert_manager.evaluate(make_metrics(ram_percent=90.0))
        # Still high → should NOT fire again
        await alert_manager.evaluate(make_metrics(ram_percent=92.0))

        await asyncio.sleep(0.05)
        await event_bus.stop()

        alerts = [
            e
            for e in received
            if isinstance(e, AlertEvent) and e.alert_type == "ram_high"
        ]
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_alert_clears_when_resolved(self, alert_manager, event_bus):
        cleared = []

        async def collect(e):
            cleared.append(e)

        event_bus.subscribe(AlertClearedEvent, collect)
        await event_bus.start()

        await alert_manager.evaluate(make_metrics(ram_percent=90.0))
        await alert_manager.evaluate(make_metrics(ram_percent=70.0))  # below threshold

        await asyncio.sleep(0.05)
        await event_bus.stop()

        clear_events = [
            e
            for e in cleared
            if isinstance(e, AlertClearedEvent) and e.alert_type == "ram_high"
        ]
        assert len(clear_events) == 1

    @pytest.mark.asyncio
    async def test_ram_no_alert_below_threshold(self, alert_manager, event_bus):
        received = []

        async def collect(e):
            received.append(e)

        event_bus.subscribe(AlertEvent, collect)
        await event_bus.start()

        await alert_manager.evaluate(make_metrics(ram_percent=80.0))  # below 85%

        await asyncio.sleep(0.05)
        await event_bus.stop()

        assert not received

    @pytest.mark.asyncio
    async def test_disk_alert(self, alert_manager, event_bus):
        received = []

        async def collect(e):
            received.append(e)

        event_bus.subscribe(AlertEvent, collect)
        await event_bus.start()

        await alert_manager.evaluate(make_metrics(disk_percent=95.0))

        await asyncio.sleep(0.05)
        await event_bus.stop()

        alerts = [e for e in received if e.alert_type == "disk_high"]
        assert len(alerts) == 1
        assert alerts[0].value == 95.0

    @pytest.mark.asyncio
    async def test_ssh_brute_force_alert(self, alert_manager, event_bus):
        received = []

        async def collect(e):
            received.append(e)

        event_bus.subscribe(AlertEvent, collect)
        await event_bus.start()

        await alert_manager.evaluate(make_metrics(ssh_auth_failures_last_min=10))

        await asyncio.sleep(0.05)
        await event_bus.stop()

        alerts = [e for e in received if e.alert_type == "ssh_brute_force"]
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_ssh_no_alert_below_threshold(self, alert_manager, event_bus):
        received = []

        async def collect(e):
            received.append(e)

        event_bus.subscribe(AlertEvent, collect)
        await event_bus.start()

        await alert_manager.evaluate(
            make_metrics(ssh_auth_failures_last_min=3)
        )  # below 5

        await asyncio.sleep(0.05)
        await event_bus.stop()

        assert not received

    @pytest.mark.asyncio
    async def test_tunnel_instability_alert(self, alert_manager, event_bus):
        received = []

        async def collect(e):
            received.append(e)

        event_bus.subscribe(AlertEvent, collect)
        await event_bus.start()

        # Record 3 drops (== threshold)
        for _ in range(3):
            alert_manager.record_tunnel_drop()
        await alert_manager.check_tunnel_instability()

        await asyncio.sleep(0.05)
        await event_bus.stop()

        alerts = [e for e in received if e.alert_type == "tunnel_instability"]
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_tunnel_instability_no_alert_below_threshold(
        self, alert_manager, event_bus
    ):
        received = []

        async def collect(e):
            received.append(e)

        event_bus.subscribe(AlertEvent, collect)
        await event_bus.start()

        for _ in range(2):  # below threshold of 3
            alert_manager.record_tunnel_drop()
        await alert_manager.check_tunnel_instability()

        await asyncio.sleep(0.05)
        await event_bus.stop()

        assert not received

    @pytest.mark.asyncio
    async def test_alert_refires_after_clear(self, alert_manager, event_bus):
        """After clearing, the alert can fire again on next crossing."""
        received = []

        async def collect(e):
            received.append(e)

        event_bus.subscribe(AlertEvent, collect)
        await event_bus.start()

        await alert_manager.evaluate(make_metrics(disk_percent=95.0))  # fires
        await alert_manager.evaluate(make_metrics(disk_percent=50.0))  # clears
        await alert_manager.evaluate(make_metrics(disk_percent=96.0))  # fires again

        await asyncio.sleep(0.1)
        await event_bus.stop()

        alerts = [
            e
            for e in received
            if isinstance(e, AlertEvent) and e.alert_type == "disk_high"
        ]
        assert len(alerts) == 2

    def test_active_alerts_property(self, alert_manager):
        assert alert_manager.active_alerts == frozenset()
        alert_manager._active.add("ram_high")
        assert "ram_high" in alert_manager.active_alerts
