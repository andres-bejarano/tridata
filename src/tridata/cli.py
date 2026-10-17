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

    # Verified copy of the store (plus extra files, e.g. a food diary), keep 14
    tridata backup --extra deficit/deficit.md

    # Food log web app on this PC only (needs the `app` extra and .env values)
    tridata web
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from .exporters import (
    CyclingMarkdownExporter, GymMarkdownExporter, JSONExporter, MarkdownExporter,
    MetricsMarkdownExporter, PMCMarkdownExporter, RunningMarkdownExporter,
    SwimmingMarkdownExporter,
)
from .garmin_client import GarminAuthError, GarminClient
from .metrics import compute_pmc, daily_tss
from .storage import DEFAULT_DB_PATH, DataStore
from .sync import ALL_SPORT_TYPES, CYCLING_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES, SyncService

DEFAULT_BACKUP_DIR = DEFAULT_DB_PATH.parent / "backups"

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
        help="Backfill from this date (YYYY-MM-DD). Defaults to a rolling 45-day lookback.",
    )
    sync_cmd.add_argument(
        "--revalidate-days", type=int, default=None, metavar="N",
        help=(
            "Re-fetch this many most-recent real-world days even if already present, "
            "overriding SyncService.RECENT_REVALIDATE_DAYS for this run. The daily cron "
            "run leaves this unset (short, cheap window). Used by the weekly wide-sweep "
            "job to revalidate a much larger window (e.g. 45) and catch days that went "
            "stale outside the short window before anything re-checked them."
        ),
    )
    sync_cmd.add_argument(
        "--laps", action="store_true", default=False,
        help="Sync per-lap splits instead of daily data (does not run the regular sync).",
    )
    sync_cmd.add_argument(
        "--laps-backfill-limit", type=int, default=20, metavar="N",
        help="Max number of activities to fetch laps for in one run (default: 20).",
    )
    sync_cmd.add_argument(
        "--weather", action="store_true", default=False,
        help="Sync weather snapshots instead of daily data (does not run the regular sync).",
    )
    sync_cmd.add_argument(
        "--weather-backfill-limit", type=int, default=20, metavar="N",
        help="Max number of activities to fetch weather for in one run (default: 20).",
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

    backup_cmd = sub.add_parser(
        "backup",
        help="Write a verified copy of the local store to ~/.tridata/backups/",
    )
    backup_cmd.add_argument("--dest", type=Path, default=DEFAULT_BACKUP_DIR)
    backup_cmd.add_argument(
        "--keep", type=int, default=14, metavar="N",
        help="Number of most recent backups to keep (default: 14).",
    )
    backup_cmd.add_argument(
        "--extra", type=Path, action="append", default=[], metavar="PATH",
        help=(
            "Also copy this file into the backup. Repeatable. For data that "
            "isn't in the store and can't be re-downloaded from Garmin."
        ),
    )

    web_cmd = sub.add_parser(
        "web",
        help="Run the food log web app (listens on this PC only by default)",
    )
    web_cmd.add_argument(
        "--host", default="127.0.0.1",
        help="Address to listen on (default: 127.0.0.1, this PC only; Tailscale serves it outside).",
    )
    web_cmd.add_argument("--port", type=int, default=8765, help="Port (default: 8765).")

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    parser = _build_parser()
    args = parser.parse_args(argv)

    store = DataStore()

    if args.command == "sync":
        # Only sync talks to Garmin; exports and backups work offline.
        email = os.environ.get("GARMIN_EMAIL")
        password = os.environ.get("GARMIN_PASSWORD")
        if not email or not password:
            print(
                "Missing GARMIN_EMAIL / GARMIN_PASSWORD. Copy .env.example to .env and fill it in.",
                file=sys.stderr,
            )
            return 1
        client = GarminClient(email, password)
        svc = SyncService(client, store)
        try:
            if args.laps:
                n = svc.sync_laps(limit=args.laps_backfill_limit)
                print(f"Lap sync complete: {n} activities processed.")
            elif args.weather:
                n = svc.sync_weather(limit=args.weather_backfill_limit)
                print(f"Weather sync complete: {n} activities processed.")
            else:
                # A rolling lookback rather than "start of this month": SyncService only
                # re-fetches genuinely missing days (plus a short recent-day revalidation
                # window — see SyncService.RECENT_REVALIDATE_DAYS), so a wider default here
                # costs nothing on a normal run but means a failure near a month boundary
                # can no longer strand data the moment the month rolls over.
                since = args.since or (date.today() - timedelta(days=45))
                svc.sync(since=since, revalidate_days=args.revalidate_days)
                print("Sync complete.")
        except GarminAuthError as exc:
            print(f"Login failed: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "export":
        exporter = EXPORTERS[args.format]()
        out_path = exporter.export(store.export_all(), args.out)
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

    if args.command == "backup":
        return _cmd_backup(store, args.dest, args.keep, args.extra)

    if args.command == "web":
        return _cmd_web(args.host, args.port)

    parser.print_help()
    return 1


def _cmd_backup(store: DataStore, dest_root: Path, keep: int, extras: list[Path]) -> int:
    missing = [p for p in extras if not p.is_file()]
    if missing:
        print(f"--extra file(s) not found: {', '.join(map(str, missing))}", file=sys.stderr)
        return 1

    # Timestamped folders sort chronologically, which rotation relies on.
    dest = dest_root / datetime.now().strftime("%Y-%m-%dT%H%M%S")
    dest.mkdir(parents=True)
    dest_root.chmod(0o700)
    dest.chmod(0o700)
    counts = store.backup(dest / "data.sqlite3")
    for extra in extras:
        copied = dest / extra.name
        shutil.copy2(extra, copied)
        copied.chmod(0o600)

    old = sorted(p for p in dest_root.iterdir() if p.is_dir())[:-keep] if keep > 0 else []
    for path in old:
        shutil.rmtree(path)

    print(
        f"Backup OK: {dest} ({sum(counts.values())} rows in {len(counts)} tables"
        + (f", +{len(extras)} extra file(s)" if extras else "")
        + f"); removed {len(old)} old backup(s)."
    )
    return 0


def _cmd_web(host: str, port: int, db_path: Path = DEFAULT_DB_PATH) -> int:
    try:
        import uvicorn

        from .ia import InterpreteClaude
        from .nutricion import ConfigGasto, RegistroComidas
        from .web import crear_app
    except ImportError as exc:
        print(f"Missing the `app` extra ({exc}). Install it: pip install -e '.[app]'", file=sys.stderr)
        return 1
    try:
        config = ConfigGasto.desde_entorno()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    app = crear_app(RegistroComidas(db_path), InterpreteClaude(), config)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"Warning: listening on {host}, reachable from outside this PC.", file=sys.stderr)
    print(f"TriData web on http://{host}:{port} (Ctrl+C to stop)")
    uvicorn.run(app, host=host, port=port)
    return 0


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
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
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
