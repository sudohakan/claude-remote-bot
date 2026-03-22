"""Tests for the tunnel manager and notifier."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.events.bus import EventBus
from src.events.types import TunnelRetryExhaustedEvent, TunnelStateChangeEvent
from src.tunnel.manager import TunnelManager, TunnelState
from src.tunnel.notifier import TunnelNotifier


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def tunnel_manager(event_bus, tmp_path):
    return TunnelManager(
        event_bus=event_bus,
        ssh_port=2222,
        poll_interval=30,
        max_retries=3,
        state_file=tmp_path / "tunnel.json",
    )


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.fixture
def notifier(event_bus, mock_bot):
    return TunnelNotifier(event_bus=event_bus, bot=mock_bot, admin_chat_id=12345)


# ── TunnelState ───────────────────────────────────────────────────────────────

class TestTunnelState:
    def test_default_values(self):
        state = TunnelState()
        assert state.status == "stopped"
        assert state.url is None
        assert state.ssh_host is None
        assert state.ssh_port is None
        assert state.retry_count == 0

    def test_dataclass_fields(self):
        state = TunnelState(status="up", url="tcp://x.tcp.ngrok.io:12345")
        assert state.status == "up"
        assert state.url == "tcp://x.tcp.ngrok.io:12345"


# ── URL parsing ───────────────────────────────────────────────────────────────

class TestUrlParsing:
    def test_valid_url(self):
        host, port = TunnelManager._parse_url("tcp://0.tcp.ngrok.io:19876")
        assert host == "0.tcp.ngrok.io"
        assert port == 19876

    def test_invalid_url(self):
        host, port = TunnelManager._parse_url("not-a-url")
        assert host is None
        assert port is None

    def test_empty_url(self):
        host, port = TunnelManager._parse_url("")
        assert host is None
        assert port is None


# ── State persistence ─────────────────────────────────────────────────────────

class TestStatePersistence:
    def test_save_and_load(self, tunnel_manager, tmp_path):
        tunnel_manager._state = TunnelState(
            status="up",
            url="tcp://x.ngrok.io:1234",
            ssh_host="x.ngrok.io",
            ssh_port=1234,
            retry_count=2,
        )
        tunnel_manager._save_state()
        assert (tmp_path / "tunnel.json").exists()

        # Re-load into a new manager
        mgr2 = TunnelManager(
            event_bus=MagicMock(),
            state_file=tmp_path / "tunnel.json",
        )
        mgr2._load_state()
        # Status is reset to "stopped" on load
        assert mgr2._state.status == "stopped"
        assert mgr2._state.ssh_host == "x.ngrok.io"
        assert mgr2._state.ssh_port == 1234
        assert mgr2._state.retry_count == 2

    def test_load_nonexistent_file(self, tunnel_manager):
        # Should not raise
        tunnel_manager._load_state()
        assert tunnel_manager._state.status == "stopped"

    def test_load_corrupted_file(self, tmp_path):
        bad_file = tmp_path / "tunnel.json"
        bad_file.write_text("not-json")
        mgr = TunnelManager(event_bus=MagicMock(), state_file=bad_file)
        mgr._load_state()  # should not raise
        assert mgr._state.status == "stopped"


# ── State transitions ─────────────────────────────────────────────────────────

class TestStateTransitions:
    @pytest.mark.asyncio
    async def test_set_state_emits_event(self, tunnel_manager, event_bus):
        received_events = []

        async def collector(event):
            received_events.append(event)

        event_bus.subscribe(TunnelStateChangeEvent, collector)
        await event_bus.start()

        await tunnel_manager._set_state("up", url="tcp://x.ngrok.io:1234", host="x.ngrok.io", port=1234)
        await asyncio.sleep(0.05)  # allow dispatch

        await event_bus.stop()

        assert len(received_events) == 1
        assert received_events[0].new_state == "up"

    @pytest.mark.asyncio
    async def test_no_event_on_same_state(self, tunnel_manager, event_bus):
        received = []

        async def collector(event):
            received.append(event)

        event_bus.subscribe(TunnelStateChangeEvent, collector)
        await event_bus.start()

        tunnel_manager._state.status = "up"
        tunnel_manager._state.url = "tcp://x.ngrok.io:1234"
        await tunnel_manager._set_state("up", url="tcp://x.ngrok.io:1234")
        await asyncio.sleep(0.05)

        await event_bus.stop()

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_retry_exhausted_event(self, tunnel_manager, event_bus):
        received = []

        async def collector(event):
            received.append(event)

        event_bus.subscribe(TunnelRetryExhaustedEvent, collector)
        await event_bus.start()

        tunnel_manager._state.retry_count = 3  # == max_retries
        tunnel_manager._state.status = "up"

        with patch.object(tunnel_manager, "_terminate_ngrok"):
            await tunnel_manager._handle_tunnel_down()

        await asyncio.sleep(0.05)
        await event_bus.stop()

        # Down event + retry-exhausted
        retry_events = [e for e in received if isinstance(e, TunnelRetryExhaustedEvent)]
        assert len(retry_events) == 1

    @pytest.mark.asyncio
    async def test_ssh_command_when_up(self, tunnel_manager):
        tunnel_manager._state.status = "up"
        tunnel_manager._state.ssh_host = "host.ngrok.io"
        tunnel_manager._state.ssh_port = 22222
        cmd = tunnel_manager.get_ssh_command()
        assert "host.ngrok.io" in cmd
        assert "22222" in cmd

    @pytest.mark.asyncio
    async def test_no_ssh_command_when_down(self, tunnel_manager):
        tunnel_manager._state.status = "down"
        assert tunnel_manager.get_ssh_command() is None


# ── API response handling ─────────────────────────────────────────────────────

class TestApiResponseHandling:
    @pytest.mark.asyncio
    async def test_parse_valid_api_response(self, tunnel_manager, event_bus):
        await event_bus.start()
        data = {
            "tunnels": [
                {
                    "proto": "tcp",
                    "public_url": "tcp://0.tcp.ngrok.io:19876",
                }
            ]
        }
        await tunnel_manager._handle_api_response(data)
        assert tunnel_manager._state.status == "up"
        assert tunnel_manager._state.ssh_port == 19876
        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_empty_tunnels_when_up(self, tunnel_manager, event_bus):
        await event_bus.start()
        tunnel_manager._state.status = "up"
        tunnel_manager._state.url = "tcp://x.ngrok.io:1"

        with patch.object(tunnel_manager, "_handle_tunnel_down", new_callable=AsyncMock) as mock_down:
            await tunnel_manager._handle_api_response({"tunnels": []})
            mock_down.assert_called_once()

        await event_bus.stop()


# ── TunnelNotifier ────────────────────────────────────────────────────────────

class TestTunnelNotifier:
    @pytest.mark.asyncio
    async def test_notify_on_up(self, notifier, mock_bot, event_bus):
        notifier.register()
        await event_bus.start()

        event = TunnelStateChangeEvent(
            previous_state="down",
            new_state="up",
            tunnel_url="tcp://x.ngrok.io:1234",
            ssh_host="x.ngrok.io",
            ssh_port=1234,
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)
        await event_bus.stop()

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args.kwargs
        assert call_kwargs["chat_id"] == 12345
        assert "UP" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_notify_on_down(self, notifier, mock_bot, event_bus):
        notifier.register()
        await event_bus.start()

        event = TunnelStateChangeEvent(
            previous_state="up",
            new_state="down",
        )
        await event_bus.publish(event)
        await asyncio.sleep(0.05)
        await event_bus.stop()

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args.kwargs
        assert "DOWN" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_dedup_suppresses_repeated_notifications(self, notifier, mock_bot, event_bus):
        """Same transition twice within 5 min should only fire once."""
        notifier.register()
        await event_bus.start()

        for _ in range(3):
            await event_bus.publish(
                TunnelStateChangeEvent(previous_state="up", new_state="down")
            )
        await asyncio.sleep(0.1)
        await event_bus.stop()

        assert mock_bot.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_no_notification_on_unchanged_state(self, notifier, mock_bot, event_bus):
        """up→up or down→down should not trigger a notification."""
        notifier.register()
        await event_bus.start()

        await event_bus.publish(
            TunnelStateChangeEvent(previous_state="up", new_state="up")
        )
        await asyncio.sleep(0.05)
        await event_bus.stop()

        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_exhausted_notification(self, notifier, mock_bot, event_bus):
        notifier.register()
        await event_bus.start()

        await event_bus.publish(TunnelRetryExhaustedEvent(attempts=5))
        await asyncio.sleep(0.05)
        await event_bus.stop()

        mock_bot.send_message.assert_called_once()
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "retries exhausted" in text.lower()

    @pytest.mark.asyncio
    async def test_retry_exhausted_dedup(self, notifier, mock_bot, event_bus):
        """Retry-exhausted suppressed within 1 hour."""
        notifier._retry_exhausted_sent = float("inf")  # pretend we just sent it
        notifier.register()
        await event_bus.start()

        await event_bus.publish(TunnelRetryExhaustedEvent(attempts=5))
        await asyncio.sleep(0.05)
        await event_bus.stop()

        mock_bot.send_message.assert_not_called()
