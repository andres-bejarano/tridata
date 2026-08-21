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
    tridata export --format markdown --out exports/garmin_export.md

    # Export only daily metrics (no per-activity lines)
    tridata export-metrics --out exports/metrics.md
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .exporters import (
    CyclingMarkdownExporter, GymMarkdownExporter, JSONExporter, MarkdownExporter,
    MetricsMarkdownExporter, PMCMarkdownExporter, RunningMarkdownExporter,
    SwimmingMarkdownExporter,
)
from .garmin_client import GarminAuthError, GarminClient
from .metrics import compute_pmc, daily_tss
from .storage import DataStore
from .sync import ALL_SPORT_TYPES, CYCLING_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES, SyncService


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
    export_cmd.add_argument("--out", type=Path, default=Path("exports/garmin_export.md"))

    show_laps_cmd = sub.add_parser("show-laps", help="Print per-lap splits for one activity")
    show_laps_cmd.add_argument("activity_id", help="Garmin activity ID")

    metrics_cmd = sub.add_parser(
        "export-metrics",
        help="Export daily metrics (sleep, HRV, training status, etc.) without per-activity lines",
    )
    metrics_cmd.add_argument("--out", type=Path, default=Path("exports/metrics.md"))

    running_cmd = sub.add_parser(
        "export-running",
        help="Export running activities with per-lap breakdown",
    )
    running_cmd.add_argument("--out", type=Path, default=Path("exports/running.md"))

    cycling_cmd = sub.add_parser(
        "export-cycling",
        help="Export cycling activities with per-lap breakdown",
    )
    cycling_cmd.add_argument("--out", type=Path, default=Path("exports/cycling.md"))

    swimming_cmd = sub.add_parser(
        "export-swimming",
        help="Export pool and open-water swimming activities",
    )
    swimming_cmd.add_argument("--out", type=Path, default=Path("exports/swimming.md"))

    gym_cmd = sub.add_parser(
        "export-gym",
        help="Export gym/strength activities",
    )
    gym_cmd.add_argument("--out", type=Path, default=Path("exports/gym.md"))

    pmc_cmd = sub.add_parser(
        "export-pmc",
        help="Export Performance Management Chart (CTL/ATL/TSB) as Markdown",
    )
    pmc_cmd.add_argument("--out", type=Path, default=Path("exports/pmc.md"))
    pmc_cmd.add_argument(
        "--chart", action="store_true", default=False,
        help="Also regenerate docs/assets/pmc_last14d.png",
    )

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

    if args.command == "export-running":
        data = store.get_activities_with_laps(RUNNING_TYPES)
        out_path = RunningMarkdownExporter().export(data, args.out)
        print(f"Wrote {out_path}")
        return 0

    if args.command == "export-cycling":
        data = store.get_activities_with_laps(CYCLING_TYPES)
        out_path = CyclingMarkdownExporter().export(data, args.out)
        print(f"Wrote {out_path}")
        return 0

    if args.command == "export-swimming":
        data = store.get_activities_with_laps(SWIMMING_TYPES)
        out_path = SwimmingMarkdownExporter().export(data, args.out)
        print(f"Wrote {out_path}")
        return 0

    if args.command == "export-gym":
        data = store.get_activities_with_laps(GYM_TYPES)
        out_path = GymMarkdownExporter().export(data, args.out)
        print(f"Wrote {out_path}")
        return 0

    if args.command == "export-pmc":
        activities = store.get_activities_with_laps(ALL_SPORT_TYPES)
        tss_series = daily_tss(activities)
        pmc_series = compute_pmc(tss_series)
        out_path = PMCMarkdownExporter().export(pmc_series, args.out)
        print(f"Wrote {out_path}")
        if args.chart:
            _generate_pmc_chart(pmc_series)
        return 0

    parser.print_help()
    return 1


def _cmd_show_laps(store: DataStore, activity_id: str) -> int:
    laps = store.get_laps(activity_id)
    if not laps:
        print(f"No laps found for activity {activity_id}. Run: tridata sync --laps")
        return 0

    print(f"Activity {activity_id} — {len(laps)} laps\n")
    for lap in laps:
        print(MarkdownExporter._fmt_lap_line(lap))

    return 0


def _generate_pmc_chart(pmc_series: list) -> None:
    """Regenerate docs/assets/pmc_last14d.png from the last 14 days of the series."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("matplotlib not available — skipping chart generation", file=sys.stderr)
        return

    tail = pmc_series[-14:]
    dates = [row["date"] for row in tail]
    tss_vals = [row["tss"] for row in tail]
    ctl_vals = [row["ctl"] for row in tail]
    atl_vals = [row["atl"] for row in tail]
    tsb_vals = [row["tsb"] for row in tail]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    fig.suptitle("Performance Management Chart — last 14 days", fontsize=13)

    ax1.bar(dates, tss_vals, color="#aac4e0", label="TSS (TRIMP)")
    ax1.plot(dates, ctl_vals, "b-o", markersize=4, label="CTL (fitness)")
    ax1.plot(dates, atl_vals, "r-o", markersize=4, label="ATL (fatigue)")
    ax1.set_ylabel("Load")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(axis="y", alpha=0.3)

    colors = ["green" if v >= 0 else "red" for v in tsb_vals]
    ax2.bar(dates, tsb_vals, color=colors, alpha=0.7, label="TSB (form)")
    ax2.axhline(0, color="black", linewidth=0.8)
    ax2.set_ylabel("Form (TSB)")
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(axis="y", alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    plt.xticks(rotation=30, ha="right")

    out = Path(__file__).parent.parent.parent / "docs" / "assets" / "pmc_last14d.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out, dpi=150)
    plt.close()
    print(f"Wrote {out}")


if __name__ == "__main__":
    raise SystemExit(main())
