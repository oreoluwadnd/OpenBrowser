"""Live view: stream the task's browser to a web page and let a human drive or approve."""

import asyncio
import contextlib
from pathlib import Path

from playwright.async_api import CDPSession, Page
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from openbrowser.errors import OpenBrowserError
from openbrowser.runtime import Runtime
from openbrowser.tasks import Task

WEB = Path(__file__).parent / "web"
POLL_SECONDS = 0.5


def page_html(name: str) -> str:
    """A page from web/, with the chat bridge script inlined (MCP App resources must be self-contained)."""
    return (WEB / name).read_text().replace("/*BRIDGE*/", (WEB / "bridge.js").read_text())


class LiveView:
    def __init__(self, runtime: Runtime):
        self._runtime = runtime

    async def page(self, request: Request) -> HTMLResponse:
        try:
            self._runtime.tasks.by_live_token(request.path_params["task_id"], request.query_params.get("token", ""))
        except OpenBrowserError as e:
            return HTMLResponse(str(e), status_code=403)
        return HTMLResponse(page_html("live.html"))

    async def socket(self, ws: WebSocket) -> None:
        try:
            task = self._runtime.tasks.by_live_token(ws.path_params["task_id"], ws.query_params.get("token", ""))
        except OpenBrowserError:
            await ws.close(code=4403)
            return
        await ws.accept()
        await _Session(self._runtime, task.id, ws).run()


class _Session:
    def __init__(self, runtime: Runtime, task_id: str, ws: WebSocket):
        self._rt = runtime
        self._task_id = task_id
        self._ws = ws
        self._frames: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
        self._page: Page | None = None
        self._cdp: CDPSession | None = None
        self._last_state: dict | None = None
        self._send_lock = asyncio.Lock()

    async def run(self) -> None:
        workers = [asyncio.create_task(self._watch()), asyncio.create_task(self._send_frames())]
        try:
            await self._read()
        finally:
            for worker in workers:
                worker.cancel()
            await self._stop_screencast()

    async def _watch(self) -> None:
        """Follow the newest tab and push state changes, until the task closes."""
        while True:
            task = self._task()
            if task.state == "closed":
                await self._send_state(task, url="")
                await self._ws.close()
                return
            page = await self._rt.tools.page(task)
            if page is not self._page:
                await self._stop_screencast()
                await self._start_screencast(page)
            await self._send_state(task, url=page.url)
            await asyncio.sleep(POLL_SECONDS)

    async def _start_screencast(self, page: Page) -> None:
        self._page = page
        self._cdp = await page.context.new_cdp_session(page)
        cdp = self._cdp

        async def on_frame(params: dict) -> None:
            with contextlib.suppress(Exception):
                await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            frame = {"type": "frame", "data": params["data"],
                     "width": params["metadata"]["deviceWidth"], "height": params["metadata"]["deviceHeight"]}
            if self._frames.full():
                self._frames.get_nowait()
            self._frames.put_nowait(frame)

        cdp.on("Page.screencastFrame", lambda params: asyncio.ensure_future(on_frame(params)))
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 70})

    async def _stop_screencast(self) -> None:
        if self._cdp is not None:
            with contextlib.suppress(Exception):
                await self._cdp.detach()
        self._cdp = None
        self._page = None

    async def _send_frames(self) -> None:
        while True:
            await self._send(await self._frames.get())

    async def _send(self, message: dict) -> None:
        # Frames, state and errors come from different coroutines; one message at a time on the socket.
        async with self._send_lock:
            await self._ws.send_json(message)

    async def _send_state(self, task: Task, url: str) -> None:
        approval = self._rt.approvals.pending_for(task.id)
        state = {"type": "state", "state": task.state, "url": url,
                 "approval": {"id": approval.id, "description": approval.description} if approval else None}
        if state != self._last_state:
            self._last_state = state
            await self._send(state)

    async def _read(self) -> None:
        # The socket ends when the viewer leaves (disconnect) or the task closes (we closed it).
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            while True:
                message = await self._ws.receive_json()
                try:
                    await self._handle(message)
                except OpenBrowserError as e:
                    await self._send({"type": "error", "message": str(e)})

    async def _handle(self, m: dict) -> None:
        task = self._task()
        match m["type"]:
            case "take_control":
                self._rt.tasks.set_state(task.id, "human_control", expected="running")
                self._rt.audit.record("human", "take_control", {}, "ok", task.id, task.profile)
            case "hand_back":
                self._rt.tasks.set_state(task.id, "running", expected="human_control")
                self._rt.audit.record("human", "hand_back", {}, "ok", task.id, task.profile)
                self._rt.tasks.steer(task.id, "The user used the browser and handed it back. Take a snapshot first.")
            case "approve" | "reject":
                await self._rt.tools.resolve_approval(task, m["id"], approved=m["type"] == "approve")
            case "mouse" | "key":
                if task.state != "human_control":
                    raise OpenBrowserError("Take control first.")
                await self._input(m)

    async def _input(self, m: dict) -> None:
        """Human input. Not written to the audit log: keystrokes can be passwords."""
        page = await self._rt.tools.page(self._task())
        if m["type"] == "mouse":
            if m["action"] == "click":
                await page.mouse.click(m["x"], m["y"])
            elif m["action"] == "wheel":
                await page.mouse.move(m["x"], m["y"])
                await page.mouse.wheel(0, m["dy"])
        elif len(m["key"]) == 1 and not m["mods"]:
            await page.keyboard.type(m["key"])
        else:
            await page.keyboard.press("+".join([*m["mods"], m["key"]]))

    def _task(self) -> Task:
        return self._rt.tasks.get(self._task_id)
