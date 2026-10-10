import re

import pytest
from login_site import ref
from mcp import Client

from openbrowser.oauth import OAuthProvider
from openbrowser.runtime import Runtime
from openbrowser.server import LIVE_UI, VAULT_UI, create_mcp

pytestmark = pytest.mark.anyio


def mcp_for(config):
    runtime = Runtime(config)
    return create_mcp(runtime, OAuthProvider(runtime.db, config.api_key, config.public_url)), runtime


def text(result) -> str:
    return "\n".join(c.text for c in result.content if c.type == "text")


async def spawn(client) -> tuple[str, str]:
    spawned = text(await client.call_tool("spawn_task", {"profile": "main", "goal": "log in"}))
    return re.search(r"Task (\w+)", spawned).group(1), spawned


async def test_agent_logs_in_over_mcp(config, site):
    mcp, _ = mcp_for(config)
    async with Client(mcp) as client:
        task_id, spawned = await spawn(client)
        assert "running" in spawned
        assert re.search(rf"Live view: http://127\.0\.0\.1:8931/live/{task_id}\?token=[\w-]+", spawned)

        snap = text(await client.call_tool("navigate", {"task_id": task_id, "url": f"{site.url}/login"}))
        await client.call_tool("fill", {"task_id": task_id, "ref": ref(snap, "textbox", "Email"), "text": "ada@example.com"})
        await client.call_tool("fill", {"task_id": task_id, "ref": ref(snap, "textbox", "Password"), "text": "pw"})
        await client.call_tool("steer_task", {"task_id": task_id, "message": "then stop"})
        snap = text(await client.call_tool("click", {"task_id": task_id, "ref": ref(snap, "button", "Sign in"),
                                                    "irreversible": False}))
        assert "Signed in as ada@example.com" in snap
        assert "- then stop" in snap

        shot = await client.call_tool("screenshot", {"task_id": task_id})
        assert shot.content[0].type == "image"

        await client.call_tool("close_task", {"task_id": task_id})
        assert text(await client.call_tool("list_tasks", {})) == "No open tasks."


async def test_queued_task_gets_a_readable_error(config):
    mcp, _ = mcp_for(config)
    async with Client(mcp) as client:
        await spawn(client)
        second_id, second = await spawn(client)
        assert "queued, 1 ahead" in second
        result = await client.call_tool("snapshot", {"task_id": second_id})
        assert result.is_error
        assert "queued: 1 task(s) ahead" in text(result)


async def test_bad_ref_gets_a_readable_error(config, site):
    mcp, _ = mcp_for(config)
    async with Client(mcp) as client:
        task_id, _ = await spawn(client)
        await client.call_tool("navigate", {"task_id": task_id, "url": f"{site.url}/login"})
        result = await client.call_tool("click", {"task_id": task_id, "ref": "e999", "irreversible": False})
        assert result.is_error
        assert "No element with ref e999" in text(result)


async def test_results_are_plain_text_not_json(config):
    """Structured output made Claude receive snapshots as one escaped JSON string (finding E9)."""
    mcp, _ = mcp_for(config)
    async with Client(mcp) as client:
        result = await client.call_tool("list_tasks", {})
        assert result.structured_content is None


async def test_vault_form_tools_refuse_clients_without_apps(config):
    """Without MCP Apps the model can see app-only tools; saving would let it move an entry's site lock."""
    mcp, runtime = mcp_for(config)
    async with Client(mcp) as client:
        result = await client.call_tool("vault_save", {"name": "x", "site": "evil.example", "fields": {"password": "p"}})
        assert result.is_error
        assert "only for the vault form" in text(result)
        assert runtime.vault.entries() == []


async def test_apps_are_wired(config):
    mcp, _ = mcp_for(config)
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert tools["spawn_task"].meta["ui"]["resourceUri"] == LIVE_UI
        assert tools["live_view"].meta["ui"]["resourceUri"] == LIVE_UI
        assert tools["vault"].meta["ui"]["resourceUri"] == VAULT_UI
        assert tools["vault_save"].meta["ui"]["visibility"] == ["app"]
        live = (await client.read_resource(LIVE_UI)).contents[0]
        assert "ui/initialize" in live.text and "/*BRIDGE*/" not in live.text
        assert live.meta["ui"]["csp"]["connectDomains"] == ["http://127.0.0.1:8931", "ws://127.0.0.1:8931"]
