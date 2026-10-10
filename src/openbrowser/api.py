"""REST API: the same tasks, tools and vault as MCP, for agents and scripts that speak HTTP.
OpenAPI docs at /api/docs. Every request needs `Authorization: Bearer <API key or OAuth token>`."""

from dataclasses import asdict
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from pydantic import BaseModel

from openbrowser.errors import OpenBrowserError
from openbrowser.runtime import Runtime
from openbrowser.tasks import Task


class SpawnBody(BaseModel):
    profile: str
    goal: str


class SteerBody(BaseModel):
    message: str


class NavigateBody(BaseModel):
    url: str


class ClickBody(BaseModel):
    ref: str
    irreversible: bool


class FillBody(BaseModel):
    ref: str
    text: str | None = None
    secret: str | None = None


class PressBody(BaseModel):
    key: str
    irreversible: bool


class VaultBody(BaseModel):
    site: str
    fields: dict[str, str]


class TaskOut(BaseModel):
    id: str
    profile: str
    goal: str
    state: str
    queue_position: int
    live_url: str


class SnapshotOut(BaseModel):
    snapshot: str


class VaultEntryOut(BaseModel):
    name: str
    site: str
    fields: list[str]


def create_api(runtime: Runtime) -> FastAPI:
    api = FastAPI(title="OpenBrowser", version="0.1.0",
                  description="Persistent browser runtime for AI agents.")

    @api.exception_handler(OpenBrowserError)
    async def expected_error(_: Request, e: OpenBrowserError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=400)

    def agent(request: Request) -> str:
        user = request.scope.get("user")
        if not isinstance(user, AuthenticatedUser):
            raise HTTPException(401, "Send Authorization: Bearer <API key>.", headers={"WWW-Authenticate": "Bearer"})
        return f"rest:{user.access_token.client_id}"

    Agent = Annotated[str, Depends(agent)]

    def task_out(task: Task) -> TaskOut:
        return TaskOut(id=task.id, profile=task.profile, goal=task.goal, state=task.state,
                       queue_position=runtime.tasks.position(task), live_url=runtime.tools.live_url(task))

    @api.post("/tasks", summary="spawn_task")
    async def spawn_task(body: SpawnBody, who: Agent) -> TaskOut:
        runtime.profiles.allow(body.profile)
        task = runtime.tasks.spawn(body.profile, body.goal)
        runtime.audit.record(who, "spawn_task", {"goal": body.goal}, task.state, task.id, body.profile)
        return task_out(task)

    @api.get("/tasks", summary="list_tasks")
    async def list_tasks(who: Agent, profile: str | None = None) -> list[TaskOut]:
        return [task_out(t) for t in runtime.tasks.open_tasks(profile)]

    @api.get("/tasks/{task_id}")
    async def get_task(task_id: str, who: Agent) -> TaskOut:
        return task_out(runtime.tasks.get(task_id))

    @api.post("/tasks/{task_id}/steer", summary="steer_task")
    async def steer_task(task_id: str, body: SteerBody, who: Agent) -> TaskOut:
        runtime.tasks.steer(task_id, body.message)
        return task_out(runtime.tasks.get(task_id))

    @api.post("/tasks/{task_id}/close", summary="close_task")
    async def close_task(task_id: str, who: Agent) -> TaskOut:
        task = runtime.tasks.get(task_id)
        runtime.tasks.close(task_id)
        runtime.audit.record(who, "close_task", {}, "ok", task_id, task.profile)
        return task_out(runtime.tasks.get(task_id))

    @api.post("/tasks/{task_id}/snapshot")
    async def snapshot(task_id: str, who: Agent) -> SnapshotOut:
        return SnapshotOut(snapshot=await runtime.tools.snapshot(who, task_id))

    @api.get("/tasks/{task_id}/screenshot", response_class=Response,
             responses={200: {"content": {"image/png": {}}}})
    async def screenshot(task_id: str, who: Agent) -> Response:
        return Response(await runtime.tools.screenshot(who, task_id), media_type="image/png")

    @api.post("/tasks/{task_id}/navigate")
    async def navigate(task_id: str, body: NavigateBody, who: Agent) -> SnapshotOut:
        return SnapshotOut(snapshot=await runtime.tools.navigate(who, task_id, body.url))

    @api.post("/tasks/{task_id}/click")
    async def click(task_id: str, body: ClickBody, who: Agent) -> SnapshotOut:
        return SnapshotOut(snapshot=await runtime.tools.click(who, task_id, body.ref, body.irreversible))

    @api.post("/tasks/{task_id}/fill")
    async def fill(task_id: str, body: FillBody, who: Agent) -> SnapshotOut:
        return SnapshotOut(snapshot=await runtime.tools.fill(who, task_id, body.ref, body.text, body.secret))

    @api.post("/tasks/{task_id}/press")
    async def press(task_id: str, body: PressBody, who: Agent) -> SnapshotOut:
        return SnapshotOut(snapshot=await runtime.tools.press(who, task_id, body.key, body.irreversible))

    @api.get("/vault", summary="List vault entries (never values)")
    async def list_vault(who: Agent) -> list[VaultEntryOut]:
        return [VaultEntryOut(**asdict(e)) for e in runtime.vault.entries()]

    @api.put("/vault/{name}", summary="Create or update a vault entry. Blank values keep the old ones.")
    async def put_vault(name: str, body: VaultBody, who: Agent) -> VaultEntryOut:
        runtime.vault.put(name, body.site, body.fields)
        runtime.audit.record(who, "vault_save", {"name": name, "site": body.site, "fields": sorted(body.fields)}, "ok")
        return next(VaultEntryOut(**asdict(e)) for e in runtime.vault.entries() if e.name == name)

    @api.delete("/vault/{name}", status_code=204)
    async def delete_vault(name: str, who: Agent) -> None:
        runtime.vault.delete(name)
        runtime.audit.record(who, "vault_delete", {"name": name}, "ok")

    @api.get("/audit", summary="Action log, newest first")
    async def audit(who: Agent, profile: str | None = None, task_id: str | None = None, limit: int = 200) -> list[dict]:
        return [asdict(r) for r in runtime.audit.rows(profile, task_id, limit)]

    return api
