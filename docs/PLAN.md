# Agent Browser Bridge: Architecture and Build Plan

Status: draft for review. No code yet.

Open-source persistent browser runtime any AI agent can drive via REST or MCP. Log in once, stay logged in.

## 1. Evidence gathered before planning

Each design choice below rests on a test run against Playwright 1.63 (Python) and the bundled Chromium. Scripts were throwaway; results:

| # | Test | Result | What it means for the design |
|---|------|--------|------------------------------|
| E1 | `page.aria_snapshot(mode="ai")` | Returns a YAML tree with `[ref=eN]` on each element | Snapshots come for free. No custom DOM walker. |
| E2 | `page.locator("aria-ref=e3").fill(...)` / `.click()` | Works | `click` and `fill` take a ref from the snapshot directly. |
| E3 | Change the page, snapshot again | Old elements keep their ref (`e4` stayed "Go"), new elements get new refs (`e5`, `e6`) | Refs stay valid across steps while the element lives. Agents do not need to re-map. |
| E4 | Close and reopen a persistent profile | Cookies with an expiry and `localStorage` survive. **Cookies without an expiry (session cookies) are lost.** | Many sites log in with session cookies. A plain persistent profile does NOT keep you logged in across restarts. See 4.1. |
| E5 | Open the same profile folder twice at once | Chromium refuses: "Failed to create a ProcessSingleton" | One profile = one browser process. Leasing is enforced by Chromium itself; the runtime must own that one process. |
| E6 | Fill a password field, then snapshot | **The password appears in plain text** (`textbox "Pass": hunter2`) | Filling from the vault is not enough. Snapshots must be scrubbed. See 4.5. |

## 2. Stack

- Python 3.13
- Playwright 1.63 (`launch_persistent_context`, `aria_snapshot(mode="ai")`)
- MCP Python SDK (`mcp` 2.3.0) for the MCP server
- FastAPI for REST, which also generates the OpenAPI spec
- SQLite for tasks, leases, audit log, vault
- `cryptography` (Fernet) for vault encryption, key from `ABB_VAULT_KEY` env var. Missing key = startup error.

## 3. Shape of the system

One process, one machine (local-first). Multi-tenant comes later (phase 5).

```
             REST (FastAPI)            MCP (stdio + streamable HTTP)
   spawn_task/steer_task/...        snapshot/navigate/click/fill/...
                 \                        /
                  \                      /
                   +---- Runtime core --+
                   |                    |
        Task queue (per profile, FIFO)  Tool layer (one implementation of each action)
                   |                    |
               Worker loop ------------>+
                   |                    |
              Profile manager  (one Chromium process per profile, owns leases)
                   |
           Vault  |  Audit log  |  Approval gate
```

Rule: every browser action goes through the tool layer, whether it comes from MCP or from the built-in worker. One code path, so audit, approval and scrubbing cannot be skipped.

## 4. Components

### 4.1 Profile manager

- A profile is a name plus a folder: `data/profiles/<name>/`.
- Opening a profile launches `launch_persistent_context(folder)`. The context stays open while the runtime runs (E5: only one process can hold it anyway).
- **Session cookies (E4):** on close and every 60s while open, save `context.cookies()` that have no expiry to `data/profiles/<name>/session_cookies.json` (encrypted with the vault key). On open, add them back with `context.add_cookies`. This is the one mechanism for "stay logged in".
- Lease: a profile is held by at most one task or one MCP session at a time. Lease has an owner id and is released on task end or MCP disconnect. Asking for a held profile returns an error naming the holder. No waiting, no stealing.

### 4.2 Task queue

- Per profile FIFO, stored in SQLite.
- States: `queued -> running -> (waiting_approval -> running)* -> done | failed | closed`.
- The head of a profile's queue gets the lease when it's free.

### 4.3 Tool layer (shared by MCP and the worker)

| Tool | Input | Output |
|------|-------|--------|
| `snapshot` | none | scrubbed aria snapshot (E1) + URL + title |
| `screenshot` | none | PNG, vault-filled fields masked with Playwright's `mask=` |
| `navigate` | url | new snapshot |
| `click` | ref | new snapshot |
| `fill` | ref, text OR secret name | new snapshot |
| `press` | key | new snapshot |

