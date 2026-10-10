import argparse

import uvicorn

from openbrowser.app import create_app
from openbrowser.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="openbrowser")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run OpenBrowser: MCP at /mcp, REST at /api, live view at /live.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8931)
    args = parser.parse_args()

    uvicorn.run(create_app(load_config()), host=args.host, port=args.port)
