from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from openbrowser.config import Config
from openbrowser.errors import OpenBrowserError
from openbrowser.runtime import Runtime
from openbrowser.tasks import Task

INSTRUCTIONS = """\
OpenBrowser runs a persistent Chromium browser with named profiles that stay logged in.
Start with spawn_task(profile, goal). Only one task per profile runs at a time; if yours is queued,
browser tools return your queue position, so try again later. Call snapshot to see the page as an
accessibility tree where each element has a ref like e12. Pass that ref to click and fill.
Every action returns the new snapshot. Call close_task when done so the next task can run."""


def create_server(config: Config) -> MCPServer:
    runtime = Runtime(config)

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        async with runtime:
            yield

    mcp = MCPServer("openbrowser", instructions=INSTRUCTIONS, lifespan=lifespan)

    @mcp.tool(structured_output=False)
    async def spawn_task(profile: str, goal: str) -> str:
        """Queue a task on a browser profile. Returns the task id and whether it is running or queued."""
        with _agent_errors():
            runtime.profiles.check_name(profile)
            return _describe(runtime, runtime.tasks.spawn(profile, goal))

    @mcp.tool(structured_output=False)
    async def steer_task(task_id: str, message: str) -> str:
        """Send a message to the agent working on a task. It is shown with that agent's next action result."""
        with _agent_errors():
            runtime.tasks.steer(task_id, message)
        return "Message queued."

    @mcp.tool(structured_output=False)
    async def list_tasks(profile: str | None = None) -> str:
        """List open tasks, optionally for one profile."""
        tasks = runtime.tasks.open_tasks(profile)
        return "\n".join(_describe(runtime, t) for t in tasks) or "No open tasks."

    @mcp.tool(structured_output=False)
    async def close_task(task_id: str) -> str:
        """Close a task and hand its profile to the next queued task."""
        with _agent_errors():
            runtime.tasks.close(task_id)
        return f"Task {task_id} closed."

    @mcp.tool(structured_output=False)
    async def snapshot(task_id: str) -> str:
        """The current page as an accessibility tree. Elements carry refs like e12 for click and fill."""
        with _agent_errors():
            return await runtime.tools.snapshot(task_id)

    @mcp.tool(structured_output=False)
    async def screenshot(task_id: str) -> Image:
        """A PNG of the current page. Use when the snapshot is not enough (canvas, images, layout)."""
        with _agent_errors():
            return Image(data=await runtime.tools.screenshot(task_id), format="png")

    @mcp.tool(structured_output=False)
    async def navigate(task_id: str, url: str) -> str:
        """Go to a URL. Returns the new snapshot."""
        with _agent_errors():
            return await runtime.tools.navigate(task_id, url)

    @mcp.tool(structured_output=False)
    async def click(task_id: str, ref: str) -> str:
        """Click the element with this ref. Returns the new snapshot."""
        with _agent_errors():
            return await runtime.tools.click(task_id, ref)

    @mcp.tool(structured_output=False)
    async def fill(task_id: str, ref: str, text: str) -> str:
        """Replace the text in the input with this ref. Returns the new snapshot."""
        with _agent_errors():
            return await runtime.tools.fill(task_id, ref, text)

    @mcp.tool(structured_output=False)
    async def press(task_id: str, key: str) -> str:
        """Press a key, like Enter, Tab or Escape. Returns the new snapshot."""
        with _agent_errors():
            return await runtime.tools.press(task_id, key)

    return mcp


@contextmanager
def _agent_errors() -> Iterator[None]:
    """Show expected failures to the agent instead of a generic tool crash."""
    try:
        yield
    except OpenBrowserError as e:
        raise ToolError(str(e)) from e


def _describe(runtime: Runtime, task: Task) -> str:
    if task.state == "queued":
        return f"Task {task.id} on profile {task.profile!r}: queued, {runtime.tasks.position(task)} ahead."
    return f"Task {task.id} on profile {task.profile!r}: {task.state}."
