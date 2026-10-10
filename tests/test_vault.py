import io

import pytest
from login_site import ref
from PIL import Image

from openbrowser.errors import OpenBrowserError
from openbrowser.runtime import Runtime

pytestmark = pytest.mark.anyio
EMAIL, PASSWORD = "ada@example.com", "correct horse battery"


def magenta_pixels(png: bytes) -> int:
    """Playwright paints masked elements #FF00FF."""
    colors = Image.open(io.BytesIO(png)).convert("RGB").getcolors(maxcolors=1 << 24)
    return next((n for n, rgb in colors if rgb == (255, 0, 255)), 0)


async def login_with_vault(rt: Runtime, site_url: str) -> tuple[str, list[str]]:
    rt.vault.put("testsite", "127.0.0.1", {"email": EMAIL, "password": PASSWORD})
    task = rt.tasks.spawn("main", "log in")
    outputs = [await rt.tools.navigate("agent", task.id, f"{site_url}/login")]
    login = outputs[0]
    outputs.append(await rt.tools.fill("agent", task.id, ref(login, "textbox", "Email"), None, "testsite.email"))
    outputs.append(await rt.tools.fill("agent", task.id, ref(login, "textbox", "Password"), None, "testsite.password"))
    return task.id, outputs


def test_entries_never_show_values(config):
    rt = Runtime(config)
    rt.vault.put("github", "GitHub.com", {"email": EMAIL, "password": PASSWORD})
    [entry] = rt.vault.entries()
    assert (entry.name, entry.site, entry.fields) == ("github", "github.com", ["email", "password"])
    assert EMAIL.encode() not in rt.db.execute("SELECT fields FROM vault").fetchone()[0]


def test_blank_value_keeps_the_old_one(config):
    rt = Runtime(config)
    rt.vault.put("github", "github.com", {"email": EMAIL, "password": PASSWORD})
    rt.vault.put("github", "github.com", {"email": "new@example.com", "password": ""})
    assert rt.vault.value_for("github.password", "https://github.com/login") == PASSWORD


def test_site_lock(config):
    rt = Runtime(config)
    rt.vault.put("github", "github.com", {"password": PASSWORD})
    assert rt.vault.value_for("github.password", "https://gist.github.com/") == PASSWORD
    for url in ("https://github.com.evil.example/", "https://evilgithub.com/", "about:blank"):
        with pytest.raises(OpenBrowserError, match="is for github.com"):
            rt.vault.value_for("github.password", url)


async def test_agent_logs_in_without_seeing_the_values(config, site):
    async with Runtime(config) as rt:
        task_id, outputs = await login_with_vault(rt, site.url)
        login = outputs[0]
        outputs.append(await rt.tools.click("agent", task_id, ref(login, "button", "Sign in"), irreversible=False))
        assert "Signed in as [secret:testsite.email]" in outputs[-1]
        everything = "\n".join(outputs) + str(rt.audit.rows())
        assert EMAIL not in everything and PASSWORD not in everything
        assert "[secret:testsite.password]" in outputs[2]  # E6: the raw snapshot would show the password


async def test_screenshot_masks_vault_values(config, site):
    async with Runtime(config) as rt:
        task = rt.tasks.spawn("main", "x")
        await rt.tools.navigate("agent", task.id, f"{site.url}/login")
        assert magenta_pixels(await rt.tools.screenshot("agent", task.id)) == 0  # control: nothing to mask yet
        rt.tasks.close(task.id)
        task_id, _ = await login_with_vault(rt, site.url)
        assert magenta_pixels(await rt.tools.screenshot("agent", task_id)) > 200


async def test_fill_refuses_other_sites(config, site):
    async with Runtime(config) as rt:
        rt.vault.put("testsite", "127.0.0.1", {"password": PASSWORD})
        task = rt.tasks.spawn("main", "x")
        snap = await rt.tools.navigate("agent", task.id, site.url.replace("127.0.0.1", "localhost") + "/login")
        with pytest.raises(OpenBrowserError, match="is for 127.0.0.1, but this page is on localhost"):
            await rt.tools.fill("agent", task.id, ref(snap, "textbox", "Password"), None, "testsite.password")


def test_list_vault_text(config):
    rt = Runtime(config)
    rt.vault.put("github", "github.com", {"email": EMAIL})
    from openbrowser.tools import Tools
    tools = Tools(config, None, rt.tasks, rt.vault, rt.audit, rt.approvals)
    assert "- github (github.com): email" in tools.list_vault()
    assert EMAIL not in tools.list_vault()
