from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from openbrowser.errors import OpenBrowserError
from openbrowser.profiles import ProfileManager
from openbrowser.tasks import TaskStore

ACTION_TIMEOUT_MS = 10_000


class Tools:
    """Every browser action, for every interface. One code path."""

    def __init__(self, profiles: ProfileManager, tasks: TaskStore):
        self._profiles = profiles
        self._tasks = tasks

    async def snapshot(self, task_id: str) -> str:
        page = await self._page(task_id)
        return await self._observe(task_id, page)

    async def screenshot(self, task_id: str) -> bytes:
        page = await self._page(task_id)
        return await page.screenshot(type="png")

    async def navigate(self, task_id: str, url: str) -> str:
        page = await self._page(task_id)
        await _run(page.goto(url, wait_until="domcontentloaded", timeout=ACTION_TIMEOUT_MS * 3))
        return await self._observe(task_id, page)

    async def click(self, task_id: str, ref: str) -> str:
        page = await self._page(task_id)
        await _run((await _element(page, ref)).click(timeout=ACTION_TIMEOUT_MS))
        return await self._observe_after_action(task_id)

    async def fill(self, task_id: str, ref: str, text: str) -> str:
        page = await self._page(task_id)
        await _run((await _element(page, ref)).fill(text, timeout=ACTION_TIMEOUT_MS))
        return await self._observe_after_action(task_id)

    async def press(self, task_id: str, key: str) -> str:
        page = await self._page(task_id)
        await _run(page.keyboard.press(key))
        return await self._observe_after_action(task_id)

    async def _page(self, task_id: str) -> Page:
        """The newest open tab of the task's profile."""
        task = self._tasks.require_running(task_id)
        context = await self._profiles.open(task.profile)
        if not context.pages:
            await context.new_page()
        return context.pages[-1]

    async def _observe_after_action(self, task_id: str) -> str:
        # An action can open a tab or start a navigation; snapshot whatever is newest once it has loaded.
        page = await self._page(task_id)
        await _run(page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS * 3))
        return await self._observe(task_id, page)

    async def _observe(self, task_id: str, page: Page) -> str:
        tree = await _run(page.aria_snapshot(mode="ai"))
        parts = [f"URL: {page.url}", f"Title: {await page.title()}", "", tree]
        notes = self._tasks.take_notes(task_id)
        if notes:
            parts += ["", "Messages from the user:"] + [f"- {n}" for n in notes]
        return "\n".join(parts)


async def _element(page: Page, ref: str) -> Locator:
    locator = page.locator(f"aria-ref={ref}")
    if await _run(locator.count()) == 0:
        raise OpenBrowserError(f"No element with ref {ref} on this page. Take a new snapshot.")
    return locator


async def _run(awaitable):
    try:
        return await awaitable
    except PlaywrightError as e:
        raise OpenBrowserError(str(e).splitlines()[0]) from e
