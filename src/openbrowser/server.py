from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from mcp.server.apps import Apps, ResourceCsp, client_supports_apps
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from openbrowser.errors import OpenBrowserError
from openbrowser.live import page_html
from openbrowser.oauth import OAuthProvider
from openbrowser.runtime import Runtime
from openbrowser.tasks import Task

LIVE_UI = "ui://openbrowser/live.html"
VAULT_UI = "ui://openbrowser/vault.html"

INSTRUCTIONS = """\
OpenBrowser runs a persistent Chromium browser with named profiles that stay logged in.
Start with spawn_task(profile, goal) and show the user the live view link it returns.
Only one task per profile runs at a time; if yours is queued, browser tools return your queue position.
Call snapshot to see the page as an accessibility tree where each element has a ref like e12; pass the ref to click and fill.
Every action returns the new snapshot. For logins, call list_vault and fill with secret='name.field'; never ask the user to type passwords in chat.
Set irreversible=true on click or press for anything that buys, sends, submits, deletes or can't be undone; a human then approves it.
If a site needs the user (login, captcha), ask them to take control in the live view. Call close_task when done."""


def create_mcp(runtime: Runtime, oauth: OAuthProvider) -> MCPServer:
    config = runtime.config

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        async with runtime:
            yield

    apps = Apps()
    origin = config.public_url
    ws_origin = origin.replace("http", "ws", 1)
    apps.add_html_resource(LIVE_UI, page_html("live.html"), title="OpenBrowser live view",
                           csp=ResourceCsp(connect_domains=[origin, ws_origin]))
    apps.add_html_resource(VAULT_UI, page_html("vault.html"), title="OpenBrowser vault")

    def describe(task: Task) -> str:
        state = task.state
        if state == "queued":
            state = f"queued, {runtime.tasks.position(task)} ahead"
        return f"Task {task.id} on profile {task.profile!r}: {state}."

    @apps.tool(resource_uri=LIVE_UI, structured_output=False)
    async def spawn_task(profile: str, goal: str) -> str:
        """Queue a task on a browser profile. Returns the task id, its state and a live view link for the user."""
        with _agent_errors():
            runtime.profiles.allow(profile)
            task = runtime.tasks.spawn(profile, goal)
            runtime.audit.record(_agent(), "spawn_task", {"goal": goal}, task.state, task.id, profile)
            return f"{describe(task)}\nLive view: {runtime.tools.live_url(task)}"

    @apps.tool(resource_uri=LIVE_UI, structured_output=False)
    async def live_view(task_id: str) -> str:
        """Show the user the task's browser. They can watch, take control, and approve risky actions there."""
        with _agent_errors():
            task = runtime.tasks.get(task_id)
            return f"{describe(task)}\nLive view: {runtime.tools.live_url(task)}"

    @apps.tool(resource_uri=VAULT_UI, structured_output=False)
    async def vault() -> str:
        """Open the vault form in the chat so the user can add or change logins themselves.
        Never ask for passwords in chat; use this instead."""
        return runtime.tools.list_vault()

    @apps.tool(resource_uri=VAULT_UI, visibility=["app"], structured_output=False)
    async def vault_save(ctx: Context, name: str, site: str, fields: dict[str, str]) -> str:
        """Save a vault entry. Called by the vault form, not the model."""
        _require_app(ctx)
        with _agent_errors():
            runtime.vault.put(name, site, fields)
            runtime.audit.record("human", "vault_save", {"name": name, "site": site, "fields": sorted(fields)}, "ok")
        return f"Saved {name}."

    @apps.tool(resource_uri=VAULT_UI, visibility=["app"], structured_output=False)
    async def vault_delete(ctx: Context, name: str) -> str:
        """Delete a vault entry. Called by the vault form, not the model."""
        _require_app(ctx)
        with _agent_errors():
            runtime.vault.delete(name)
            runtime.audit.record("human", "vault_delete", {"name": name}, "ok")
        return f"Deleted {name}."

    # Created after the app tools above: the server copies the extension's tools when it is built.
    mcp = MCPServer(
        "openbrowser",
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        extensions=[apps],
        auth_server_provider=oauth,
        auth=AuthSettings(
            issuer_url=origin,
            resource_server_url=f"{origin}/mcp",
            validate_token_resource=False,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    @mcp.tool(structured_output=False)
    async def steer_task(task_id: str, message: str) -> str:
        """Send a message to the agent working on a task. It is shown with that agent's next action result."""
        with _agent_errors():
            runtime.tasks.steer(task_id, message)
        return "Message queued."

    @mcp.tool(structured_output=False)
    async def list_tasks(profile: str | None = None) -> str:
        """List open tasks and their state, optionally for one profile."""
        return "\n".join(describe(t) for t in runtime.tasks.open_tasks(profile)) or "No open tasks."

    @mcp.tool(structured_output=False)
    async def close_task(task_id: str) -> str:
        """Close a task and hand its profile to the next queued task."""
        with _agent_errors():
            task = runtime.tasks.get(task_id)
            runtime.tasks.close(task_id)
            runtime.audit.record(_agent(), "close_task", {}, "ok", task_id, task.profile)
        return f"Task {task_id} closed."

    @mcp.tool(structured_output=False)
    async def snapshot(task_id: str) -> str:
        """The current page as an accessibility tree. Elements carry refs like e12 for click and fill."""
        with _agent_errors():
            return await runtime.tools.snapshot(_agent(), task_id)

    @mcp.tool(structured_output=False)
    async def screenshot(task_id: str) -> Image:
        """A PNG of the current page. Use when the snapshot is not enough (canvas, images, layout)."""
        with _agent_errors():
            return Image(data=await runtime.tools.screenshot(_agent(), task_id), format="png")

    @mcp.tool(structured_output=False)
    async def navigate(task_id: str, url: str) -> str:
        """Go to a URL. Returns the new snapshot."""
        with _agent_errors():
            return await runtime.tools.navigate(_agent(), task_id, url)

    @mcp.tool(structured_output=False)
    async def click(task_id: str, ref: str, irreversible: bool) -> str:
        """Click the element with this ref. Set irreversible=true if it buys, sends, submits, deletes or
        can't be undone; a human must then approve it. Returns the new snapshot."""
        with _agent_errors():
            return await runtime.tools.click(_agent(), task_id, ref, irreversible)

    @mcp.tool(structured_output=False)
    async def fill(task_id: str, ref: str, text: str | None = None, secret: str | None = None) -> str:
        """Replace the text in an input. Give text, or secret='entry.field' to type a vault value
        you never see (only works on the entry's site). Returns the new snapshot."""
        with _agent_errors():
            return await runtime.tools.fill(_agent(), task_id, ref, text, secret)

    @mcp.tool(structured_output=False)
    async def press(task_id: str, key: str, irreversible: bool) -> str:
        """Press a key, like Enter, Tab or Escape. Set irreversible=true if it submits something that
        can't be undone. Returns the new snapshot."""
        with _agent_errors():
            return await runtime.tools.press(_agent(), task_id, key, irreversible)

    @mcp.tool(structured_output=False)
    async def list_vault() -> str:
        """Saved logins: entry names, sites and field names. Never the values."""
        return runtime.tools.list_vault()

    return mcp


def _agent() -> str:
    token = get_access_token()
    return f"mcp:{token.client_id}" if token else "mcp"


def _require_app(ctx: Context) -> None:
    """App-only tools are hidden from the model only by hosts that support MCP Apps.
    Elsewhere the model could see them, so refuse: changing a vault entry's site would defeat the site lock."""
    if not client_supports_apps(ctx):
        raise ToolError("This tool is only for the vault form in apps that support MCP Apps.")


@contextmanager
def _agent_errors() -> Iterator[None]:
    """Show expected failures to the agent instead of a generic tool crash."""
    try:
        yield
    except OpenBrowserError as e:
        raise ToolError(str(e)) from e
