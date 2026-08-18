"""Command-line entry point.

Examples:
    # First run: backfill everything since Feb 1st, 2026
    tridata sync --since 2026-02-01

    # Daily cron run: just catch up on whatever's missing
    tridata sync

    # Sync from a different source (skeleton only — requires partner credentials)
    tridata sync --source suunto
    tridata sync --source coros

    # Write out a Claude-ready Markdown file with your full history
    tridata export --format markdown --out export.md
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .clients import CorosClient, FitnessClient, GarminAuthError, GarminClient, SuuntoClient
from .exporters import JSONExporter, MarkdownExporter
from .storage import DataStore
from .sync import SyncService

EXPORTERS = {
    "json": JSONExporter,
    "markdown": MarkdownExporter,
}

SOURCES = ("garmin", "suunto", "coros")


def _build_client(source: str) -> FitnessClient:
    """Instantiate the right client for *source*; exits early on missing env vars."""
    if source == "garmin":
        email = os.environ.get("GARMIN_EMAIL")
        password = os.environ.get("GARMIN_PASSWORD")
        if not email or not password:
            print(
                "Missing GARMIN_EMAIL / GARMIN_PASSWORD. Copy .env.example to .env and fill it in.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return GarminClient(email, password)
    if source == "suunto":
        return SuuntoClient()
    if source == "coros":
        return CorosClient()
    print(f"Unknown source '{source}'. Choose from: {', '.join(SOURCES)}", file=sys.stderr)
    raise SystemExit(1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tridata")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_cmd = sub.add_parser("sync", help="Pull new data from a fitness platform into the local store")
    sync_cmd.add_argument(
        "--source", choices=SOURCES, default="garmin",
        help="Which fitness platform to sync from (default: garmin).",
    )
    sync_cmd.add_argument(
        "--since", type=date.fromisoformat, default=None,
        help="Backfill from this date (YYYY-MM-DD). Defaults to the first day of the current month.",
    )

    export_cmd = sub.add_parser("export", help="Write everything in the local store to a file")
    export_cmd.add_argument("--format", choices=EXPORTERS.keys(), default="markdown")
    export_cmd.add_argument("--out", type=Path, default=Path("garmin_export.md"))

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    parser = _build_parser()
    args = parser.parse_args(argv)

    store = DataStore()

    if args.command == "sync":
        since = args.since or date.today().replace(day=1)
        client = _build_client(args.source)
        try:
            SyncService(client, store).sync(since=since)
        except NotImplementedError as exc:
            print(
                f"'{args.source}' integration is not yet available: {exc}",
                file=sys.stderr,
            )
            return 1
        except GarminAuthError as exc:
            print(f"Login failed: {exc}", file=sys.stderr)
            return 1
        print("Sync complete.")
        return 0

    if args.command == "export":
        exporter = EXPORTERS[args.format]()
        data = store.export_all()
        out_path = exporter.export(data, args.out)
        print(f"Wrote {out_path}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
