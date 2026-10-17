"""MCP server: lets Claude query the local store on demand.

Instead of attaching the full Markdown exports (hundreds of KB) to every
conversation, Claude calls these tools and gets back only the slice it
needs — a date range, one activity, a weekly summary. That keeps each
conversation small, which on a Claude subscription means the usage limits
stretch much further.

Every tool is read-only and renders through the existing exporters, so the
output looks exactly like the Markdown files people are used to.

Run it with `tridata-mcp` (stdio transport) and register that command in
your MCP client, e.g. for Claude Code:

    claude mcp add tridata -- /path/to/env/bin/tridata-mcp
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .exporters import (
    CyclingMarkdownExporter, GymMarkdownExporter, MarkdownExporter,
    MetricsMarkdownExporter, SwimmingMarkdownExporter,
)
from .metrics import compute_pmc, daily_tss, trimp_for_activity
from .storage import DataStore
from .sync import ALL_SPORT_TYPES, CYCLING_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES

SPORTS: dict[str, tuple[str, ...]] = {
    "running": RUNNING_TYPES,
    "cycling": CYCLING_TYPES,
    "swimming": SWIMMING_TYPES,
    "gym": GYM_TYPES,
}

# Date key of each export_all() category, used to slice it by range.
_DATE_KEYS = {
    "activities": "activity_date",
    "daily_stats": "stat_date",
    "sleep": "sleep_date",
    "hrv": "hrv_date",
    "vo2max": "vo2max_date",
    "training_readiness": "readiness_date",
    "training_status": "status_date",
    "body_battery": "bb_date",
    "spo2": "spo2_date",
    "respiration": "respiration_date",
    "floors": "floors_date",
    "intensity_minutes": "intensity_date",
    "hydration": "hydration_date",
    "race_predictions": "prediction_date",
}

INSTRUCTIONS = """\
Local Garmin Connect history (activities with laps and weather, daily
metrics, sleep, HRV, training load) of one triathlete.

- Ask for the smallest slice that answers the question; prefer
  get_weekly_summary / get_training_load for trends before pulling
  individual activities.
- "TSS" in this data is Banister TRIMP from heart rate (measured HRmax
  and HRrest), not power-based TSS. CTL/ATL/TSB
  follow the standard 42/7-day EMAs and underestimate during the first
  weeks of history.
- Day totals marked "⚠ incompleto" were cached by Garmin before the day
  was finalized; don't treat them as real totals.
"""


def _parse_range(start_date: str | None, end_date: str | None, default_days: int) -> tuple[date, date]:
    end = date.fromisoformat(end_date) if end_date else date.today()
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=default_days - 1)
    if start > end:
        raise ValueError(f"start_date {start} is after end_date {end}")
    return start, end


def _in_range(row: dict, key: str, start: date, end: date) -> bool:
    value = row.get(key)
    return value is not None and start.isoformat() <= value <= end.isoformat()


def _sport_of(activity: dict) -> str | None:
    for sport, types in SPORTS.items():
        if activity.get("activity_type") in types:
            return sport
    return None


def _fmt_activity(activity: dict) -> str:
    """Summary line in the same format as that sport's export file."""
    sport = _sport_of(activity)
    if sport == "cycling":
        line = CyclingMarkdownExporter._fmt_cycling_activity_line(activity)
    elif sport == "swimming":
        line = SwimmingMarkdownExporter._fmt_swim_activity_line(activity)
    elif sport == "gym":
        line = GymMarkdownExporter._fmt_gym_activity_line(activity)
    else:
        line = MarkdownExporter._fmt_activity_line(activity)
    if sport in ("running", "cycling"):
        line += f" | {MarkdownExporter._fmt_weather(activity.get('weather'))}"
    return line


def _fmt_lap(activity: dict, lap: dict) -> str:
    sport = _sport_of(activity)
    if sport == "cycling":
        return CyclingMarkdownExporter._fmt_cycling_lap_line(lap)
    if sport == "swimming":
        return SwimmingMarkdownExporter._fmt_swim_lap_line(lap)
    return MarkdownExporter._fmt_lap_line(lap)


# -- Tool implementations ------------------------------------------------
# Plain functions over a DataStore so they can be tested without MCP.


