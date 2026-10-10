import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    vault_key: str
    chromium_path: str | None = None
    task_idle_seconds: float = 600
    cookie_save_seconds: float = 60


def load_config() -> Config:
    key = os.environ.get("OPENBROWSER_VAULT_KEY")
    if not key:
        raise RuntimeError(
            "OPENBROWSER_VAULT_KEY is not set. Create one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Config(
        data_dir=Path(os.environ.get("OPENBROWSER_DATA_DIR", "data")),
        vault_key=key,
        chromium_path=os.environ.get("OPENBROWSER_CHROMIUM"),
    )
