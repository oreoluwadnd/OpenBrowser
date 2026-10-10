import json
import secrets
import sqlite3
import time
from dataclasses import dataclass

from openbrowser.errors import OpenBrowserError

# queued -> running -> (waiting_approval | human_control -> running)* -> closed


@dataclass(frozen=True)
class Task:
    id: str
    profile: str
    goal: str
    state: str
    live_token: str
    created_at: float
    last_active_at: float


class TaskStore:
    """Per-profile FIFO queue. The oldest open task of a profile holds the profile."""

    def __init__(self, db: sqlite3.Connection, max_open_tasks: int):
        self._db = db
        self._max_open_tasks = max_open_tasks
        # Agents that held tasks before a restart are gone with their connections.
        self._db.execute("UPDATE tasks SET state = 'closed' WHERE state != 'closed'")
        self._db.commit()

    def spawn(self, profile: str, goal: str) -> Task:
        if len(self.open_tasks()) >= self._max_open_tasks:
            raise OpenBrowserError(f"Too many open tasks (limit {self._max_open_tasks}). Close some first.")
        now = time.time()
        task_id = secrets.token_hex(6)
        self._db.execute(
            "INSERT INTO tasks (id, profile, goal, state, live_token, created_at, last_active_at)"
            " VALUES (?, ?, ?, 'queued', ?, ?, ?)",
            (task_id, profile, goal, secrets.token_urlsafe(24), now, now),
        )
        self._promote(profile)
        return self.get(task_id)

    def get(self, task_id: str) -> Task:
        row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise OpenBrowserError(f"No task {task_id!r}.")
        return _task(row)

    def by_live_token(self, task_id: str, token: str) -> Task:
        task = self.get(task_id)
        if not secrets.compare_digest(task.live_token, token):
            raise OpenBrowserError("Invalid live view token.")
        return task

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
        """The task, if its agent may use the browser now."""
        task = self.get(task_id)
        match task.state:
            case "closed":
                raise OpenBrowserError(f"Task {task_id} is closed.")
            case "queued":
                raise OpenBrowserError(
                    f"Task {task_id} is queued: {self.position(task)} task(s) ahead on profile {task.profile!r}. "
                    "Try again later."
                )
            case "waiting_approval":
                raise OpenBrowserError(
                    f"Task {task_id} is waiting for a human to approve an action. Call list_tasks to check, then continue."
                )
            case "human_control":
                raise OpenBrowserError(f"A human has control of task {task_id}'s browser. Try again later.")
        self.touch(task_id)
        return task

    def touch(self, task_id: str) -> None:
        self._db.execute("UPDATE tasks SET last_active_at = ? WHERE id = ?", (time.time(), task_id))
        self._db.commit()

    def set_state(self, task_id: str, state: str, expected: str) -> None:
        task = self.get(task_id)
        if task.state != expected:
            raise OpenBrowserError(f"Task {task_id} is {task.state}, not {expected}.")
        self._db.execute(
            "UPDATE tasks SET state = ?, last_active_at = ? WHERE id = ?", (state, time.time(), task_id)
        )
        self._db.commit()

    def steer(self, task_id: str, message: str) -> None:
        if self.get(task_id).state == "closed":
            raise OpenBrowserError(f"Task {task_id} is closed.")
        notes = self._notes(task_id)
        notes.append(message)
        self._db.execute("UPDATE tasks SET notes = ? WHERE id = ?", (json.dumps(notes), task_id))
        self._db.commit()

    def take_notes(self, task_id: str) -> list[str]:
        notes = self._notes(task_id)
        if notes:
            self._db.execute("UPDATE tasks SET notes = '[]' WHERE id = ?", (task_id,))
            self._db.commit()
        return notes

    def close(self, task_id: str) -> None:
        task = self.get(task_id)
        self._db.execute("UPDATE tasks SET state = 'closed' WHERE id = ?", (task_id,))
        self._promote(task.profile)

    def close_idle(self, idle_seconds: float) -> list[str]:
        """Close running tasks whose agent went quiet. Tasks waiting on a human are left alone."""
        cutoff = time.time() - idle_seconds
        rows = self._db.execute(
            "SELECT id FROM tasks WHERE state = 'running' AND last_active_at < ?", (cutoff,)
        ).fetchall()
        for row in rows:
            self.close(row["id"])
        return [row["id"] for row in rows]

    def _notes(self, task_id: str) -> list[str]:
        return json.loads(self._db.execute("SELECT notes FROM tasks WHERE id = ?", (task_id,)).fetchone()[0])

    def _promote(self, profile: str) -> None:
        holder = self._db.execute(
            "SELECT 1 FROM tasks WHERE profile = ? AND state NOT IN ('queued', 'closed')", (profile,)
        ).fetchone()
        if holder is None:
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
        live_token=row["live_token"],
        created_at=row["created_at"],
        last_active_at=row["last_active_at"],
    )
