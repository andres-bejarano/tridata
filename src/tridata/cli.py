"""Command-line entry point.

Examples:
    # First run: backfill everything since Feb 1st, 2026
    tridata sync --since 2026-02-01

    # Daily cron run: just catch up on whatever's missing
    tridata sync

    # Backfill per-lap splits for the 20 most recent unprocessed activities
    tridata sync --laps
    tridata sync --laps --laps-backfill-limit 50

    # Inspect the laps of a specific activity
    tridata show-laps 24026027227

    # Write out a Claude-ready Markdown file with your full history
    tridata export --format markdown --out export.md

    # Export only daily metrics (no per-activity lines)
    tridata export-metrics --out metrics.md
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .exporters import JSONExporter, MarkdownExporter, MetricsMarkdownExporter
from .garmin_client import GarminAuthError, GarminClient
from .storage import DataStore
from .sync import SyncService

_LAP_COLUMNS = ("dist", "pace", "HR", "cadence", "stride", "elev")

EXPORTERS = {
    "json": JSONExporter,
    "markdown": MarkdownExporter,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tridata")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_cmd = sub.add_parser("sync", help="Pull new data from Garmin Connect into the local store")
    sync_cmd.add_argument(
        "--since", type=date.fromisoformat, default=None,
        help="Backfill from this date (YYYY-MM-DD). Defaults to start of current month.",
    )
    sync_cmd.add_argument(
        "--laps", action="store_true", default=False,
        help="Sync per-lap splits instead of daily data (does not run the regular sync).",
    )
    sync_cmd.add_argument(
        "--laps-backfill-limit", type=int, default=20, metavar="N",
        help="Max number of activities to fetch laps for in one run (default: 20).",
    )

    export_cmd = sub.add_parser("export", help="Write everything in the local store to a file")
    export_cmd.add_argument("--format", choices=EXPORTERS.keys(), default="markdown")
    export_cmd.add_argument("--out", type=Path, default=Path("garmin_export.md"))

    show_laps_cmd = sub.add_parser("show-laps", help="Print per-lap splits for one activity")
    show_laps_cmd.add_argument("activity_id", help="Garmin activity ID")

    metrics_cmd = sub.add_parser(
        "export-metrics",
        help="Export daily metrics (sleep, HRV, training status, etc.) without per-activity lines",
    )
    metrics_cmd.add_argument("--out", type=Path, default=Path("metrics.md"))

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    parser = _build_parser()
    args = parser.parse_args(argv)

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        print(
            "Missing GARMIN_EMAIL / GARMIN_PASSWORD. Copy .env.example to .env and fill it in.",
            file=sys.stderr,
        )
        return 1

    store = DataStore()

    if args.command == "sync":
        client = GarminClient(email, password)
        svc = SyncService(client, store)
        try:
            if args.laps:
                n = svc.sync_laps(limit=args.laps_backfill_limit)
                print(f"Lap sync complete: {n} activities processed.")
            else:
                since = args.since or date.today().replace(day=1)
                svc.sync(since=since)
                print("Sync complete.")
        except GarminAuthError as exc:
            print(f"Login failed: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "export":
        exporter = EXPORTERS[args.format]()
        data = store.export_all()
        out_path = exporter.export(data, args.out)
        print(f"Wrote {out_path}")
        return 0

    if args.command == "show-laps":
        return _cmd_show_laps(store, args.activity_id)

    if args.command == "export-metrics":
        out_path = MetricsMarkdownExporter().export(store.export_all(), args.out)
        print(f"Wrote {out_path}")
        return 0

    parser.print_help()
    return 1


def _fmt_pace(seconds_per_km: float | None) -> str:
    if seconds_per_km is None:
        return "—"
    total = int(round(seconds_per_km))
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


def _cmd_show_laps(store: DataStore, activity_id: str) -> int:
    laps = store.get_laps(activity_id)
    if not laps:
        print(f"No laps found for activity {activity_id}. Run: tridata sync --laps")
        return 0

    print(f"Activity {activity_id} — {len(laps)} laps\n")
    for lap in laps:
        idx = lap.get("lap_index", "?")
        dist_km = (lap["distance_meters"] / 1000) if lap.get("distance_meters") else None
        pace = _fmt_pace(lap.get("avg_pace_seconds_per_km"))
        hr = lap.get("avg_hr")
        max_hr = lap.get("max_hr")
        cadence = lap.get("avg_cadence")
        stride = lap.get("avg_stride_length_cm")
        gain = lap.get("elevation_gain_m")
        loss = lap.get("elevation_loss_m")
        itype = lap.get("intensity_type", "")

        parts = [f"Lap {idx:>2}"]
        parts.append(f"{dist_km:.2f} km" if dist_km is not None else "  —  ")
        parts.append(f"{pace}/km")
        if hr is not None:
            hr_str = f"HR {hr:.0f}"
            if max_hr is not None:
                hr_str += f" (max {max_hr:.0f})"
            parts.append(hr_str)
        if cadence is not None:
            parts.append(f"{cadence:.0f} spm")
        if stride is not None:
            parts.append(f"stride {stride:.0f} cm")
        elev_parts = []
        if gain is not None:
            elev_parts.append(f"+{gain:.0f}m")
        if loss is not None:
            elev_parts.append(f"-{loss:.0f}m")
        if elev_parts:
            parts.append(" / ".join(elev_parts))
        if itype:
            parts.append(f"[{itype}]")

        print("   ".join(parts))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
