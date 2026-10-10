import asyncio
import dataclasses
import os
import socket
from dataclasses import dataclass

import pytest
import uvicorn
from cryptography.fernet import Fernet
from login_site import Site

from openbrowser.app import create_app
from openbrowser.config import Config
from openbrowser.runtime import Runtime

API_KEY = "test-api-key"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        vault_key=Fernet.generate_key().decode(),
        api_key=API_KEY,
        chromium_path=os.environ.get("OPENBROWSER_CHROMIUM"),
    )


@pytest.fixture
def site():
    s = Site()
    yield s
    s.stop()


@dataclass
class Server:
    url: str
    runtime: Runtime
    headers: dict


@pytest.fixture
async def server(config):
    """The whole app on a free port, as `openbrowser serve` runs it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = dataclasses.replace(config, public_url=f"http://127.0.0.1:{port}")
    app = create_app(config)
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(uv.serve())
    while not uv.started:
        await asyncio.sleep(0.05)
    yield Server(config.public_url, app.state.runtime, {"Authorization": f"Bearer {API_KEY}"})
    uv.should_exit = True
    await serving
