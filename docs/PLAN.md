# OpenBrowser: Architecture and Status

Status: all planned phases built and tested (48 tests). What is not yet verified is listed in section 6.

Open-source persistent browser runtime any AI agent can drive via REST or MCP. Log in once, stay logged in.

## 1. Evidence

Each design choice rests on a test against Playwright 1.63 (Python), the bundled Chromium and `mcp` 2.3.0.

| # | Test | Result | What it means for the design |
|---|------|--------|------------------------------|
| E1 | `page.aria_snapshot(mode="ai")` | YAML tree with `[ref=eN]` on each element | Snapshots come for free. |
| E2 | `page.locator("aria-ref=e3").fill(...)` / `.click()` | Works | `click` and `fill` take a ref from the snapshot. |
| E3 | Change the page, snapshot again | Old elements keep their ref, new ones get new refs | Refs stay valid across steps. |
| E4 | Close and reopen a persistent profile | Cookies with an expiry and `localStorage` survive. **Session cookies are lost.** | Session cookies are saved encrypted and restored (4.1). |
| E5 | Open one profile folder twice | Chromium refuses ("ProcessSingleton") | One long-running process owns each profile. |
| E6 | Fill a password, then snapshot | **Password shows in plain text** | Every snapshot and log entry is scrubbed (4.4). |
| E7 | Headless: `Page.startScreencast` + input events | Real frames out, human input lands | Live view works without a window (4.7). |
| E8 | Read `mcp` source: Apps, elicitation | MCP Apps can show HTML in the chat; `visibility=["app"]` hides a tool from the model; form elicitation must not carry passwords | Vault form and live view are MCP Apps. |
| E9 | Real Claude CLI over MCP, log in to a test site | Done in 8 steps. Results first arrived as one escaped JSON string; fixed with `structured_output=False` | Tools return plain text. |
| E10 | stdio vs HTTP | With stdio every Claude window starts its own runtime, and E5 makes two runtimes clash | MCP is HTTP only. |
| E11 | Two MCP clients on one HTTP server | Server lifespan ran once, not per client | One runtime per server is safe. |
| E12 | Parallel first tool calls on a new profile | Second Chromium launch crashed (E5) | A lock around opening a profile. |
| E13 | Snapshot size, github.com/microsoft/playwright | 80k chars (~20k tokens): 33% indentation, 20% link URLs, 10% empty wrappers | Drop URLs, empty wrappers and redundant cursor marks: 47% smaller, no actionable ref lost (4.3). |
| E14 | `locator.aria_snapshot()` (default mode) on an element | **Resets the page's refs**: `aria-ref` locators stop matching (count 1 to 0). `mode="ai"` keeps them | Element labels use `mode="ai"`. |
| E15 | Page that hooks `String.prototype`, `RegExp.prototype`, `Array.prototype` | Passing a value into `page.evaluate`: 5669 captures. Playwright `get_by_text` and screenshot `mask`: 0 | Vault values never go into page scripts; masking uses locators only. |
| E16 | claude.ai connector docs | Claude's apps need OAuth (dynamic registration or CIMD, PKCE S256, 401 with `resource_metadata`, callback `https://claude.ai/api/mcp/auth_callback`). Static headers are a limited beta | Built-in OAuth sign-in (4.8). |
| E17 | uvicorn without a WebSocket library | No `websockets` or `wsproto` installed, so the live view socket could not open | `websockets` is a dependency. |
| E18 | Real Claude CLI with the full server: "log in with my saved login, then place the order" | Used `list_vault` and `fill(secret=...)`, saw only `[secret:...]`, set `irreversible=true` on "Place order", stopped and gave the live view link. Email and password appear 0 times in the transcript and the action log | Vault, scrubbing and approvals work with a real agent. |
| E19 | Bot check (BrowserScan), from your run | `navigator.webdriver = true`, user agent `HeadlessChrome` | True facts about the browser. OpenBrowser will not hide them; sites that block bots will block it. |
| E20 | Memory and reopen time, 4 cores / 16 GB | One browser 292 MB; 10 browsers 145 MB each (GitHub page). Reopening a closed profile: 0.3 s (2.8 s the first time) | Close idle browsers: memory follows browsers in use, not profiles ever used. 500 at once is roughly 75 to 150 GB of RAM. |

## 2. Shape

One long-running process (`openbrowser serve`), one web app:

| Path | What |
|---|---|
| `/mcp` | MCP over streamable HTTP, bearer token required |
| `/api` | REST, same tasks/tools/vault, OpenAPI at `/api/docs` |
| `/live/{task}?token=` | Live view page and its WebSocket |
| `/vault` | Vault page for browsers (API key) |
| `/login`, `/authorize`, `/token`, `/register`, `/.well-known/*` | OAuth sign-in |

