import os
from dataclasses import dataclass, field
from pathlib import Path

APPROVAL_WORDS = ("buy", "pay", "submit", "delete", "send", "confirm", "place order")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    vault_key: str
    api_key: str
    public_url: str = "http://127.0.0.1:8931"
    chromium_path: str | None = None
    task_idle_seconds: float = 600
    cookie_save_seconds: float = 60
    max_profiles: int = 20
    max_open_tasks: int = 50
    max_actions_per_day: int = 5000
    approval_words: tuple[str, ...] = field(default=APPROVAL_WORDS)


def load_config() -> Config:
    return Config(
        data_dir=Path(os.environ.get("OPENBROWSER_DATA_DIR", "data")),
        vault_key=_required("OPENBROWSER_VAULT_KEY", 'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'),
        api_key=_required("OPENBROWSER_API_KEY", 'python -c "import secrets; print(secrets.token_urlsafe(32))"'),
        public_url=os.environ.get("OPENBROWSER_PUBLIC_URL", "http://127.0.0.1:8931").rstrip("/"),
        chromium_path=os.environ.get("OPENBROWSER_CHROMIUM"),
        max_profiles=int(os.environ.get("OPENBROWSER_MAX_PROFILES", 20)),
        max_open_tasks=int(os.environ.get("OPENBROWSER_MAX_OPEN_TASKS", 50)),
        max_actions_per_day=int(os.environ.get("OPENBROWSER_MAX_ACTIONS_PER_DAY", 5000)),
    )


def _required(name: str, how: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Create one with: {how}")
    return value
