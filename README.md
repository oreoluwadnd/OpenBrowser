# OpenBrowser

Open-source persistent browser runtime any AI agent can drive via MCP. Log in once, stay logged in.

OpenBrowser runs Chromium with named profiles that keep their logins across tasks and restarts. Any agent that speaks MCP (Claude, other agent frameworks, plain scripts) connects and drives it. There is no model inside; the connected agent is the brain.

Status: phase 1 of [the plan](docs/PLAN.md). Profiles, tasks and browser tools over MCP work. Vault, live view, REST and approvals come next.

## Run it

```sh
uv sync
uv run playwright install chromium
export OPENBROWSER_VAULT_KEY=$(uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
uv run openbrowser serve
```

Keep the key: it encrypts saved logins. Losing it means logging in again.

Settings (environment variables):

| Variable | Default | |
|---|---|---|
| `OPENBROWSER_VAULT_KEY` | required | Encryption key for saved session data |
| `OPENBROWSER_DATA_DIR` | `data` | Profiles and database |
| `OPENBROWSER_CHROMIUM` | Playwright's Chromium | Path to a Chromium binary |

## Connect Claude Code

```sh
claude mcp add --transport http openbrowser http://127.0.0.1:8931/mcp
```

Then ask Claude something like "Using openbrowser on profile `work`, log in to ... and ...".

## Tools

| Tool | What it does |
|---|---|
| `spawn_task(profile, goal)` | Join the profile's queue. One task per profile runs at a time. |
| `list_tasks(profile?)` | Open tasks and queue positions |
| `steer_task(task_id, message)` | Send a note to the agent on that task, shown with its next result |
| `close_task(task_id)` | Finish and hand the profile to the next task |
| `snapshot(task_id)` | The page as an accessibility tree; each element has a ref like `e12` |
| `screenshot(task_id)` | PNG of the page |
| `navigate(task_id, url)` | Go to a URL |
| `click(task_id, ref)` | Click an element |
| `fill(task_id, ref, text)` | Type into an input |
| `press(task_id, key)` | Press a key like `Enter` |

Every action returns the new snapshot. A task with no action for 10 minutes is closed.

## Test

```sh
uv run pytest
```
