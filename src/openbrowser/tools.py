import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from openbrowser.approvals import ApprovalStore, is_risky
from openbrowser.audit import AuditLog
from openbrowser.config import Config
from openbrowser.errors import OpenBrowserError
from openbrowser.profiles import ProfileManager
from openbrowser.snapshot import compact
from openbrowser.tasks import Task, TaskStore
from openbrowser.vault import Vault

T = TypeVar("T")
ACTION_TIMEOUT_MS = 10_000

# Runs in the page. Reads only; never pass vault values into page scripts (pages can hook built-ins).
INPUT_VALUES_JS = "() => [...document.querySelectorAll('input, textarea')].map(e => e.value)"
ENTER_TARGET_JS = """() => {
  const el = document.activeElement;
  if (!el) return "";
  const parts = [el.getAttribute("aria-label") || el.getAttribute("name") || el.tagName.toLowerCase()];
  const form = el.form || el.closest("form");
  if (form) for (const b of form.querySelectorAll("button, input[type=submit]")) parts.push(b.innerText || b.value || "");
  return parts.join(" | ");
}"""


class Tools:
    """Every browser action, for every interface (MCP, REST, live view). One code path,
    so the queue, vault scrubbing, approvals, quota and audit log always apply."""

    def __init__(self, config: Config, profiles: ProfileManager, tasks: TaskStore, vault: Vault,
                 audit: AuditLog, approvals: ApprovalStore):
        self._config = config
        self._profiles = profiles
        self._tasks = tasks
        self._vault = vault
        self._audit = audit
        self._approvals = approvals

    def live_url(self, task: Task) -> str:
        return f"{self._config.public_url}/live/{task.id}?token={task.live_token}"

    async def snapshot(self, agent: str, task_id: str) -> str:
        async def run(task: Task, page: Page) -> str:
            return await self._observe(task, page)
        return await self._act(agent, task_id, "snapshot", {}, run)

    async def screenshot(self, agent: str, task_id: str) -> bytes:
        async def run(task: Task, page: Page) -> bytes:
            return await self._masked_screenshot(page)
        return await self._act(agent, task_id, "screenshot", {}, run)

    async def navigate(self, agent: str, task_id: str, url: str) -> str:
        async def run(task: Task, page: Page) -> str:
            await _pw(page.goto(url, wait_until="domcontentloaded", timeout=ACTION_TIMEOUT_MS * 3))
            return await self._observe(task, page)
        return await self._act(agent, task_id, "navigate", {"url": url}, run)

    async def click(self, agent: str, task_id: str, ref: str, irreversible: bool) -> str:
        async def run(task: Task, page: Page) -> str:
            element = await _element(page, ref)
            label = await _label(element)
            if irreversible or is_risky(label, self._config.approval_words):
                return self._request_approval(task, "click", {"ref": ref}, f"Click {label}")
            await _pw(element.click(timeout=ACTION_TIMEOUT_MS))
            return await self._observe_after_action(task)
        return await self._act(agent, task_id, "click", {"ref": ref, "irreversible": irreversible}, run)

    async def fill(self, agent: str, task_id: str, ref: str, text: str | None, secret: str | None) -> str:
        if (text is None) == (secret is None):
            raise OpenBrowserError("Give exactly one of text or secret.")

        async def run(task: Task, page: Page) -> str:
            element = await _element(page, ref)
            value = text if secret is None else self._vault.value_for(secret, page.url)
            await _pw(element.fill(value, timeout=ACTION_TIMEOUT_MS))
            return await self._observe_after_action(task)
        audit_input = {"ref": ref, "secret": secret} if secret else {"ref": ref, "text": text}
        return await self._act(agent, task_id, "fill", audit_input, run)

    async def press(self, agent: str, task_id: str, key: str, irreversible: bool) -> str:
        async def run(task: Task, page: Page) -> str:
            target = await _pw(page.evaluate(ENTER_TARGET_JS)) if key == "Enter" else ""
            if irreversible or is_risky(target, self._config.approval_words):
                return self._request_approval(task, "press", {"key": key}, f"Press {key} on: {target or 'page'}")
            await _pw(page.keyboard.press(key))
            return await self._observe_after_action(task)
        return await self._act(agent, task_id, "press", {"key": key, "irreversible": irreversible}, run)

    def list_vault(self) -> str:
        entries = self._vault.entries()
        if not entries:
            return "The vault is empty. Ask the user to add logins with the vault tool."
        lines = [f"- {e.name} ({e.site}): {', '.join(e.fields)}" for e in entries]
        return "Vault entries. Use fill with secret='name.field'.\n" + "\n".join(lines)

    async def resolve_approval(self, task: Task, approval_id: str, approved: bool) -> None:
        """A human approved or rejected a pending action. Approved actions run here, not in the agent."""
        approval = self._approvals.get(approval_id)
        if approval.task_id != task.id:
            raise OpenBrowserError("That approval belongs to another task.")
        approval = self._approvals.resolve(approval_id, approved)
        self._audit.record("human", "approve" if approved else "reject", {"approval_id": approval_id},
                           approval.description, task.id, task.profile, approval_id)
        note = f"Rejected by the user: {approval.description}. Do not try it again."
        if approved:
            page = await self._page(task)
            try:
                if approval.action == "click":
                    await _pw((await _element(page, approval.args["ref"])).click(timeout=ACTION_TIMEOUT_MS))
                else:
                    await _pw(page.keyboard.press(approval.args["key"]))
                note = f"Approved by the user and done: {approval.description}. Take a snapshot to continue."
                result = "ok"
            except OpenBrowserError as e:
                note = f"Approved by the user, but it failed: {e}"
                result = f"error: {e}"
            self._audit.record("human", approval.action, approval.args, result, task.id, task.profile, approval_id)
        self._tasks.set_state(task.id, "running", expected="waiting_approval")
        self._tasks.steer(task.id, note)

    async def page(self, task: Task) -> Page:
        return await self._page(task)

    async def _act(self, agent: str, task_id: str, action: str, input: dict,
                   run: Callable[[Task, Page], Awaitable[T]]) -> T:
        if self._audit.actions_last_day() >= self._config.max_actions_per_day:
            raise OpenBrowserError(f"Daily action limit reached ({self._config.max_actions_per_day}).")
        task = self._tasks.require_running(task_id)
        scrubbed_input = {k: self._vault.scrub(v) if isinstance(v, str) else v for k, v in input.items()}
        try:
            result = await run(task, await self._page(task))
        except OpenBrowserError as e:
            self._audit.record(agent, action, scrubbed_input, self._vault.scrub(f"error: {e}"), task.id, task.profile)
            raise
        # Tools are blocked while an approval is pending, so a pending one was created by this call.
        approval = self._approvals.pending_for(task.id)
        self._audit.record(agent, action, scrubbed_input, "waiting for approval" if approval else "ok",
                           task.id, task.profile, approval.id if approval else None)
        return result

    def _request_approval(self, task: Task, action: str, args: dict, description: str) -> str:
        description = self._vault.scrub(description)
        self._approvals.create(task.id, action, args, description)
        self._tasks.set_state(task.id, "waiting_approval", expected="running")
        return (
            f"Approval required before: {description}.\n"
            f"A human must approve it in the live view: {self.live_url(task)}\n"
            "Do nothing else on this task until then. Call list_tasks to see when it is running again."
        )

    async def _page(self, task: Task) -> Page:
        """The newest open tab of the task's profile."""
        context = await self._profiles.open(task.profile)
        if not context.pages:
            await context.new_page()
        return context.pages[-1]

    async def _observe_after_action(self, task: Task) -> str:
        # An action can open a tab or start a navigation; snapshot whatever is newest once it has loaded.
        page = await self._page(task)
        await _pw(page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS * 3))
        return await self._observe(task, page)

    async def _observe(self, task: Task, page: Page) -> str:
        tree = compact(await _pw(page.aria_snapshot(mode="ai")))
        parts = [f"URL: {page.url}", f"Title: {await _pw(page.title())}", "", tree]
        notes = self._tasks.take_notes(task.id)
        if notes:
            parts += ["", "Messages from the user:"] + [f"- {n}" for n in notes]
        return self._vault.scrub("\n".join(parts))

    async def _masked_screenshot(self, page: Page) -> bytes:
        secrets = self._vault.secret_labels()
        input_values = await _pw(page.evaluate(INPUT_VALUES_JS))
        inputs = page.locator("input, textarea")
        masks = [inputs.nth(i) for i, v in enumerate(input_values) if v in secrets]
        masks += [page.get_by_text(value) for value in secrets]
        return await _pw(page.screenshot(type="png", mask=masks))


async def _element(page: Page, ref: str) -> Locator:
    locator = page.locator(f"aria-ref={ref}")
    if await _pw(locator.count()) == 0:
        raise OpenBrowserError(f"No element with ref {ref} on this page. Take a new snapshot.")
    return locator


async def _label(element: Locator) -> str:
    """How the element reads in a snapshot, like: button "Place order".
    Must use mode="ai": a default-mode snapshot resets the page's refs and breaks aria-ref locators."""
    first = (await _pw(element.aria_snapshot(mode="ai"))).splitlines()[0]
    return re.sub(r" \[(ref|cursor)=[^\]]+\]", "", first).removeprefix("- ").rstrip(":")


async def _pw(awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except PlaywrightError as e:
        raise OpenBrowserError(str(e).splitlines()[0]) from e
