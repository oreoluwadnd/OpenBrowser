"""One web app: MCP at /mcp, REST at /api, live view at /live, vault page at /vault, OAuth sign-in."""

from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route, WebSocketRoute

from openbrowser.api import create_api
from openbrowser.config import Config
from openbrowser.live import LiveView, page_html
from openbrowser.oauth import OAuthProvider
from openbrowser.runtime import Runtime
from openbrowser.server import create_mcp


def create_app(config: Config) -> Starlette:
    runtime = Runtime(config)
    oauth = OAuthProvider(runtime.db, config.api_key, config.public_url)
    mcp = create_mcp(runtime, oauth)
    live = LiveView(runtime)

    async def vault_page(_: Request) -> HTMLResponse:
        return HTMLResponse(page_html("vault.html"))

    # The MCP app brings the OAuth endpoints, token checking for every route, and the runtime lifespan.
    public = urlsplit(config.public_url)
    app = mcp.streamable_http_app(transport_security=TransportSecuritySettings(
        # Only accept requests addressed to our own public URL (blocks DNS rebinding from web pages).
        allowed_hosts=[public.netloc],
        allowed_origins=[f"{public.scheme}://{public.netloc}"],
    ))
    app.router.routes.extend([
        Route("/login", oauth.login_page, methods=["GET", "POST"]),
        Route("/vault", vault_page),
        Route("/live/{task_id}", live.page),
        WebSocketRoute("/live/{task_id}/ws", live.socket),
        Mount("/api", create_api(runtime)),
    ])
    app.state.runtime = runtime
    return app
