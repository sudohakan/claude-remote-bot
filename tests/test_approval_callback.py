"""Onayla / Reddet buttons under a Jarvis ban request: owner only, exact data shape, one CLI call per press."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.bot.handlers import autonomy as A
from src.bot.handlers import callback as C

OWNER = 999888777
RID = "0123456789abcdef"


def update(data, user_id=OWNER):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=SimpleNamespace(text="Jarvis yasaklı bir iş için onay istiyor."),
    )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id), callback_query=query
    )


def ctx(authorised=True):
    access = SimpleNamespace(is_authorised=AsyncMock(return_value=authorised))
    return SimpleNamespace(
        bot_data={
            "settings": SimpleNamespace(admin_telegram_id=OWNER),
            "access_manager": access,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,verb", [(f"apr:ok:{RID}", "approve"), (f"apr:no:{RID}", "reject")]
)
async def test_owner_press_runs_the_matching_cli_verb(data, verb):
    u = update(data)
    with patch.object(
        A, "_run_auto", new=AsyncMock(return_value=(0, "onaylandı"))
    ) as run:
        await C.handle_callback(u, ctx())
    run.assert_awaited_once()
    assert run.await_args.args[0] == [verb, RID]
    u.callback_query.answer.assert_awaited_once()
    assert u.callback_query.edit_message_text.await_args.args[0].endswith(
        "✅ Onaylandı" if verb == "approve" else "❌ Reddedildi"
    )


@pytest.mark.asyncio
async def test_promoted_admin_or_stranger_cannot_approve():
    for user_id, authorised in [(123, True), (456, False)]:
        u = update(f"apr:ok:{RID}", user_id)
        with patch.object(A, "_run_auto", new=AsyncMock()) as run:
            await C.handle_callback(u, ctx(authorised))
        run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        "apr:ok:../../x",
        "apr:yes:" + RID,
        "apr:ok:" + RID + "; rm -rf /",
        "apr:ok:",
        "apr:ok:0123",
    ],
)
async def test_malformed_button_data_runs_nothing(data):
    u = update(data)
    with patch.object(A, "_run_auto", new=AsyncMock()) as run:
        await C.handle_callback(u, ctx())
    run.assert_not_awaited()
    u.callback_query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cli_refusal_is_shown_not_marked_approved():
    u = update(f"apr:ok:{RID}")
    with patch.object(A, "_run_auto", new=AsyncMock(return_value=(5, "süresi doldu"))):
        await C.handle_callback(u, ctx())
    assert "süresi doldu" in u.callback_query.edit_message_text.await_args.args[0]
    assert "Onaylandı" not in u.callback_query.edit_message_text.await_args.args[0]
