import json
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass

from openbrowser.errors import OpenBrowserError


@dataclass(frozen=True)
class Approval:
    id: str
    task_id: str
    action: str
    args: dict
    description: str
    state: str  # pending | approved | rejected


class ApprovalStore:
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._db.execute("UPDATE approvals SET state = 'rejected' WHERE state = 'pending'")
        self._db.commit()

    def create(self, task_id: str, action: str, args: dict, description: str) -> Approval:
        approval_id = secrets.token_hex(6)
        self._db.execute(
            "INSERT INTO approvals (id, task_id, action, args, description, state, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (approval_id, task_id, action, json.dumps(args), description, time.time()),
        )
        self._db.commit()
        return self.get(approval_id)

    def get(self, approval_id: str) -> Approval:
        row = self._db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise OpenBrowserError(f"No approval {approval_id!r}.")
        return Approval(row["id"], row["task_id"], row["action"], json.loads(row["args"]), row["description"],
                        row["state"])

    def pending_for(self, task_id: str) -> Approval | None:
        row = self._db.execute(
            "SELECT id FROM approvals WHERE task_id = ? AND state = 'pending'", (task_id,)
        ).fetchone()
        return self.get(row["id"]) if row else None

    def resolve(self, approval_id: str, approved: bool) -> Approval:
        approval = self.get(approval_id)
        if approval.state != "pending":
            raise OpenBrowserError(f"Approval {approval_id} is already {approval.state}.")
        self._db.execute(
            "UPDATE approvals SET state = ? WHERE id = ?", ("approved" if approved else "rejected", approval_id)
        )
        self._db.commit()
        return self.get(approval_id)


def is_risky(element_text: str, words: tuple[str, ...]) -> bool:
    """Whole-word match, so "Pay now" counts but "PayPal" does not."""
    text = element_text.lower()
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)
