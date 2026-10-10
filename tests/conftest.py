import os

import pytest
from cryptography.fernet import Fernet

from openbrowser.config import Config
from login_site import Site


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        vault_key=Fernet.generate_key().decode(),
        chromium_path=os.environ.get("OPENBROWSER_CHROMIUM"),
    )


@pytest.fixture
def site():
    s = Site()
    yield s
    s.stop()
