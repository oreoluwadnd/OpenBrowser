import asyncio
import json
import re
from pathlib import Path

from cryptography.fernet import Fernet
from playwright.async_api import BrowserContext, Playwright

from openbrowser.errors import OpenBrowserError

PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ProfileManager:
    """One persistent Chromium context per profile, kept open while the runtime runs.

    Chromium drops cookies without an expiry when a profile closes, so they are
    saved encrypted next to the profile and added back on open.
    """

    def __init__(self, playwright: Playwright, root: Path, fernet: Fernet, chromium_path: str | None,
                 max_profiles: int):
        self._playwright = playwright
        self._max_profiles = max_profiles
        self._root = root
        self._fernet = fernet
        self._chromium_path = chromium_path
        self._contexts: dict[str, BrowserContext] = {}
        self._open_lock = asyncio.Lock()

    def allow(self, name: str) -> None:
        """Fail unless `name` is a valid profile that exists or fits under the profile limit."""
        if not PROFILE_NAME.match(name):
            raise OpenBrowserError(f"Invalid profile name {name!r}: use lowercase letters, digits, '-' and '_'.")
        names = self.names()
        if name not in names and len(names) >= self._max_profiles:
            raise OpenBrowserError(f"Profile limit reached ({self._max_profiles}). Use one of: {', '.join(names)}.")

    async def open(self, name: str) -> BrowserContext:
        self.allow(name)
        async with self._open_lock:
            if name not in self._contexts:
                self._contexts[name] = await self._launch(name)
            return self._contexts[name]

    def names(self) -> list[str]:
        return sorted(p.name for p in self._root.iterdir() if p.is_dir()) if self._root.exists() else []

    async def _launch(self, name: str) -> BrowserContext:
        folder = self._root / name
        folder.mkdir(parents=True, exist_ok=True)
        context = await self._playwright.chromium.launch_persistent_context(
            folder / "browser",
            headless=True,
            executable_path=self._chromium_path,
        )
        cookies_file = self._cookies_file(name)
        if cookies_file.exists():
            await context.add_cookies(json.loads(self._fernet.decrypt(cookies_file.read_bytes())))
        return context

    async def save_session_cookies(self, name: str) -> None:
        context = self._contexts[name]
        session_cookies = [c for c in await context.cookies() if c["expires"] == -1]
        self._cookies_file(name).write_bytes(self._fernet.encrypt(json.dumps(session_cookies).encode()))

    async def save_all(self) -> None:
        for name in list(self._contexts):
            await self.save_session_cookies(name)

    async def close_all(self) -> None:
        await self.save_all()
        for context in self._contexts.values():
            await context.close()
        self._contexts.clear()

    def _cookies_file(self, name: str) -> Path:
        return self._root / name / "session_cookies.enc"
