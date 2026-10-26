"""/auto: argument parsing, job shape (checked by the real autonomy validator in a throwaway state dir), admin gate."""

import json
import os
import shutil
import subprocess
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.bot.handlers import autonomy as A

NOW = datetime(2026, 10, 26, 10, 40, 5)


def test_parse_add_default_area():
    assert A.parse_auto_args("HCD loglarını tara") == {"action": "add", "area": "claude-stack", "task": "HCD loglarını tara"}


def test_parse_area_prefix():
    assert A.parse_auto_args("alan:HCD finans sayfasını ölç") == {"action": "add", "area": "hcd", "task": "finans sayfasını ölç"}


def test_parse_status_and_usage():
    assert A.parse_auto_args("durum tg-20261026-104005") == {"action": "status", "job_id": "tg-20261026-104005"}
    assert A.parse_auto_args("durum ; rm -rf") == {"action": "usage"}
    assert A.parse_auto_args("") == {"action": "usage"}
    assert A.parse_auto_args("alan:hcd   ") == {"action": "usage"}


def test_build_job_shape():
    job = A.build_job("ilk satır\nikinci satır", "hcd", 42, NOW)
    assert job["id"] == "tg-20261026-104005"
    assert job["title"] == "Telegram: ilk satır"
    assert job["kind"] == "oneshot" and job["executor"] == "claude"
    assert job["instruction"] == "ilk satır\nikinci satır"
    assert not {"authority", "preapproved", "force_open", "allow"} & job.keys()


def test_build_job_truncates_long_title():
    job = A.build_job("x" * 200, "claude-stack", 1, NOW)
    assert len(job["title"]) == len("Telegram: ") + A.TITLE_MAX


@pytest.mark.skipif(not shutil.which(A.AUTO_BIN) and not os.path.exists(A.AUTO_BIN), reason="auto CLI yok")
def test_job_passes_real_validator(tmp_path):
    env = {**os.environ, "AUTONOMY_STATE_DIR": str(tmp_path), "AUTONOMY_ALERTS_PATH": str(tmp_path / "a.log"),
           }
    subprocess.run([A.AUTO_BIN, "seed"], env=env, check=True, capture_output=True)
    env["AUTONOMY_ACTOR"] = "telegram:1"
    job_file = tmp_path / "job.json"
    job_file.write_text(json.dumps(A.build_job("stack loglarını özetle", "claude-stack", 1, NOW), ensure_ascii=False))
    r = subprocess.run([A.AUTO_BIN, "add", str(job_file)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tg-20261026-104005" in r.stdout


def _update(user_id, text):
    msg = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), message=msg)


def _ctx(admin_id=7, is_admin=False):
    access = SimpleNamespace(is_admin=AsyncMock(return_value=is_admin))
    return SimpleNamespace(bot_data={"settings": SimpleNamespace(admin_telegram_id=admin_id), "access_manager": access})


@pytest.mark.asyncio
async def test_non_admin_rejected_without_running_auto():
    upd = _update(99, "/auto bir iş")
    with patch.object(A, "_run_auto", new=AsyncMock()) as run:
        await A.cmd_auto(upd, _ctx())
    run.assert_not_called()
    assert "Admin" in upd.message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_promoted_admin_rejected():
    upd = _update(99, "/auto bir iş")
    with patch.object(A, "_run_auto", new=AsyncMock()) as run:
        await A.cmd_auto(upd, _ctx(is_admin=True))
    run.assert_not_called()


@pytest.mark.asyncio
async def test_admin_add_calls_auto_add_with_telegram_actor():
    upd = _update(7, "/auto alan:hcd panel hatasını incele")
    with patch.object(A, "_run_auto", new=AsyncMock(return_value=(0, "iş eklendi"))) as run:
        await A.cmd_auto(upd, _ctx())
    args, user_id = run.call_args.args
    assert args[0] == "add" and user_id == 7
    assert "Otonom kuyruğa eklendi" in upd.message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_auto_failure_is_reported():
    upd = _update(7, "/auto bir iş")
    with patch.object(A, "_run_auto", new=AsyncMock(return_value=(1, "alan yok: x"))):
        await A.cmd_auto(upd, _ctx())
    assert "İş eklenemedi (kod 1)" in upd.message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_ack_owner_runs_jarvis_ack():
    upd = _update(7, "/ack")
    with patch.object(A, "_run_auto", new=AsyncMock(return_value=(0, "1 ulaşma onaylandı"))) as run:
        await A.cmd_ack(upd, _ctx())
    assert run.call_args.args == (["ack"], 7, A.JARVIS_BIN)
    assert "1 ulaşma onaylandı" in upd.message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_ack_non_owner_and_promoted_rejected():
    for ctx in (_ctx(), _ctx(is_admin=True)):
        upd = _update(99, "/ack")
        with patch.object(A, "_run_auto", new=AsyncMock()) as run:
            await A.cmd_ack(upd, ctx)
        run.assert_not_called()
