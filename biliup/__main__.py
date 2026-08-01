from __future__ import annotations

import argparse
from pathlib import Path

from biliup import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Biliup pure Python recording service")
    parser.add_argument("--version", action="version", version=f"v{__version__}")
    subparsers = parser.add_subparsers(dest="command")

    server = subparsers.add_parser("server", help="Start the API and recording service")
    server.add_argument("--host", "-H", default=None)
    server.add_argument("--port", "-P", type=int, default=None)
    server.add_argument("--home", type=Path, default=None)
    server.add_argument("--config", type=Path, default=None)
    server.add_argument("--no-auth", action="store_true")
    server.add_argument("--reload", action="store_true")

    validate = subparsers.add_parser("validate-config", help="Validate a YAML or TOML configuration")
    validate.add_argument("path", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "validate-config":
        from biliup.core import load_recording_config

        loaded = load_recording_config(args.path)
        print(loaded.model_dump_json(indent=2))
        return
    if args.command in {None, "server"}:
        import uvicorn

        from biliup.api import create_app
        from biliup.core import AppSettings

        settings = AppSettings(
            **{
                key: value
                for key, value in {
                    "host": getattr(args, "host", None),
                    "port": getattr(args, "port", None),
                    "home": getattr(args, "home", None),
                    "config_file": getattr(args, "config", None),
                    "auth_enabled": not getattr(args, "no_auth", False),
                }.items()
                if value is not None
            }
        )
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, reload=getattr(args, "reload", False))
        return
    raise SystemExit(2)


if __name__ == "__main__":
    main()
