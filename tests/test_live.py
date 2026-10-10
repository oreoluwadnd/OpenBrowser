"""The live view, driven by a second browser playing the human."""

import asyncio
import os

import pytest
from login_site import ref
from playwright.async_api import async_playwright

from openbrowser.errors import OpenBrowserError

pytestmark = pytest.mark.anyio

PAINTED_JS = "() => document.getElementById('screen').getContext('2d').getImageData(0, 0, 1, 1).data[3] === 255"


@pytest.fixture
async def viewer():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ.get("OPENBROWSER_CHROMIUM"), headless=True)
        page = await browser.new_page(viewport={"width": 1000, "height": 800})
        yield page
        await browser.close()


async def open_checkout(server, site):
    rt = server.runtime
    task = rt.tasks.spawn("main", "buy")
    await rt.tools.navigate("agent", task.id, f"{site.url}/checkout")
    return task, await rt.tools.page(task)


async def eventually(read, expected, seconds=5.0):
    """Live view input travels over a WebSocket, so its effect lands a moment after the viewer sends it."""
    for _ in range(int(seconds / 0.05)):
        if await read() == expected:
            return
        await asyncio.sleep(0.05)
    assert await read() == expected


async def click_on_screen(viewer, task_page, locator):
    """Click where the element appears in the live view's canvas."""
    box = await locator.bounding_box()
    canvas = await viewer.locator("#screen").bounding_box()
    scale = canvas["width"] / task_page.viewport_size["width"]
    await viewer.mouse.click(canvas["x"] + (box["x"] + box["width"] / 2) * scale,
                             canvas["y"] + (box["y"] + box["height"] / 2) * scale)


async def test_human_watches_takes_control_and_hands_back(server, site, viewer):
    rt = server.runtime
    task, task_page = await open_checkout(server, site)
    await viewer.goto(rt.tools.live_url(task))
    await viewer.wait_for_function(PAINTED_JS)  # frames are arriving
    await viewer.get_by_text("Agent is driving").wait_for()

    await viewer.get_by_role("button", name="Take control").click()
    await viewer.get_by_text("You are driving").wait_for()
    with pytest.raises(OpenBrowserError, match="A human has control"):
        await rt.tools.snapshot("agent", task.id)

    await click_on_screen(viewer, task_page, task_page.get_by_label("Message"))
    await viewer.keyboard.type("Hi")
    await viewer.keyboard.press("Backspace")
    await eventually(task_page.get_by_label("Message").input_value, "H")

    await click_on_screen(viewer, task_page, task_page.get_by_role("button", name="Place order"))
    await eventually(task_page.locator("#out").text_content, "Order placed")  # a human click needs no approval

    await viewer.get_by_role("button", name="Hand back").click()
    await viewer.get_by_text("Agent is driving").wait_for()
    assert "handed it back" in await rt.tools.snapshot("agent", task.id)
    actions = [r.action for r in rt.audit.rows(task_id=task.id)]
    assert "take_control" in actions and "hand_back" in actions


async def test_human_approves_in_the_live_view(server, site, viewer):
    rt = server.runtime
    task, task_page = await open_checkout(server, site)
    snap = await rt.tools.snapshot("agent", task.id)
    await rt.tools.click("agent", task.id, ref(snap, "button", "Place order"), irreversible=False)

    await viewer.goto(rt.tools.live_url(task))
    await viewer.get_by_text('Click button "Place order"').wait_for()
    assert await task_page.locator("#out").text_content() == ""
    await viewer.get_by_role("button", name="Approve").click()
    await viewer.get_by_text("Agent is driving").wait_for()
    assert await task_page.locator("#out").text_content() == "Order placed"


async def test_wrong_token_is_refused(server, viewer):
    task = server.runtime.tasks.spawn("main", "x")
    response = await viewer.goto(f"{server.url}/live/{task.id}?token=wrong")
    assert response.status == 403


async def test_live_view_ends_when_task_closes(server, site, viewer):
    rt = server.runtime
    task, _ = await open_checkout(server, site)
    await viewer.goto(rt.tools.live_url(task))
    await viewer.get_by_text("Agent is driving").wait_for()
    rt.tasks.close(task.id)
    await viewer.get_by_text("Task closed").wait_for()
