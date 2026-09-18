"""A timed-out CLI run must not leave its subprocess (and process group) behind."""

import asyncio

from src.claude.sdk_integration import _kill_process


async def test_kill_process_reaps_the_whole_group():
    # A shell that spawns a child: killing only the leader would orphan `sleep`.
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", "sleep 30 & wait", start_new_session=True
    )
    assert proc.returncode is None
    await asyncio.wait_for(_kill_process(proc), timeout=10)
    assert proc.returncode is not None


async def test_kill_process_tolerates_none_and_finished():
    await _kill_process(None)
    proc = await asyncio.create_subprocess_exec("true")
    await proc.wait()
    await _kill_process(proc)  # already exited: no error
