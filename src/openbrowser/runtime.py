import asyncio
import logging
import time
from contextlib import AsyncExitStack

from cryptography.fernet import Fernet
from playwright.async_api import async_playwright

from openbrowser.approvals import ApprovalStore
from openbrowser.audit import AuditLog
from openbrowser.config import Config
from openbrowser.db import connect
from openbrowser.profiles import ProfileManager
from openbrowser.tasks import TaskStore
from openbrowser.tools import Tools
from openbrowser.vault import Vault

log = logging.getLogger(__name__)


class Runtime:
    """Owns the browser, the database and every store for the life of the server.

    The database and stores exist from construction (sign-in needs them before startup);
    the browser and tools exist inside `async with`."""

    def __init__(self, config: Config):
        self.config = config
        self.fernet = Fernet(config.vault_key)
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = connect(config.data_dir / "openbrowser.db")
        self.tasks = TaskStore(self.db, config.max_open_tasks)
        self.vault = Vault(self.db, self.fernet)
        self.audit = AuditLog(self.db)
        self.approvals = ApprovalStore(self.db)
        self.profiles: ProfileManager
        self.tools: Tools
        self._idle_since: dict[str, float] = {}
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> "Runtime":
        playwright = await self._stack.enter_async_context(async_playwright())
        self.profiles = ProfileManager(playwright, self.config.data_dir / "profiles", self.fernet,
                                       self.config.chromium_path, self.config.max_profiles)
        self._stack.push_async_callback(self.profiles.close_all)
        self.tools = Tools(self.config, self.profiles, self.tasks, self.vault, self.audit, self.approvals)
        housekeeping = asyncio.create_task(self._housekeeping())
        housekeeping.add_done_callback(_log_crash)
        self._stack.callback(housekeeping.cancel)
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()
        self.db.close()

    async def close_idle_browsers(self, now: float) -> list[str]:
        """Close browsers of profiles that have had no open task for `browser_idle_seconds`."""
        closed = []
        for name in self.profiles.open_names():
            if self.tasks.open_tasks(name):
                self._idle_since.pop(name, None)
                continue
            since = self._idle_since.setdefault(name, now)
            if now - since >= self.config.browser_idle_seconds:
                # Re-checked under the profile lock: a task may have been spawned meanwhile.
                if await self.profiles.close_if_idle(name, lambda name=name: not self.tasks.open_tasks(name)):
                    closed.append(name)
                self._idle_since.pop(name, None)
        return closed

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(self.config.housekeeping_seconds)
            await self.profiles.save_all()
            closed_tasks = self.tasks.close_idle(self.config.task_idle_seconds)
            if closed_tasks:
                log.info("Closed idle tasks: %s", closed_tasks)
            closed_browsers = await self.close_idle_browsers(time.monotonic())
            if closed_browsers:
                log.info("Closed idle browsers: %s", closed_browsers)


def _log_crash(task: asyncio.Task) -> None:
    """Housekeeping must never stop silently: without it, session cookies stop being saved."""
    if not task.cancelled() and task.exception() is not None:
        log.error("Housekeeping stopped: session cookies are no longer saved", exc_info=task.exception())
