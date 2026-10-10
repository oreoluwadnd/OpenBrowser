import json
import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditRow:
    at: float
    agent: str
    task_id: str | None
    profile: str | None
    action: str
    input: dict
    result: str
    approval_id: str | None


class AuditLog:
    """Append-only record of every action. Callers pass inputs already scrubbed of vault values."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def record(
        self,
        agent: str,
        action: str,
        input: dict,
        result: str,
        task_id: str | None = None,
        profile: str | None = None,
        approval_id: str | None = None,
    ) -> None:
        self._db.execute(
            "INSERT INTO audit (at, agent, task_id, profile, action, input, result, approval_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), agent, task_id, profile, action, json.dumps(input), result, approval_id),
        )
        self._db.commit()

    def rows(self, profile: str | None = None, task_id: str | None = None, limit: int = 200) -> list[AuditRow]:
        query, args = "SELECT * FROM audit WHERE 1 = 1", []
        if profile is not None:
            query, args = query + " AND profile = ?", args + [profile]
        if task_id is not None:
            query, args = query + " AND task_id = ?", args + [task_id]
        rows = self._db.execute(query + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [
            AuditRow(r["at"], r["agent"], r["task_id"], r["profile"], r["action"], json.loads(r["input"]),
                     r["result"], r["approval_id"])
            for r in rows
        ]

    def actions_last_day(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM audit WHERE at > ?", (time.time() - 86400,)).fetchone()[0]
