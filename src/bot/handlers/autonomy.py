"""/auto: hand a one-off task to the Claude autonomy queue from Telegram (owner only, not promoted admins).

`/auto <metin>` writes a oneshot claude job and adds it with `auto add`; `/auto alan:<id> <metin>` picks the
area (default claude-stack); `/auto durum <id>` shows the job. The bot never sets authority or human-only
fields: the area's own policy decides what the run may do.
"""

import asyncio
import json
import os
import re
import tempfile
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.utils import messages as M
from src.bot.utils.formatting import escape_html

AUTO_BIN = os.environ.get("AUTONOMY_CLI", "/home/hakan/.local/bin/auto")
JARVIS_BIN = os.environ.get("JARVIS_CLI", "/home/hakan/.local/bin/jarvis")
DEFAULT_AREA = "claude-stack"
AUTO_TIMEOUT_SEC = 60
TITLE_MAX = 80
USAGE = (
    "Kullanım:\n"
    "<code>/auto &lt;iş&gt;</code> otonom kuyruğa tek seferlik iş ekler\n"
    "<code>/auto alan:hcd &lt;iş&gt;</code> alanı seçer (varsayılan claude-stack)\n"
    "<code>/auto durum &lt;id&gt;</code> işin durumunu gösterir"
)
AREA_RE = re.compile(r"^alan:([a-z0-9-]+)(?:\s+|$)", re.IGNORECASE)
JOB_ID_RE = re.compile(r"^tg-\d{8}-\d{6}$")


def parse_auto_args(text: str) -> dict:
    """Split the text after /auto into an action: add (area, task), status (job id) or usage."""
    body = (text or "").strip()
    if not body:
        return {"action": "usage"}
    head, _, rest = body.partition(" ")
    if head.lower() == "durum":
        job_id = rest.strip()
        return {"action": "status", "job_id": job_id} if JOB_ID_RE.match(job_id) else {"action": "usage"}
    area = DEFAULT_AREA
    m = AREA_RE.match(body)
    if m:
        area, body = m.group(1).lower(), body[m.end():].strip()
    if not body:
        return {"action": "usage"}
    return {"action": "add", "area": area, "task": body}


def build_job(task: str, area: str, user_id: int, now: datetime) -> dict:
    """Oneshot claude job for the autonomy queue; due at the next tick."""
    title = task.splitlines()[0]
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1] + "…"
    return {
        "id": f"tg-{now:%Y%m%d-%H%M%S}",
        "title": f"Telegram: {title}",
        "area_id": area,
        "source": "user",
        "kind": "oneshot",
        "executor": "claude",
        "weight": "light",
        "priority": 50,
        "schedule": {"every": "24h"},
        "need_check": {"always": True, "reason": f"Telegram'dan verilen tek seferlik iş (kullanıcı {user_id})"},
        "instruction": task,
    }


async def _run_auto(args: list[str], user_id: int, binary: str = AUTO_BIN) -> tuple[int, str]:
    env = {**os.environ, "AUTONOMY_ACTOR": f"telegram:{user_id}"}
    proc = await asyncio.create_subprocess_exec(
        binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=AUTO_TIMEOUT_SEC)
    return proc.returncode, (out or b"").decode(errors="replace").strip()


def _is_owner(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Only the owner's Telegram id: admins promoted with /promote must not queue autonomous runs."""
    settings = ctx.bot_data.get("settings")
    return bool(settings) and update.effective_user.id == settings.admin_telegram_id


async def _add(update: Update, area: str, task: str) -> None:
    user_id = update.effective_user.id
    job = build_job(task, area, user_id, datetime.now())
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)
        path = f.name
    try:
        code, out = await _run_auto(["add", path], user_id)
    finally:
        os.unlink(path)
    if code == 0:
        await update.message.reply_text(
            f"✅ Otonom kuyruğa eklendi: <code>{job['id']}</code> (alan {escape_html(area)}).\n"
            f"Durum: <code>/auto durum {job['id']}</code>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"⚠️ İş eklenemedi (kod {code}):\n<pre>{escape_html(out[-400:])}</pre>", parse_mode="HTML",
        )


