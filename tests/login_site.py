"""A tiny login site for tests. Logging in sets a session cookie (no expiry)."""

import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

LOGIN_PAGE = b"""<!doctype html><title>Login</title>
<form method=post action=/login>
<label>Email <input name=email type=email></label>
<label>Password <input name=password type=password></label>
<button>Sign in</button>
</form>"""


CHECKOUT_PAGE = b"""<!doctype html><title>Checkout</title>
<button onclick="document.getElementById('out').textContent='Order placed'">Place order</button>
<button onclick="document.getElementById('out').textContent='Coupon applied'">Apply coupon</button>
<form onsubmit="document.getElementById('out').textContent='Message sent'; return false">
<input aria-label="Message"><button>Send message</button></form>
<p id=out></p>"""


class Site:
    def __init__(self):
        sessions: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _html(self, body: bytes, headers: dict[str, str] | None = None):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/login":
                    return self._html(LOGIN_PAGE)
                if self.path == "/checkout":
                    return self._html(CHECKOUT_PAGE)
                if self.path == "/me":
                    cookies = dict(
                        c.strip().split("=", 1) for c in self.headers.get("Cookie", "").split(";") if "=" in c
                    )
                    email = sessions.get(cookies.get("sid", ""))
                    text = f"Signed in as {email}" if email else "Not signed in"
                    return self._html(f"<title>Me</title><h1>{text}</h1>".encode())
                self.send_error(404)

            def do_POST(self):
                form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                sid = secrets.token_hex(8)
                sessions[sid] = form["email"][0]
                self.send_response(303)
                self.send_header("Set-Cookie", f"sid={sid}; Path=/")
                self.send_header("Location", "/me")
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        self._server.shutdown()


def ref(snapshot: str, role: str, name: str) -> str:
    """The ref of the element with this role and name in a snapshot."""
    match = re.search(rf'{role} "{re.escape(name)}".*?\[ref=([a-z0-9]+)\]', snapshot)
    assert match, f"No {role} {name!r} in snapshot:\n{snapshot}"
    return match.group(1)