def list_activities(
    store: DataStore,
    start_date: str | None = None,
    end_date: str | None = None,
    sport: str | None = None,
) -> str:
    if sport is not None and sport not in SPORTS:
        raise ValueError(f"Unknown sport {sport!r}; use one of {', '.join(SPORTS)}")
    start, end = _parse_range(start_date, end_date, default_days=14)
    types = SPORTS[sport] if sport else ALL_SPORT_TYPES
    activities = [
        a for a in store.get_activities_with_laps(types)
        if _in_range(a, "activity_date", start, end)
    ]
    activities.reverse()  # oldest first, like the export files

    lines = [f"# Activities {start} → {end} ({len(activities)})", ""]
    for a in activities:
        lines.append(f"{_fmt_activity(a)} · TRIMP {trimp_for_activity(a, a['laps']):.0f} · id {a['activity_id']}")
    if not activities:
        lines.append("_No activities in this range._")
    return "\n".join(lines)


def get_activity(store: DataStore, activity_id: str) -> str:
    activity = store.get_activity(activity_id)
    if activity is None:
        raise ValueError(f"No activity with id {activity_id}")
    activity["laps"] = store.get_laps(activity_id)
    activity["weather"] = store.get_weather(activity_id)

    lines = [
        f"# Activity {activity_id}",
        "",
        _fmt_activity(activity),
        f"- TRIMP {trimp_for_activity(activity, activity['laps']):.1f}",
    ]
    te_aer = activity.get("training_effect_aerobic")
    te_ana = activity.get("training_effect_anaerobic")
    if te_aer is not None or te_ana is not None:
        lines.append(f"- Training effect: aerobic {te_aer}, anaerobic {te_ana}")
    if activity.get("avg_power"):
        lines.append(f"- Avg power {activity['avg_power']:.0f} W")
    if activity["laps"]:
        lines += ["", f"## Laps ({len(activity['laps'])})"]
        lines += [f"- {_fmt_lap(activity, lap)}" for lap in activity["laps"]]
    else:
        lines += ["", "_No laps synced for this activity._"]
    return "\n".join(lines)