async def _status(update: Update, job_id: str) -> None:
    code, out = await _run_auto(["runs", "--job", job_id, "--limit", "3"], update.effective_user.id)
    if code != 0:
        await update.message.reply_text(f"⚠️ Durum okunamadı:\n<pre>{escape_html(out[-400:])}</pre>", parse_mode="HTML")
        return
    await update.message.reply_text(f"<pre>{escape_html(out[-1500:])}</pre>", parse_mode="HTML")


async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    if not _is_owner(update, ctx):
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return
    text = update.message.text or ""
    parsed = parse_auto_args(text.split(" ", 1)[1] if " " in text else "")
    try:
        if parsed["action"] == "add":
            await _add(update, parsed["area"], parsed["task"])
        elif parsed["action"] == "status":
            await _status(update, parsed["job_id"])
        else:
            await update.message.reply_text(USAGE, parse_mode="HTML")
    except asyncio.TimeoutError:
        await update.message.reply_text(f"⚠️ auto komutu {AUTO_TIMEOUT_SEC} sn içinde bitmedi.", parse_mode="HTML")


async def cmd_ack(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/ack: Hakan saw a Jarvis escalation on Telegram; `jarvis ack` stops the ladder. Owner only."""
    if update.effective_user is None or update.message is None:
        return
    if not _is_owner(update, ctx):
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return
    try:
        code, out = await _run_auto(["ack"], update.effective_user.id, JARVIS_BIN)
    except asyncio.TimeoutError:
        await update.message.reply_text(f"⚠️ jarvis ack {AUTO_TIMEOUT_SEC} sn içinde bitmedi.", parse_mode="HTML")
        return
    except OSError as err:
        await update.message.reply_text(f"⚠️ jarvis çalıştırılamadı: {escape_html(str(err))}", parse_mode="HTML")
        return
    if code == 0:
        await update.message.reply_text(f"✅ {escape_html(out or 'Onaylandı.')}", parse_mode="HTML")
    else:
        await update.message.reply_text(f"⚠️ Onay verilemedi (kod {code}):\n<pre>{escape_html(out[-400:])}</pre>",
                                        parse_mode="HTML")



JARVIS_USAGE = "Kullanım: <code>/j &lt;mesaj&gt;</code> Jarvis'e yazar, cevabı buraya gelir."
RELAY_BUSY = 3


async def cmd_jarvis(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/j <mesaj>: write to Jarvis as Hakan. `jarvis relay` signs it as user-relay; owner only."""
    if update.effective_user is None or update.message is None:
        return
    if not _is_owner(update, ctx):
        await update.message.reply_text(M.msg_admin_only(), parse_mode="HTML")
        return
    # one line, no control characters: the text is typed into a terminal
    body = re.sub(r"[\x00-\x1f\x7f]", " ", " ".join((update.message.text or "").split()[1:])).strip()
    if not body:
        await update.message.reply_text(JARVIS_USAGE, parse_mode="HTML")
        return
    try:
        code, out = await _run_auto(["relay", f"[Telegram] {body}"], update.effective_user.id, JARVIS_BIN)
    except asyncio.TimeoutError:
        await update.message.reply_text(f"⚠️ jarvis relay {AUTO_TIMEOUT_SEC} sn içinde bitmedi.", parse_mode="HTML")
        return
    except OSError as err:
        await update.message.reply_text(f"⚠️ jarvis çalıştırılamadı: {escape_html(str(err))}", parse_mode="HTML")
        return
    if code == 0:
        await update.message.reply_text("📨 Jarvis'e iletildi, cevabı buraya gelecek.", parse_mode="HTML")
    elif code == RELAY_BUSY:
        await update.message.reply_text("⚠️ Jarvis'in giriş kutusunda yazı var ya da oturum açık değil; birazdan tekrar dene.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"⚠️ İletilemedi (kod {code}):\n<pre>{escape_html(out[-400:])}</pre>", parse_mode="HTML")
