"""OAuth sign-in so the Claude apps (web, desktop, mobile) can connect.

Claude registers itself (dynamic client registration), sends you to /login, and you
approve by entering your OpenBrowser API key. The API key also works directly as a
bearer token, for clients that send headers (Claude Code, scripts).
"""

import html
import json
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 86400
PENDING_SECONDS = 600


class OAuthProvider:
    def __init__(self, db: sqlite3.Connection, api_key: str, public_url: str):
        self._db = db
        self._api_key = api_key
        self._public_url = public_url

    def is_api_key(self, token: str) -> bool:
        return secrets.compare_digest(token.encode(), self._api_key.encode())

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = self._db.execute("SELECT info FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row["info"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO oauth_clients (client_id, info) VALUES (?, ?)",
            (client_info.client_id, client_info.model_dump_json()),
        )
        self._db.commit()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id = self._grant(
            "pending", {"client_id": client.client_id, "params": params.model_dump(mode="json")}, PENDING_SECONDS
        )
        return f"{self._public_url}/login?request={request_id}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, code: str) -> AuthorizationCode | None:
        data = self._load("code", code)
        if data is None or data["client_id"] != client.client_id:
            return None
        return AuthorizationCode.model_validate(data)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, code: AuthorizationCode) -> OAuthToken:
        self._delete(code.code)
        return self._issue(client.client_id, code.scopes, code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        data = self._load("refresh", refresh_token)
        if data is None or data["client_id"] != client.client_id:
            return None
        return RefreshToken.model_validate(data)

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        self._delete(refresh_token.token)  # rotate: each refresh token works once
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        if self.is_api_key(token):
            return AccessToken(token=token, client_id="api-key", scopes=[])
        data = self._load("access", token)
        return AccessToken.model_validate(data) if data else None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._delete(token.token)

    async def login_page(self, request: Request) -> Response:
        """Consent screen. GET shows it, POST checks the API key and sends the user back to the client."""
        if request.method == "GET":
            pending = self._load("pending", request.query_params.get("request", ""))
            if pending is None:
                return HTMLResponse(_page("This sign-in link expired. Start again from your app."), status_code=400)
            client = await self.get_client(pending["client_id"])
            redirect_host = urlsplit(pending["params"]["redirect_uri"]).hostname
            return HTMLResponse(_page(_consent_form(request.query_params["request"], client, redirect_host)))

        form = await request.form()
        request_id = str(form.get("request", ""))
        pending = self._load("pending", request_id)
        if pending is None:
            return HTMLResponse(_page("This sign-in link expired. Start again from your app."), status_code=400)
        if not self.is_api_key(str(form.get("api_key", ""))):
            client = await self.get_client(pending["client_id"])
            redirect_host = urlsplit(pending["params"]["redirect_uri"]).hostname
            return HTMLResponse(_page(_consent_form(request_id, client, redirect_host, error=True)), status_code=401)
        self._delete(request_id)
        params = pending["params"]
        code = self._grant("code", {
            "code": "",
            "scopes": params["scopes"] or [],
            "expires_at": time.time() + PENDING_SECONDS,
            "client_id": pending["client_id"],
            "code_challenge": params["code_challenge"],
            "redirect_uri": params["redirect_uri"],
            "redirect_uri_provided_explicitly": params["redirect_uri_provided_explicitly"],
            "resource": params["resource"],
        }, PENDING_SECONDS)
        return RedirectResponse(construct_redirect_uri(params["redirect_uri"], code=code, state=params["state"]),
                                status_code=302)

    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        now = int(time.time())
        access = self._grant("access", {"token": "", "client_id": client_id, "scopes": scopes,
                                        "expires_at": now + ACCESS_SECONDS, "resource": resource}, ACCESS_SECONDS)
        refresh = self._grant("refresh", {"token": "", "client_id": client_id, "scopes": scopes,
                                          "expires_at": now + REFRESH_SECONDS, "resource": resource}, REFRESH_SECONDS)
        return OAuthToken(access_token=access, expires_in=ACCESS_SECONDS, refresh_token=refresh,
                          scope=" ".join(scopes) or None)

    def _grant(self, kind: str, data: dict, seconds: int) -> str:
        key = secrets.token_urlsafe(32)
        for field in ("code", "token"):
            if field in data:
                data[field] = key
        self._db.execute(
            "INSERT INTO oauth_grants (key, kind, data, expires_at) VALUES (?, ?, ?, ?)",
            (key, kind, json.dumps(data), time.time() + seconds),
        )
        self._db.commit()
        return key

    def _load(self, kind: str, key: str) -> dict | None:
        row = self._db.execute(
            "SELECT data FROM oauth_grants WHERE key = ? AND kind = ? AND expires_at > ?", (key, kind, time.time())
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def _delete(self, key: str) -> None:
        self._db.execute("DELETE FROM oauth_grants WHERE key = ?", (key,))
        self._db.commit()


def _consent_form(request_id: str, client: OAuthClientInformationFull | None, redirect_host: str | None,
                  error: bool = False) -> str:
    name = html.escape((client.client_name if client else None) or "An app")
    host = html.escape(redirect_host or "unknown")
    warning = ""
    if redirect_host in ("localhost", "127.0.0.1"):
        warning = "<p class=warn>This app runs on your own computer. Only continue if you started it.</p>"
    error_html = "<p class=warn>That API key is not right.</p>" if error else ""
    return f"""<h1>Connect to OpenBrowser</h1>
<p><b>{name}</b> wants to drive your OpenBrowser browser. After you allow it, you will be sent to <b>{host}</b>.</p>
{warning}{error_html}
<form method=post action=/login>
<input type=hidden name=request value="{html.escape(request_id)}">
<label>OpenBrowser API key <input type=password name=api_key autofocus required></label>
<button>Allow</button>
</form>"""


def _page(body: str) -> str:
    return f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>OpenBrowser</title>
<style>body{{font:16px system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem}}
label{{display:block;margin:1rem 0}}input[type=password]{{display:block;width:100%;padding:.5rem;margin-top:.25rem}}
button{{padding:.5rem 1.25rem}}.warn{{color:#b00}}</style>{body}"""
