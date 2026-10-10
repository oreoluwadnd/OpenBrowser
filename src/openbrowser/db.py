import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    goal TEXT NOT NULL,
    state TEXT NOT NULL,
    live_token TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    last_active_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS vault (
    name TEXT PRIMARY KEY,
    site TEXT NOT NULL,
    fields BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    agent TEXT NOT NULL,
    task_id TEXT,
    profile TEXT,
    action TEXT NOT NULL,
    input TEXT NOT NULL,
    result TEXT NOT NULL,
    approval_id TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    action TEXT NOT NULL,
    args TEXT NOT NULL,
    description TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    info TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_grants (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    """One connection, used only from the event loop thread."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db
