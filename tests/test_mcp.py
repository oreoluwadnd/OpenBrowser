import re

import pytest
from login_site import ref
from mcp import Client

from openbrowser.server import create_server

pytestmark = pytest.mark.anyio


def text(result) -> str:
    return "\n".join(c.text for c in result.content if c.type == "text")


async def test_agent_logs_in_over_mcp(config, site):
    async with Client(create_server(config)) as client:
        spawned = text(await client.call_tool("spawn_task", {"profile": "main", "goal": "log in"}))
        task_id = re.search(r"Task (\w+)", spawned).group(1)
        assert "running" in spawned

        snap = text(await client.call_tool("navigate", {"task_id": task_id, "url": f"{site.url}/login"}))
        await client.call_tool("fill", {"task_id": task_id, "ref": ref(snap, "textbox", "Email"), "text": "ada@example.com"})
        await client.call_tool("fill", {"task_id": task_id, "ref": ref(snap, "textbox", "Password"), "text": "pw"})
        await client.call_tool("steer_task", {"task_id": task_id, "message": "then stop"})
        snap = text(await client.call_tool("click", {"task_id": task_id, "ref": ref(snap, "button", "Sign in")}))
        assert "Signed in as ada@example.com" in snap
        assert "- then stop" in snap

        shot = await client.call_tool("screenshot", {"task_id": task_id})
        assert shot.content[0].type == "image"

        await client.call_tool("close_task", {"task_id": task_id})
        assert text(await client.call_tool("list_tasks", {})) == "No open tasks."


async def test_queued_task_gets_a_readable_error(config, site):
    async with Client(create_server(config)) as client:
        await client.call_tool("spawn_task", {"profile": "main", "goal": "first"})
        second = text(await client.call_tool("spawn_task", {"profile": "main", "goal": "second"}))
        assert "queued, 1 ahead" in second
        task_id = re.search(r"Task (\w+)", second).group(1)

        result = await client.call_tool("snapshot", {"task_id": task_id})
        assert result.is_error
        assert "queued: 1 task(s) ahead" in text(result)


async def test_bad_ref_gets_a_readable_error(config, site):
    async with Client(create_server(config)) as client:
        spawned = text(await client.call_tool("spawn_task", {"profile": "main", "goal": "x"}))
        task_id = re.search(r"Task (\w+)", spawned).group(1)
        await client.call_tool("navigate", {"task_id": task_id, "url": f"{site.url}/login"})
        result = await client.call_tool("click", {"task_id": task_id, "ref": "e999"})
        assert result.is_error
        assert "No element with ref e999" in text(result)


async def test_results_are_plain_text_not_json(config):
    """Structured output made Claude receive snapshots as one escaped JSON string."""
    async with Client(create_server(config)) as client:
        result = await client.call_tool("list_tasks", {})
        assert result.structured_content is None
