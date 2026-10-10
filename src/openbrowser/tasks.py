import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from openbrowser.errors import OpenBrowserError

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    goal TEXT NOT NULL,
    state TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    last_active_at REAL NOT NULL
);
"""


@dataclass(frozen=True)
class Task:
    id: str
    profile: str
    goal: str
    state: str  # queued | running | closed
    created_at: float
    last_active_at: float


class TaskStore:
    """Per-profile FIFO queue. The oldest open task of a profile is the one running."""

    def __init__(self, db_path: Path):
        self._db = sqlite3.connect(db_path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        # Agents that held tasks before a restart are gone with their connections.
        self._db.execute("UPDATE tasks SET state = 'closed' WHERE state != 'closed'")
        self._db.commit()

    def spawn(self, profile: str, goal: str) -> Task:
        now = time.time()
        task_id = uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT INTO tasks (id, profile, goal, state, created_at, last_active_at) VALUES (?, ?, ?, 'queued', ?, ?)",
            (task_id, profile, goal, now, now),
        )
        self._promote(profile)
        return self.get(task_id)

    def get(self, task_id: str) -> Task:
        row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise OpenBrowserError(f"No task {task_id!r}.")
        return _task(row)

    def open_tasks(self, profile: str | None = None) -> list[Task]:
        query = "SELECT * FROM tasks WHERE state != 'closed'"
        args: tuple = ()
        if profile is not None:
            query += " AND profile = ?"
            args = (profile,)
        return [_task(r) for r in self._db.execute(query + " ORDER BY rowid", args)]

    def position(self, task: Task) -> int:
        """How many open tasks of the same profile are ahead of this one."""
        return self._db.execute(
            "SELECT COUNT(*) FROM tasks WHERE profile = ? AND state != 'closed' "
            "AND rowid < (SELECT rowid FROM tasks WHERE id = ?)",
            (task.profile, task.id),
        ).fetchone()[0]

    def require_running(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task.state == "closed":
            raise OpenBrowserError(f"Task {task_id} is closed.")
        if task.state == "queued":
            raise OpenBrowserError(
                f"Task {task_id} is queued: {self.position(task)} task(s) ahead on profile {task.profile!r}. "
                "Try again later."
            )
        self._db.execute("UPDATE tasks SET last_active_at = ? WHERE id = ?", (time.time(), task_id))
        self._db.commit()
        return task

    def steer(self, task_id: str, message: str) -> None:
        task = self.get(task_id)
        if task.state == "closed":
            raise OpenBrowserError(f"Task {task_id} is closed.")
        notes = json.loads(self._db.execute("SELECT notes FROM tasks WHERE id = ?", (task_id,)).fetchone()[0])
        notes.append(message)
        self._db.execute("UPDATE tasks SET notes = ? WHERE id = ?", (json.dumps(notes), task_id))
        self._db.commit()

    def take_notes(self, task_id: str) -> list[str]:
        notes = json.loads(self._db.execute("SELECT notes FROM tasks WHERE id = ?", (task_id,)).fetchone()[0])
        if notes:
            self._db.execute("UPDATE tasks SET notes = '[]' WHERE id = ?", (task_id,))
            self._db.commit()
        return notes

    def close(self, task_id: str) -> None:
        task = self.get(task_id)
        self._db.execute("UPDATE tasks SET state = 'closed' WHERE id = ?", (task_id,))
        self._promote(task.profile)

    def close_idle(self, idle_seconds: float) -> list[str]:
        cutoff = time.time() - idle_seconds
        rows = self._db.execute(
            "SELECT id FROM tasks WHERE state = 'running' AND last_active_at < ?", (cutoff,)
        ).fetchall()
        for row in rows:
            self.close(row["id"])
        return [row["id"] for row in rows]

    def _promote(self, profile: str) -> None:
        running = self._db.execute(
            "SELECT 1 FROM tasks WHERE profile = ? AND state = 'running'", (profile,)
        ).fetchone()
        if running is None:
            self._db.execute(
                """UPDATE tasks SET state = 'running', last_active_at = ? WHERE id = (
                       SELECT id FROM tasks WHERE profile = ? AND state = 'queued' ORDER BY rowid LIMIT 1)""",
                (time.time(), profile),
            )
        self._db.commit()


def _task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        profile=row["profile"],
        goal=row["goal"],
        state=row["state"],
        created_at=row["created_at"],
        last_active_at=row["last_active_at"],
    )
