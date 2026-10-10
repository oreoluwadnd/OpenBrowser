import json
import re
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from openbrowser.errors import OpenBrowserError

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SITE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


@dataclass(frozen=True)
class Entry:
    name: str
    site: str
    fields: list[str]


class Vault:
    """Logins the agent can type but never read. Values are encrypted at rest."""

    def __init__(self, db: sqlite3.Connection, fernet: Fernet):
        self._db = db
        self._fernet = fernet

    def put(self, name: str, site: str, fields: dict[str, str]) -> None:
        """Create or update an entry. Fields not given keep their old value."""
        if not NAME.match(name):
            raise OpenBrowserError(f"Invalid entry name {name!r}: use lowercase letters, digits, '-' and '_'.")
        site = site.strip().lower()
        if not SITE.match(site):
            raise OpenBrowserError(f"Invalid site {site!r}: give a domain like github.com.")
        for field in fields:
            if not NAME.match(field):
                raise OpenBrowserError(f"Invalid field name {field!r}.")
        values = {**self._values(name), **{k: v for k, v in fields.items() if v}}
        if not values:
            raise OpenBrowserError("An entry needs at least one field with a value.")
        self._db.execute(
            "INSERT INTO vault (name, site, fields) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET site = excluded.site, fields = excluded.fields",
            (name, site, self._fernet.encrypt(json.dumps(values).encode())),
        )
        self._db.commit()

    def delete(self, name: str) -> None:
        if self._db.execute("DELETE FROM vault WHERE name = ?", (name,)).rowcount == 0:
            raise OpenBrowserError(f"No vault entry {name!r}.")
        self._db.commit()

    def entries(self) -> list[Entry]:
        rows = self._db.execute("SELECT name, site FROM vault ORDER BY name").fetchall()
        return [Entry(r["name"], r["site"], sorted(self._values(r["name"]))) for r in rows]

    def value_for(self, secret: str, page_url: str) -> str:
        """The value of `entry.field`, only if the page is on the entry's site."""
        name, _, field = secret.partition(".")
        row = self._db.execute("SELECT site FROM vault WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise OpenBrowserError(f"No vault entry {name!r}. Ask the user to add it with the vault tool.")
        values = self._values(name)
        if field not in values:
            raise OpenBrowserError(f"Vault entry {name!r} has no field {field!r}. Fields: {', '.join(sorted(values))}.")
        host = urlsplit(page_url).hostname or ""
        site = row["site"]
        if host != site and not host.endswith("." + site):
            raise OpenBrowserError(f"Vault entry {name!r} is for {site}, but this page is on {host or page_url}.")
        return values[field]

    def secret_labels(self) -> dict[str, str]:
        """Every stored value mapped to its placeholder, longest value first."""
        labels = {}
        for row in self._db.execute("SELECT name FROM vault").fetchall():
            for field, value in self._values(row["name"]).items():
                labels[value] = f"[secret:{row['name']}.{field}]"
        return dict(sorted(labels.items(), key=lambda kv: -len(kv[0])))

    def scrub(self, text: str) -> str:
        for value, label in self.secret_labels().items():
            text = text.replace(value, label)
        return text

    def _values(self, name: str) -> dict[str, str]:
        row = self._db.execute("SELECT fields FROM vault WHERE name = ?", (name,)).fetchone()
        return json.loads(self._fernet.decrypt(row["fields"])) if row else {}
