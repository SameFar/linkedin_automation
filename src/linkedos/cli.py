"""Command-line entry point: `linkedos`.

Thin by design. The CLI parses arguments, calls one service function, and formats the
result. No business logic, no database access.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from linkedos import __version__
from linkedos.core.errors import LinkedOSError
from linkedos.services.content import create_drafts
from linkedos.services.costs import month_to_date
from linkedos.services.status import get_app_status

EXIT_OK = 0
EXIT_ERROR = 1

RULE = "─" * 72


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedos",
        description="Local-first AI copilot for LinkedIn personal branding.",
    )
    parser.add_argument("--version", action="version", version=f"linkedos {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print app version and database health.")

    draft = subparsers.add_parser("draft", help="Generate draft post variants on a topic.")
    draft.add_argument("topic", help="What to write about, in your own words.")
    draft.add_argument(
        "-n",
        "--variants",
        type=int,
        default=3,
        metavar="N",
        help="How many variants to generate (default: 3). Each is a billed model call.",
    )

    subparsers.add_parser("costs", help="Print month-to-date model spend.")

    return parser


def _cmd_status() -> int:
    status = get_app_status()
    print(f"linkedos {status.version}")
    print(f"db path:  {status.db_path} (exists: {status.db_exists})")

    if not status.db_exists:
        print("no database yet; run `alembic upgrade head`")
        return EXIT_OK

    print(f"schema:   {status.db_revision or 'none'} (head: {status.head_revision or 'unknown'})")
    if status.needs_migration:
        print("schema is out of date; run `alembic upgrade head`")
        return EXIT_OK

    last = status.last_heartbeat.isoformat() if status.last_heartbeat else "never"
    print(f"heartbeat: {status.heartbeat_count} row(s), last {last}")
    print("OK")
    return EXIT_OK


def _cmd_draft(topic: str, variants: int) -> int:
    batch = create_drafts(topic, variants)

    if (duplicate := batch.duplicate_warning) is not None:
        print(
            f"note: you have written about this before "
            f"(post {duplicate.post.id}, similarity {duplicate.score:.2f})\n"
        )

    for index, post in enumerate(batch.posts, start=1):
        print(RULE)
        print(f"variant {index}/{len(batch.posts)}  ·  post id {post.id}")
        print(RULE)
        print(post.content)
        print()

    print(RULE)
    print(f"group {batch.variant_group_id}  ·  prompt {batch.prompt_version}")
    print(f"{len(batch.posts)} draft(s)  ·  this run cost ${batch.cost_usd:.4f}")
    return EXIT_OK


def _cmd_costs() -> int:
    report = month_to_date()
    print(f"month-to-date since {report.since.date().isoformat()} (UTC)\n")

    if not report.rows:
        print("no model calls recorded yet.")
        return EXIT_OK

    print(f"{'model':<22} {'purpose':<14} {'calls':>6} {'in':>9} {'out':>8} {'usd':>10}")
    print(RULE)
    for row in report.rows:
        print(
            f"{row.model:<22} {row.purpose:<14} {row.calls:>6} "
            f"{row.input_tokens:>9} {row.output_tokens:>8} {row.cost_usd:>10.4f}"
        )

    print(RULE)
    # 44 + 30 lines the total up under the `usd` column above.
    print(f"{'total':<44}{report.total_usd:>30.4f}")
    # Four decimals, not two: at these prices `$29.9953` rounds to `$30.00` and reads
    # as though nothing has been spent.
    print(f"budget ${report.budget_usd:.2f}  ·  remaining ${report.budget_remaining_usd:.4f}")
    if report.over_budget:
        print("\nWARNING: month-to-date spend is over the configured budget.")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Parse `argv` and dispatch. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            return _cmd_status()
        if args.command == "draft":
            return _cmd_draft(args.topic, args.variants)
        if args.command == "costs":
            return _cmd_costs()
    except LinkedOSError as exc:
        parser.exit(EXIT_ERROR, f"linkedos: {exc}\n")

    parser.error(f"unknown command: {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
