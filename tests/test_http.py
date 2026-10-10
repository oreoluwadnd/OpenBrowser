"""The real server over HTTP: REST API, OAuth sign-in, MCP with a bearer token."""

import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from login_site import ref
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.anyio


async def test_api_requires_a_key(server):
    async with httpx2.AsyncClient(base_url=server.url) as http:
        assert (await http.get("/api/tasks")).status_code == 401
        assert (await http.get("/api/tasks", headers={"Authorization": "Bearer wrong"})).status_code == 401


async def test_rest_task_flow(server, site):
    async with httpx2.AsyncClient(base_url=server.url, headers=server.headers, timeout=60) as http:
        task = (await http.post("/api/tasks", json={"profile": "main", "goal": "log in"})).json()
        assert task["state"] == "running"
        assert task["live_url"].startswith(f"{server.url}/live/{task['id']}?token=")
        tid = task["id"]
        snap = (await http.post(f"/api/tasks/{tid}/navigate", json={"url": f"{site.url}/login"})).json()["snapshot"]
        await http.post(f"/api/tasks/{tid}/fill", json={"ref": ref(snap, "textbox", "Email"), "text": "ada@example.com"})
        await http.post(f"/api/tasks/{tid}/fill", json={"ref": ref(snap, "textbox", "Password"), "text": "pw"})
        result = (await http.post(f"/api/tasks/{tid}/click",
                                  json={"ref": ref(snap, "button", "Sign in"), "irreversible": False})).json()
        assert "Signed in as ada@example.com" in result["snapshot"]
        shot = await http.get(f"/api/tasks/{tid}/screenshot")
        assert shot.headers["content-type"] == "image/png"

        bad = await http.post(f"/api/tasks/{tid}/click", json={"ref": "e999", "irreversible": False})
        assert bad.status_code == 400 and "No element with ref e999" in bad.json()["detail"]

        audit = (await http.get("/api/audit", params={"task_id": tid})).json()
        assert {row["agent"] for row in audit} == {"rest:api-key"}
        assert (await http.post(f"/api/tasks/{tid}/close")).json()["state"] == "closed"
        assert "/tasks/{task_id}/click" in (await http.get("/api/openapi.json")).json()["paths"]


async def test_rest_vault_never_returns_values(server):
    async with httpx2.AsyncClient(base_url=server.url, headers=server.headers) as http:
        put = await http.put("/api/vault/github", json={"site": "github.com", "fields": {"password": "hunter2"}})
        assert put.json() == {"name": "github", "site": "github.com", "fields": ["password"]}
        listed = await http.get("/api/vault")
        assert "hunter2" not in listed.text
        assert (await http.delete("/api/vault/github")).status_code == 204
        assert (await http.get("/api/vault")).json() == []


async def test_vault_page_is_served(server):
    async with httpx2.AsyncClient(base_url=server.url) as http:
        page = await http.get("/vault")
        assert page.status_code == 200 and "OpenBrowser API key" in page.text


async def test_oauth_sign_in_then_mcp(server):
    """The flow the Claude apps use: discovery, registration, consent, PKCE token exchange, refresh."""
    async with httpx2.AsyncClient(base_url=server.url, follow_redirects=False) as http:
        unauth = await http.post("/mcp", json={})
        assert unauth.status_code == 401
        assert "resource_metadata=" in unauth.headers["www-authenticate"]

        resource = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert resource["resource"] == f"{server.url}/mcp"
        assert resource["authorization_servers"] == [server.url]
        meta = (await http.get("/.well-known/oauth-authorization-server")).json()
        assert "S256" in meta["code_challenge_methods_supported"] and meta["registration_endpoint"]

        redirect = "http://localhost:5555/callback"
        client = (await http.post("/register", json={
            "redirect_uris": [redirect], "client_name": "Test app", "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        })).json()

        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        authorize = await http.get("/authorize", params={
            "response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
        })
        login_url = authorize.headers["location"]
        request_id = parse_qs(urlsplit(login_url).query)["request"][0]
        consent = await http.get(login_url)
        assert "Test app" in consent.text and "localhost" in consent.text and "your own computer" in consent.text

        wrong = await http.post("/login", data={"request": request_id, "api_key": "nope"})
        assert wrong.status_code == 401
        allowed = await http.post("/login", data={"request": request_id, "api_key": "test-api-key"})
        back = urlsplit(allowed.headers["location"])
        assert f"{back.scheme}://{back.netloc}{back.path}" == redirect
        query = parse_qs(back.query)
        assert query["state"] == ["xyz"]

        tokens = (await http.post("/token", data={
            "grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": redirect,
            "client_id": client["client_id"], "code_verifier": verifier,
        })).json()
        reused = await http.post("/token", data={
            "grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": redirect,
            "client_id": client["client_id"], "code_verifier": verifier,
        })
        assert reused.status_code == 400  # codes work once

        bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert (await http.get("/api/tasks", headers=bearer)).status_code == 200
        async with httpx2.AsyncClient(headers=bearer, timeout=30) as mcp_http:
            async with Client(streamable_http_client(f"{server.url}/mcp", http_client=mcp_http)) as mcp:
                listed = await mcp.call_tool("list_tasks", {})
                assert listed.content[0].text == "No open tasks."

        refreshed = (await http.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client["client_id"],
        })).json()
        assert refreshed["access_token"] != tokens["access_token"]
        replay = await http.post("/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client["client_id"],
        })
        assert replay.status_code == 400  # refresh tokens rotate


async def test_api_key_works_as_mcp_bearer(server):
    async with httpx2.AsyncClient(headers=server.headers, timeout=30) as mcp_http:
        async with Client(streamable_http_client(f"{server.url}/mcp", http_client=mcp_http)) as mcp:
            spawned = (await mcp.call_tool("spawn_task", {"profile": "main", "goal": "x"})).content[0].text
    task_id = re.search(r"Task (\w+)", spawned).group(1)
    assert [r.agent for r in server.runtime.audit.rows(task_id=task_id)] == ["mcp:api-key"]