def get_daily_metrics(
    store: DataStore,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    start, end = _parse_range(start_date, end_date, default_days=7)
    data = store.export_all()
    sliced: dict[str, Any] = {
        category: [row for row in data.get(category, []) if _in_range(row, key, start, end)]
        for category, key in _DATE_KEYS.items()
    }
    # Personal records aren't a daily metric; get_records covers them.
    sliced["personal_records"] = []
    body = MetricsMarkdownExporter().render(sliced)
    return body.replace("# Garmin Connect metrics export", f"# Daily metrics {start} → {end}", 1)


def get_training_load(store: DataStore, days: int = 28) -> str:
    if days < 1:
        raise ValueError("days must be >= 1")
    # The EMAs need the whole history to warm up; only the output is trimmed.
    series = compute_pmc(daily_tss(store.get_activities_with_laps(ALL_SPORT_TYPES)))
    if not series:
        return "_No activities stored yet._"
    # compute_pmc stops at the last activity; extend with rest days up to today.
    last = series[-1]
    ctl, atl, current = last["ctl"], last["atl"], last["date"] + timedelta(days=1)
    while current <= date.today():
        tsb = ctl - atl
        ctl -= ctl / 42.0
        atl -= atl / 7.0
        series.append({"date": current, "tss": 0.0, "ctl": ctl, "atl": atl, "tsb": tsb})
        current += timedelta(days=1)

    tail = series[-days:]
    today = tail[-1]
    lines = [
        f"# Training load (TRIMP-based PMC), last {len(tail)} days",
        "",
        f"- Today {today['date']}: CTL {today['ctl']:.1f}, ATL {today['atl']:.1f}, TSB {today['tsb']:+.1f}",
        f"- CTL change over the window: {today['ctl'] - tail[0]['ctl']:+.1f}",
        "",
        "| Date | TRIMP | CTL | ATL | TSB |",
        "|---|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {r['date']} | {r['tss']:.0f} | {r['ctl']:.1f} | {r['atl']:.1f} | {r['tsb']:+.1f} |"
        for r in tail
    ]
    return "\n".join(lines)


def get_weekly_summary(store: DataStore, weeks: int = 8) -> str:
    if weeks < 1:
        raise ValueError("weeks must be >= 1")
    this_monday = date.today() - timedelta(days=date.today().weekday())
    first_monday = this_monday - timedelta(weeks=weeks - 1)

    # week -> sport -> totals
    totals: dict[date, dict[str, dict[str, float]]] = {
        first_monday + timedelta(weeks=i): {} for i in range(weeks)
    }
    for a in store.get_activities_with_laps(ALL_SPORT_TYPES):
        day = date.fromisoformat(a["activity_date"])
        monday = day - timedelta(days=day.weekday())
        if monday not in totals:
            continue
        t = totals[monday].setdefault(_sport_of(a) or "other", {"n": 0, "hours": 0.0, "km": 0.0, "trimp": 0.0})
        t["n"] += 1
        t["hours"] += (a.get("duration_seconds") or 0) / 3600
        t["km"] += (a.get("distance_meters") or 0) / 1000
        t["trimp"] += trimp_for_activity(a, a["laps"])

    lines = [
        f"# Weekly summary, last {weeks} weeks (week starts Monday; current week is partial)",
        "",
        "| Week | Sport | Sessions | Hours | km | TRIMP |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for monday, by_sport in totals.items():
        if not by_sport:
            lines.append(f"| {monday} | — | 0 | 0.0 | 0.0 | 0 |")
            continue
        for sport, t in sorted(by_sport.items()):
            km = f"{t['km']:.1f}" if sport != "gym" else "—"
            lines.append(
                f"| {monday} | {sport} | {t['n']:.0f} | {t['hours']:.1f} | {km} | {t['trimp']:.0f} |"
            )
        week_hours = sum(t["hours"] for t in by_sport.values())
        week_trimp = sum(t["trimp"] for t in by_sport.values())
        lines.append(f"| {monday} | **total** | | **{week_hours:.1f}** | | **{week_trimp:.0f}** |")
    return "\n".join(lines)


def get_records(store: DataStore) -> str:
    data = store.export_all()
    exporter = MarkdownExporter()
    latest = {
        "personal_records": data.get("personal_records", []),
        "race_predictions": data.get("race_predictions", [])[-1:],
        "vo2max": data.get("vo2max", [])[-1:],
    }
    lines = ["# Records and current estimates", ""]
    lines += exporter._render_personal_records(latest)
    lines += exporter._render_race_predictions(latest)
    lines += exporter._render_vo2max(latest)
    if len(lines) == 2:
        lines.append("_No records stored yet._")
    return "\n".join(lines)


# -- MCP wiring ----------------------------------------------------------


def build_server(store: DataStore) -> MCPServer:
    server = MCPServer(name="tridata", instructions=INSTRUCTIONS)
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    @server.tool(name="list_activities", annotations=read_only, structured_output=False)
    def list_activities_tool(
        start_date: str | None = None,
        end_date: str | None = None,
        sport: str | None = None,
    ) -> str:
        """List activities in a date range, one line each with its id and TRIMP.

        Dates are YYYY-MM-DD; defaults to the last 14 days. `sport` is one of
        running, cycling, swimming, gym (omit for all). Use get_activity with
        an id for the per-lap breakdown.
        """
        return list_activities(store, start_date, end_date, sport)

    @server.tool(name="get_activity", annotations=read_only, structured_output=False)
    def get_activity_tool(activity_id: str) -> str:
        """Full detail of one activity: summary, weather, training effect, TRIMP and every lap."""
        return get_activity(store, activity_id)

    @server.tool(name="get_daily_metrics", annotations=read_only, structured_output=False)
    def get_daily_metrics_tool(start_date: str | None = None, end_date: str | None = None) -> str:
        """Daily wellness metrics for a date range: steps, calories (exercise vs basal/NEAT),
        resting HR, stress, sleep, HRV, readiness, training status, body battery, SpO2,
        respiration, hydration. Dates YYYY-MM-DD; defaults to the last 7 days."""
        return get_daily_metrics(store, start_date, end_date)

    @server.tool(name="get_training_load", annotations=read_only, structured_output=False)
    def get_training_load_tool(days: int = 28) -> str:
        """Fitness/fatigue/form (CTL/ATL/TSB) for the last `days` days, computed over the full history."""
        return get_training_load(store, days)

    @server.tool(name="get_weekly_summary", annotations=read_only, structured_output=False)
    def get_weekly_summary_tool(weeks: int = 8) -> str:
        """Per-week, per-sport sessions, hours, km and TRIMP for the last `weeks` weeks.
        The cheapest way to see volume trends."""
        return get_weekly_summary(store, weeks)

    @server.tool(name="get_records", annotations=read_only, structured_output=False)
    def get_records_tool() -> str:
        """Personal records, the latest race-time predictions and the latest VO2max."""
        return get_records(store)

    return server


def main() -> None:
    # stdout is the MCP channel on stdio; logs must go to stderr.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()  # TRIDATA_HR_MAX / TRIDATA_HR_REST for TRIMP
    build_server(DataStore()).run("stdio")


if __name__ == "__main__":
    main()