No model inside. The connected agent is the brain. Every browser action from MCP, REST or an approval goes through one tool layer (`tools.py`), so the queue, vault scrubbing, approvals, quota and action log always apply.

Files: `config.py`, `db.py` (one SQLite file), `profiles.py`, `tasks.py`, `snapshot.py`, `vault.py`, `approvals.py`, `audit.py`, `tools.py`, `live.py`, `oauth.py`, `server.py` (MCP), `api.py` (REST), `app.py`, `web/` (live view, vault page, chat bridge).

## 3. Tasks

- `spawn_task(profile, goal)` joins that profile's FIFO queue and returns a live view link. The head of the queue holds the profile.
- States: `queued -> running -> (waiting_approval | human_control -> running)* -> closed`. Tools only work while `running`; other states return a readable reason.
- `steer_task` notes reach the agent with its next result. A running task idle for 10 minutes is closed; tasks waiting on a human are not. A restart closes all open tasks.

## 4. Components

### 4.1 Profiles
Folder per profile, `launch_persistent_context(headless=True)`. Session cookies saved encrypted every 15 s, before closing and on shutdown, restored on open (E4). A browser closes after 2 minutes with no open task on its profile (re-checked under the profile lock), and reopens on the next action: logins kept, open tabs lost (E20).

### 4.2 Tool layer
`snapshot`, `screenshot`, `navigate`, `click(ref, irreversible)`, `fill(ref, text | secret)`, `press(key, irreversible)`. Each action returns the new snapshot of the newest tab. Unknown refs fail at once instead of waiting 10 s.

### 4.3 Snapshots
`snapshot.compact` drops link URLs, empty `generic` wrappers (children move up a level) and `[cursor=pointer]` on clickable roles (E13). The agent sees a link's address after it navigates.

### 4.4 Vault
- Entry: name, site, fields (`email`, `password`, anything). Encrypted with `OPENBROWSER_VAULT_KEY`. No way to read a value back.
- `fill(secret="github.password")` only works on that site or its subdomains.
- Every stored value is replaced with `[secret:name.field]` in snapshots, results, approval text and the action log; screenshots mask inputs holding a value and text showing one (E15).
- Edited by the user: the in-chat form (MCP App: `vault` shows it, `vault_save`/`vault_delete` are app-only and refuse clients without MCP Apps, since there the model could see them and move a site lock), the `/vault` page, or REST.

### 4.5 Approvals
A click or Enter is held when the agent sets `irreversible=true` or the element (or the Enter key's form buttons) matches a whole word from: buy, pay, submit, delete, send, confirm, place order. The task waits; a human approves or rejects in the live view; the runtime itself runs the approved action and tells the agent. Human clicks in the live view need no approval.

### 4.6 Action log
One row per action: time, agent (`mcp:<client>`, `rest:<client>`, `human`), task, profile, action, scrubbed input, result, approval id. Human keystrokes are not logged (they can be passwords). `GET /api/audit`.

### 4.7 Live view
JPEG frames from `Page.startScreencast` on the newest tab, over a WebSocket guarded by a per-task token. Take control (agent blocked), mouse and keyboard, hand back (agent told to snapshot first), approve or reject. Same page in a browser tab or inside the chat as an MCP App.

### 4.8 Sign-in
The API key works as a bearer token (Claude Code, scripts). For Claude's apps: OAuth with dynamic client registration, PKCE, a consent page where you enter the API key, one-use codes, rotating refresh tokens (E16). Requests are only accepted for the host in `OPENBROWSER_PUBLIC_URL`.

### 4.9 Hosting
One container per owner (Dockerfile): own volume, keys and public URL. Process isolation instead of tenant columns. Quotas per container: profiles, open tasks, actions per day.

## 5. Decisions changed along the way

- No built-in model (you): the connected agent drives.
- Browser always hidden; you watch through the live view (you).
- Vault and live view in the chat (you), plus browser pages for clients without MCP Apps such as Claude Code.
- HTTP only, no stdio (E10). Idle timeout instead of disconnect detection.
- Multi-tenant by container, not tenant columns.
- No disguising automation (E19).

## 6. Not yet verified

1. **The in-chat apps in real Claude apps.** Built to the MCP Apps spec (2026-01-26) and checked with the SDK, not rendered in Claude.
2. **Whether Claude keeps the vault form's input out of the model's context.** The spec says app-only tools are hidden from the model; not tested with real Claude.
3. **Whether an in-chat page on claude.ai may connect to a runtime on `localhost`.** Browsers can block this. A public HTTPS address avoids it.
4. **OAuth with real claude.ai.** The full flow passes with a scripted client; not run against Claude's servers.
5. **The Docker image.** No Docker daemon here; the install steps were checked outside Docker.
