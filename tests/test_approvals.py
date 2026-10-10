import pytest
from login_site import ref

from openbrowser.approvals import is_risky
from openbrowser.config import APPROVAL_WORDS
from openbrowser.errors import OpenBrowserError
from openbrowser.runtime import Runtime

pytestmark = pytest.mark.anyio


def test_word_list_matches_whole_words():
    assert is_risky('button "Place order"', APPROVAL_WORDS)
    assert is_risky('button "Pay now"', APPROVAL_WORDS)
    assert not is_risky('link "PayPal help"', APPROVAL_WORDS)
    assert not is_risky('button "Resend code"', APPROVAL_WORDS)


async def checkout(rt: Runtime, site_url: str) -> tuple[str, str]:
    task = rt.tasks.spawn("main", "buy")
    return task.id, await rt.tools.navigate("agent", task.id, f"{site_url}/checkout")


async def test_risky_click_waits_for_approval_then_runs(config, site):
    async with Runtime(config) as rt:
        task_id, snap = await checkout(rt, site.url)
        result = await rt.tools.click("agent", task_id, ref(snap, "button", "Place order"), irreversible=False)
        assert "Approval required before: Click button \"Place order\"" in result
        assert f"/live/{task_id}?token=" in result
        assert rt.tasks.get(task_id).state == "waiting_approval"
        with pytest.raises(OpenBrowserError, match="waiting for a human to approve"):
            await rt.tools.snapshot("agent", task_id)
        page = await rt.tools.page(rt.tasks.get(task_id))
        assert await page.locator("#out").text_content() == ""  # nothing happened yet

        approval = rt.approvals.pending_for(task_id)
        await rt.tools.resolve_approval(rt.tasks.get(task_id), approval.id, approved=True)
        snap = await rt.tools.snapshot("agent", task_id)
        assert "Order placed" in snap
        assert "Approved by the user and done" in snap


async def test_rejected_action_does_not_run(config, site):
    async with Runtime(config) as rt:
        task_id, snap = await checkout(rt, site.url)
        await rt.tools.click("agent", task_id, ref(snap, "button", "Place order"), irreversible=False)
        approval = rt.approvals.pending_for(task_id)
        await rt.tools.resolve_approval(rt.tasks.get(task_id), approval.id, approved=False)
        snap = await rt.tools.snapshot("agent", task_id)
        assert "Order placed" not in snap
        assert "Rejected by the user" in snap


async def test_safe_click_runs_and_agent_flag_forces_approval(config, site):
    async with Runtime(config) as rt:
        task_id, snap = await checkout(rt, site.url)
        coupon = ref(snap, "button", "Apply coupon")
        assert "Coupon applied" in await rt.tools.click("agent", task_id, coupon, irreversible=False)
        assert "Approval required" in await rt.tools.click("agent", task_id, coupon, irreversible=True)


async def test_enter_in_a_send_form_needs_approval(config, site):
    async with Runtime(config) as rt:
        task_id, snap = await checkout(rt, site.url)
        await rt.tools.fill("agent", task_id, ref(snap, "textbox", "Message"), "hi", None)
        result = await rt.tools.press("agent", task_id, "Enter", irreversible=False)
        assert "Approval required before: Press Enter" in result and "Send message" in result


async def test_approval_belongs_to_its_task(config, site):
    async with Runtime(config) as rt:
        task_id, snap = await checkout(rt, site.url)
        await rt.tools.click("agent", task_id, ref(snap, "button", "Place order"), irreversible=False)
        other = rt.tasks.spawn("other", "x")
        with pytest.raises(OpenBrowserError, match="belongs to another task"):
            await rt.tools.resolve_approval(other, rt.approvals.pending_for(task_id).id, approved=True)


async def test_every_action_is_audited(config, site):
    async with Runtime(config) as rt:
        task_id, snap = await checkout(rt, site.url)
        await rt.tools.click("agent", task_id, ref(snap, "button", "Place order"), irreversible=False)
        await rt.tools.resolve_approval(rt.tasks.get(task_id), rt.approvals.pending_for(task_id).id, approved=True)
        actions = [(r.agent, r.action, r.result) for r in reversed(rt.audit.rows(task_id=task_id))]
        assert actions == [("agent", "navigate", "ok"), ("agent", "click", "waiting for approval"),
                           ("human", "approve", 'Click button "Place order"'), ("human", "click", "ok")]
