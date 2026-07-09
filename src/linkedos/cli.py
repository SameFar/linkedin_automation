"""Command-line entry point: `linkedos`.

Thin by design. The CLI parses arguments, calls one service function, and formats the
result. No business logic, no database access.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from linkedos import __version__
from linkedos.core.errors import LinkedOSError
from linkedos.services.status import get_app_status

EXIT_OK = 0
EXIT_ERROR = 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedos",
        description="Local-first AI copilot for LinkedIn personal branding.",
    )
    parser.add_argument("--version", action="version", version=f"linkedos {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print app version and database health.")

    return parser


def _cmd_status() -> int:
    status = get_app_status()
    print(f"linkedos {status.version}")
    print(f"db path:  {status.db_path} (exists: {status.db_exists})")
    if status.db_exists:
        last = status.last_heartbeat.isoformat() if status.last_heartbeat else "never"
        print(f"heartbeat: {status.heartbeat_count} row(s), last {last}")
    print("OK")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Parse `argv` and dispatch. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            return _cmd_status()
    except LinkedOSError as exc:
        parser.exit(EXIT_ERROR, f"linkedos: {exc}\n")

    parser.error(f"unknown command: {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