Every action returns the next snapshot, so verify is built in. Each call writes one audit row before returning.

### 4.4 Worker loop (built-in brain for `spawn_task`)

```
loop until done/failed/step limit:
  perceive: snapshot (screenshot only if the model asks for one)
  reason:   send goal + history + snapshot to the model, get one tool call or "done"/"fail"
  act:      run the tool through the tool layer
  verify:   the returned snapshot goes into the next turn; model judges progress
  steer:    if steer_task added a message, add it to the next turn
```

Step limit per task (default 50). Hitting it = `failed` with reason.

### 4.5 Vault (fill-only)

- Secrets stored encrypted in SQLite, keyed by name and scoped to a profile.
- API can create, list names, delete. There is no read endpoint for values.
- `fill(ref, secret="github_password")` decrypts inside the tool layer and types it.
- **Scrubbing (E6):** the tool layer keeps the set of secret values filled in this profile's open pages. Every snapshot and audit row has those values replaced with `[secret:github_password]` before leaving the tool layer. Screenshots mask the filled elements.

### 4.6 Approval gate

- Before `click` or `press Enter`, the tool layer asks the model (built-in worker) or the caller (MCP) to label the action: `reversible` or `irreversible`. Plus a fixed rule: any click whose element name matches a configurable word list (buy, pay, submit, delete, send, confirm, place order) is irreversible regardless of label.
- Irreversible = task moves to `waiting_approval`; the action runs only after `POST /tasks/{id}/approve`. Rejected = the model is told and continues.
- For MCP clients: the tool returns `approval_required` with an approval id; the client calls `approve` on behalf of its human.

### 4.7 Audit log

One SQLite row per action: time, agent id (API key name or MCP client name), task id, profile, tool, input (scrubbed), result summary, approval id if any. Append-only. `GET /audit?profile=&task=`.

## 5. Interfaces

### REST (high level)

- `POST /tasks` (`spawn_task`): profile, goal, optional step limit. Returns task id.
- `POST /tasks/{id}/steer` (`steer_task`): message.
- `GET /tasks` (`list_tasks`): filter by profile and state.
- `POST /tasks/{id}/close` (`close_task`): stops it, releases lease.
- `POST /tasks/{id}/approve` and `/reject`
- `POST /profiles`, `GET /profiles`, `POST /profiles/{name}/vault`
- Auth: API key in header. Each key has a name, used as agent id in the audit log.

### MCP (low level)

- `open_profile(name)` takes the lease for this session, then the tools in 4.3, then `close_profile`.
- The REST task tools are also exposed over MCP so an MCP-only agent can hand off whole tasks.

## 6. Build phases

Each phase ends with something runnable and tested.

1. **Profiles + tool layer + MCP.** Persistent profiles with session-cookie restore, the six tools, MCP server. Test: log in to a test site, restart runtime, still logged in. Test: two MCP clients on one profile, second gets the lease error.
2. **Vault + scrubbing + audit.** Test: fill a password from vault, assert it is absent from snapshot, audit row and screenshot.
3. **Task queue + worker loop + REST.** Test: FIFO order per profile; spawn a task against a local test site and reach done.
4. **Approval gate.** Test: a "Place order" click stops at `waiting_approval` and runs only after approve.
5. **Multi-tenant hosting.** Tenant id on every table, per-tenant profile folders, quotas (profiles, concurrent tasks, steps per day), one OS user or container per tenant for isolation.

Phases 1 and 2 already deliver the core promise for any external agent. The built-in worker is phase 3.

## 7. Decisions needed from you

1. **Model for the built-in worker.** Proposal: one provider in v1 (Anthropic API), model set by env var. Other providers later only if asked for.
2. **Headless or headed by default.** Proposal: headed for local use (you can watch and log in by hand), headless for hosted.
3. **First login.** Proposal: you log in by hand in the headed window once (`abb login <profile>`), not through the agent.
4. **Approval word list.** Is the list in 4.6 the right starting set?
5. **Name.** Repo is `OpenBrowser`, description says "Agent Browser Bridge". Which one goes on the package and CLI?
