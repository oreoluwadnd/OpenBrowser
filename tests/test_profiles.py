import dataclasses

import pytest
from login_site import ref

from openbrowser.runtime import Runtime

pytestmark = pytest.mark.anyio


async def log_in(rt: Runtime, site_url: str) -> str:
    task = rt.tasks.spawn("main", "log in")
    snap = await rt.tools.navigate("test", task.id, f"{site_url}/login")
    await rt.tools.fill("test", task.id, ref(snap, "textbox", "Email"), "ada@example.com", None)
    await rt.tools.fill("test", task.id, ref(snap, "textbox", "Password"), "pw", None)
    snap = await rt.tools.click("test", task.id, ref(snap, "button", "Sign in"), irreversible=False)
    assert "Signed in as ada@example.com" in snap
    return task.id


async def whoami(rt: Runtime, site_url: str) -> str:
    task = rt.tasks.spawn("main", "check login")
    return await rt.tools.navigate("test", task.id, f"{site_url}/me")


async def test_still_logged_in_after_restart(config, site):
    async with Runtime(config) as rt:
        await log_in(rt, site.url)
    async with Runtime(config) as rt:
        assert "Signed in as ada@example.com" in await whoami(rt, site.url)


async def test_restart_loses_login_without_saved_session_cookies(config, site):
    """Control for the test above: proves Chromium itself drops the session cookie (finding E4)."""
    async with Runtime(config) as rt:
        await log_in(rt, site.url)
    (config.data_dir / "profiles" / "main" / "session_cookies.enc").unlink()
    async with Runtime(config) as rt:
        assert "Not signed in" in await whoami(rt, site.url)


async def test_session_cookies_are_encrypted_on_disk(config, site):
    async with Runtime(config) as rt:
        await log_in(rt, site.url)
    saved = (config.data_dir / "profiles" / "main" / "session_cookies.enc").read_bytes()
    assert b"sid" not in saved


async def test_parallel_first_calls_open_the_profile_once(config, site):
    """Agents can send tool calls in parallel; two launches of one profile would fail (finding E5)."""
    import asyncio

    async with Runtime(config) as rt:
        task = rt.tasks.spawn("main", "x")
        await asyncio.gather(rt.tools.snapshot("test", task.id), rt.tools.snapshot("test", task.id))


async def test_idle_browser_closes_and_login_survives(config, site):
    async with Runtime(dataclasses.replace(config, browser_idle_seconds=60)) as rt:
        rt.tasks.close(await log_in(rt, site.url))
        assert await rt.close_idle_browsers(now=0) == []  # starts the idle clock
        assert await rt.close_idle_browsers(now=59) == []
        assert await rt.close_idle_browsers(now=60) == ["main"]
        assert rt.profiles.open_names() == []
        assert "Signed in as ada@example.com" in await whoami(rt, site.url)  # reopens, still logged in


async def test_browser_with_an_open_task_stays_open(config, site):
    async with Runtime(dataclasses.replace(config, browser_idle_seconds=60)) as rt:
        task = rt.tasks.spawn("main", "x")
        await rt.tools.navigate("test", task.id, f"{site.url}/login")
        rt.tasks.set_state(task.id, "waiting_approval", expected="running")
        assert await rt.close_idle_browsers(now=0) == []
        assert await rt.close_idle_browsers(now=1000) == []
        assert rt.profiles.open_names() == ["main"]


async def test_new_task_restarts_the_idle_clock(config, site):
    async with Runtime(dataclasses.replace(config, browser_idle_seconds=60)) as rt:
        rt.tasks.close(await log_in(rt, site.url))
        await rt.close_idle_browsers(now=0)
        task = rt.tasks.spawn("main", "again")
        await rt.close_idle_browsers(now=50)
        rt.tasks.close(task.id)
        assert await rt.close_idle_browsers(now=100) == []
        assert await rt.close_idle_browsers(now=159) == []
        assert await rt.close_idle_browsers(now=160) == ["main"]
