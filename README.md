# OpenBrowser

Open-source persistent browser runtime any AI agent can drive via MCP or REST. Log in once, stay logged in.

OpenBrowser runs Chromium with named profiles that keep their logins across tasks and restarts. Any agent (Claude, other frameworks, plain scripts) connects and drives it. There is no model inside; the connected agent is the brain.

- **Profiles that stay logged in**, including session cookies Chromium would drop on restart
- **Tasks**: one agent per profile at a time, others wait in line
- **Live view**: watch the browser, take control to log in or solve something, hand it back
- **Vault**: saved logins the agent can type into their own site but never read
- **Approvals**: buying, sending, submitting or deleting waits for a human
- **Action log** of everything, per agent

OpenBrowser does not hide that it is automated. Sites that block bots will block it.

## Run it

```sh
uv sync
uv run playwright install chromium
export OPENBROWSER_VAULT_KEY=$(uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export OPENBROWSER_API_KEY=$(uv run python -c "import secrets; print(secrets.token_urlsafe(32))")
uv run openbrowser serve
```

Keep both keys. The vault key encrypts saved logins; losing it means saving them again. The API key is how you (and your agents) get in.

| Variable | Default | |
|---|---|---|
| `OPENBROWSER_VAULT_KEY` | required | Encrypts the vault and saved session cookies |
| `OPENBROWSER_API_KEY` | required | Bearer token for MCP and REST, and the password on the sign-in page |
| `OPENBROWSER_PUBLIC_URL` | `http://127.0.0.1:8931` | The address clients use. Requests for any other host are refused |
| `OPENBROWSER_DATA_DIR` | `data` | Profiles and database |
| `OPENBROWSER_CHROMIUM` | Playwright's Chromium | Path to a Chromium binary |
| `OPENBROWSER_MAX_PROFILES` | 20 | |
| `OPENBROWSER_MAX_OPEN_TASKS` | 50 | |
| `OPENBROWSER_MAX_ACTIONS_PER_DAY` | 5000 | |

## Connect an agent

**Claude Code**

```sh
claude mcp add --transport http openbrowser http://127.0.0.1:8931/mcp --header "Authorization: Bearer $OPENBROWSER_API_KEY"
```

Use `127.0.0.1`, not `localhost`: OpenBrowser only answers to its public URL.

**Claude on the web, desktop or phone**: these connect from Anthropic's servers, so OpenBrowser needs a public HTTPS address (see Hosting). Add it as a custom connector with URL `https://<your host>/mcp`. Claude sends you to an OpenBrowser sign-in page; enter your API key there.

**Anything else**: REST at `/api`, docs at `/api/docs`. Send `Authorization: Bearer <API key>`.

## In use

1. The agent calls `spawn_task(profile, goal)` and gets a live view link. Apps that support MCP Apps can show the live view inside the chat (built to the spec, not yet tried in Claude's apps).
2. First time on a site: open the live view, **Take control**, log in yourself, **Hand back**. The profile stays logged in.
3. Or save logins in the vault: ask Claude to open the vault (a form in the chat), or open `/vault` in a browser. The agent fills them with `fill(ref, secret="github.password")` and only ever sees `[secret:github.password]`. A vault entry only fills on its own site.
4. When the agent tries something irreversible, it stops. Approve or reject in the live view.

## MCP tools

| Tool | |
|---|---|
| `spawn_task(profile, goal)` | Join the profile's queue. Returns a live view link |
| `live_view(task_id)` | Show the live view again |
| `list_tasks(profile?)`, `steer_task(task_id, message)`, `close_task(task_id)` | |
| `snapshot(task_id)` | The page as an accessibility tree; elements have refs like `e12` |
| `screenshot(task_id)` | PNG, with vault values masked |
| `navigate`, `click(ref, irreversible)`, `fill(ref, text \| secret)`, `press(key, irreversible)` | Each returns the new snapshot |
| `list_vault()`, `vault()` | Vault entry names and fields; `vault` opens the form for the user |

## Hosting

One container per owner: own volume, own keys, own public URL behind HTTPS.

```sh
docker build -t openbrowser .
docker run -d -p 8931:8931 -v openbrowser-ada:/data \
  -e OPENBROWSER_VAULT_KEY=... -e OPENBROWSER_API_KEY=... \
  -e OPENBROWSER_PUBLIC_URL=https://ada.browser.example.com openbrowser
```

Put a TLS proxy (Caddy, nginx) in front, forwarding WebSockets for `/live/*`.

## Security notes

- The agent never receives vault values: they are replaced in snapshots, results and the action log, and masked in screenshots. The live view shows the real screen; it is for you.
- Approvals stop mistakes and pages trying to talk the agent into something. They do not stop an agent that has your API key and a shell, since it could call the API itself.
- The live view link is a key to that task's browser. Share it only with whoever should drive.

## Test

```sh
uv run pytest
```
