import asyncio
import logging
from contextlib import AsyncExitStack

from cryptography.fernet import Fernet
from playwright.async_api import async_playwright

from openbrowser.config import Config
from openbrowser.profiles import ProfileManager
from openbrowser.tasks import TaskStore
from openbrowser.tools import Tools

log = logging.getLogger(__name__)


class Runtime:
    """Owns the browser, profiles, tasks and tools for the life of the server."""

    def __init__(self, config: Config):
        self.config = config
        self.tasks: TaskStore
        self.profiles: ProfileManager
        self.tools: Tools
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> "Runtime":
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        fernet = Fernet(self.config.vault_key)
        playwright = await self._stack.enter_async_context(async_playwright())
        self.profiles = ProfileManager(
            playwright, self.config.data_dir / "profiles", fernet, self.config.chromium_path
        )
        self._stack.push_async_callback(self.profiles.close_all)
        self.tasks = TaskStore(self.config.data_dir / "openbrowser.db")
        self.tools = Tools(self.profiles, self.tasks)
        housekeeping = asyncio.create_task(self._housekeeping())
        self._stack.callback(housekeeping.cancel)
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(self.config.cookie_save_seconds)
            await self.profiles.save_all()
            closed = self.tasks.close_idle(self.config.task_idle_seconds)
            if closed:
                log.info("Closed idle tasks: %s", closed)
