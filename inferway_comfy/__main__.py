"""Inferway ComfyUI CLI."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from .client import InferwayClient
from .credentials import ClientError, create_http_client, load_settings


def _display(value: object) -> str:
    if not isinstance(value, str):
        return "-"
    return "".join(
        char if ord(char) >= 32 and ord(char) != 127 else f"\\u{ord(char):04x}"
        for char in value
    )


def create_parser() -> argparse.ArgumentParser:
    """Create command-line argument parser for inferway_comfy CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m inferway_comfy",
        description="Inferway ComfyUI CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    history_parser = subparsers.add_parser("history", help="List interaction history")
    history_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of items to fetch (1..50)",
    )
    history_parser.add_argument(
        "--cursor",
        type=str,
        default=None,
        help="Pagination cursor",
    )
    history_parser.add_argument(
        "--state",
        type=str,
        default=None,
        help="Filter by state (e.g. succeeded, running, queued, failed, cancelled)",
    )
    history_parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Filter by ISO-8601 start timestamp",
    )

    return parser


async def _run_history(
    limit: int,
    cursor: str | None,
    state: str | None,
    since: str | None,
) -> int:
    settings = load_settings(os.environ)
    async with create_http_client(settings) as http:
        client = InferwayClient(http, settings=settings)
        result = await client.list(limit=limit, cursor=cursor, state=state, since=since)

        items = result.get("items", [])
        for item in items:
            safe_id = _display(item.get("id"))
            request = item.get("request")
            safe_model = _display(
                request.get("model") if isinstance(request, dict) else None
            )
            safe_state = _display(item.get("state"))
            safe_created = _display(item.get("created_at"))
            source = item.get("source")
            safe_key_name = _display(
                source.get("key_name") if isinstance(source, dict) else None
            )
            print(
                f"{safe_id}\t{safe_model}\t{safe_state}\t{safe_created}\t{safe_key_name}"
            )

        next_cursor = result.get("next_cursor")
        if next_cursor:
            print(f"next_cursor:\t{_display(next_cursor)}")

    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.command == "history":
        try:
            return asyncio.run(
                _run_history(
                    limit=args.limit,
                    cursor=args.cursor,
                    state=args.state,
                    since=args.since,
                )
            )
        except ClientError as error:
            print(f"Inferway error: {error.code}", file=sys.stderr)
            return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
