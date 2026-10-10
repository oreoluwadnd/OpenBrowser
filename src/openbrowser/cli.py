import argparse
import asyncio

from openbrowser.config import load_config
from openbrowser.server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="openbrowser")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the runtime and its MCP server (streamable HTTP at /mcp).")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8931)
    args = parser.parse_args()

    server = create_server(load_config())
    asyncio.run(server.run_streamable_http_async(host=args.host, port=args.port))
