import pytest
from login_site import ref

from openbrowser.runtime import Runtime

pytestmark = pytest.mark.anyio


async def log_in(rt: Runtime, site_url: str) -> str:
    task = rt.tasks.spawn("main", "log in")
    snap = await rt.tools.navigate(task.id, f"{site_url}/login")
    await rt.tools.fill(task.id, ref(snap, "textbox", "Email"), "ada@example.com")
    await rt.tools.fill(task.id, ref(snap, "textbox", "Password"), "pw")
    snap = await rt.tools.click(task.id, ref(snap, "button", "Sign in"))
    assert "Signed in as ada@example.com" in snap
    return task.id


async def whoami(rt: Runtime, site_url: str) -> str:
    task = rt.tasks.spawn("main", "check login")
    return await rt.tools.navigate(task.id, f"{site_url}/me")


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
        await asyncio.gather(rt.tools.snapshot(task.id), rt.tools.snapshot(task.id))
