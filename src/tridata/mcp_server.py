"""MCP server: lets Claude query the local store on demand.

Instead of attaching the full Markdown exports (hundreds of KB) to every
conversation, Claude calls these tools and gets back only the slice it
needs — a date range, one activity, a weekly summary. That keeps each
conversation small, which on a Claude subscription means the usage limits
stretch much further.

Every Garmin tool is read-only and renders through the existing exporters, so
the output looks exactly like the Markdown files people are used to.

Unit 065 (REC-28, R-186): with the team (`equipo`, `conversaciones/desde_pc.py`), `get_contexto` for the coach, the
nutritionist and the physio of `~/tridata-equipo`: the same context the team of the app sees (read-only, never
transcribing a medical PDF). Unit 066 (REC-29, R-190 to R-197): `preparar_cambio` says what a change would do
(read-only) and, with Andrés's «sí», `hacer_cambio` makes it as if he had confirmed its card in the app (Google
Calendar and the watch included); `apuntar_comida` and `apuntar_peso` log a meal or a weight as the dictation does.
Those three are the ONLY ones that write, and each one leaves a message in the conversation «Desde el PC».

Run it with `tridata-mcp` (stdio transport) and register that command in
your MCP client, e.g. for Claude Code:

    claude mcp add tridata -- /path/to/env/bin/tridata-mcp
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .exporters import (
    CyclingMarkdownExporter, GymMarkdownExporter, MarkdownExporter,
    MetricsMarkdownExporter, SwimmingMarkdownExporter,
)
from .metrics import LimitesDelDia, compute_pmc, daily_tss, pmc_hasta, trimp_for_activity
from .nutricion.ajustes import historial_fc_de
from .nutricion.registro import SECCIONES
from .storage import DEFAULT_DB_PATH, DataStore
from .sync import ALL_SPORT_TYPES, CYCLING_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES

if TYPE_CHECKING:  # the team needs the `app` extra (anthropic): the Garmin tools work without it
    from .conversaciones.desde_pc import Equipo

log = logging.getLogger(__name__)

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
- get_contexto (when present) gives the same context the team of the app
  sees: foods, weight, objective, trainings, sleep, HR, HRV, the plan in
  course, races and incidents. To change anything: show Andrés what
  preparar_cambio returns (or the estimated meal, or the weight) and wait
  for his «sí» before hacer_cambio, apuntar_comida or apuntar_peso; say it
  is done only when the tool says «Hecho».
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


def get_training_load(store: DataStore, days: int = 28, limites: LimitesDelDia | None = None) -> str:
    """Unit 031 (G-49): `limites(day)` is the HR confirmed in Ajustes for each day (else .env), as in Fitness."""
    if days < 1:
        raise ValueError("days must be >= 1")
    # The EMAs need the whole history to warm up; only the output is trimmed.
    series = compute_pmc(daily_tss(store.get_activities_with_laps(ALL_SPORT_TYPES), limites))
    if not series:
        return "_No activities stored yet._"
    # compute_pmc stops at the last activity; extend with rest days up to today.
    series = pmc_hasta(series, max(date.today(), series[-1]["date"]))

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


def build_server(store: DataStore, limites_fc: Callable[[], LimitesDelDia | None] | None = None, *,
                 equipo: Equipo | None = None) -> MCPServer:
    """`limites_fc()` gives, on every call, the HR of each day confirmed in Ajustes (unit 031); None: .env only.
    Unit 065/066: with `equipo` (`conversaciones.desde_pc.montar_equipo`), also the team's tools."""
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
        return get_training_load(store, days, limites_fc() if limites_fc is not None else None)

    @server.tool(name="get_weekly_summary", annotations=read_only, structured_output=False)
    def get_weekly_summary_tool(weeks: int = 8) -> str:
        """Per-week, per-sport sessions, hours, km and TRIMP for the last `weeks` weeks.
        The cheapest way to see volume trends."""
        return get_weekly_summary(store, weeks)

    @server.tool(name="get_records", annotations=read_only, structured_output=False)
    def get_records_tool() -> str:
        """Personal records, the latest race-time predictions and the latest VO2max."""
        return get_records(store)

    if equipo is not None:
        _tools_del_equipo(server, equipo)
    return server


def _tools_del_equipo(server: MCPServer, equipo: Equipo) -> None:
    """Unit 065 (R-186) and 066 (R-190 to R-197): the tools of the team on the PC."""
    from .conversaciones.desde_pc import (PAPELES_PC, TITULO, apuntar_comida_desde_pc, apuntar_peso_desde_pc,
                                          hacer_desde_pc, preparar_desde_pc, texto_contexto)
    from .nutricion.asistente import texto_acciones

    @server.tool(name="get_contexto", annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
                 structured_output=False)
    def get_contexto_tool() -> str:
        """Everything the team of the app sees today, in one text: its instructions and the catalogue of changes,
        the medical transcriptions already made, and the data in JSON (the last 8 weeks day by day with foods and
        their kcal, weight, steps, resting HR, sleep, HRV and trainings; earlier weeks summed up; the settings and
        objective of today; races; HR zones; the plan in course with its sessions and incidents). Read-only."""
        return texto_contexto(equipo)

    papeles = f"papel: who you are, one of {', '.join(PAPELES_PC)}."
    registro = (f"When it is done, it leaves a message of `papel` in the conversation «{TITULO}» of the Asistente tab "
                "of the app. Say it is done only if the answer starts with «Hecho»; otherwise tell Andrés what it "
                "says: nothing was done.")
    forma = ("cambio: an object with `accion` (an id of the list below) and the fields that action uses: objeto, "
             "campo, valor, valores, texto, cantidad, unidad, kcal_estimadas, lineas, dia, desde, fecha (dates "
             "YYYY-MM-DD), antes_dicho, falta, hora (HH:MM, only when Andrés says it); the rest omitted or null.")
    preparar_desc = (
        "Say what ONE change to Andrés's data or plan would do, exactly as the app would do it (its text, before and "
        "after, where, the lines for the watch and its warnings), and its fingerprint (`Huella`). It changes "
        "NOTHING. Show Andrés that result as it is and ask him; only after his «sí» call hacer_cambio with the same "
        "`cambio` and that `huella`. If he says no, do nothing. If he corrects it, prepare it again.\n"
        f"{forma} texto (optional): what Andrés asked, in his words.\n\n" + texto_acciones())

    @server.tool(name="preparar_cambio", description=preparar_desc,
                 annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False), structured_output=False)
    def preparar_cambio_tool(cambio: dict[str, Any], texto: str = "") -> str:
        return preparar_desde_pc(equipo, cambio, texto)

    hacer_desc = (
        "Make ONE change that preparar_cambio already showed and Andrés answered «sí» to: it is applied as if he "
        "confirmed its card in the app (Google Calendar updated; `mandar_a_garmin` sends the session to his watch). "
        "NEVER call it without showing him the result of preparar_cambio and getting his «sí» first. "
        "If the data changed since it was prepared, nothing is done and the new version comes back: show it to "
        f"him again.\n{papeles} cambio: the same object given to preparar_cambio. huella: the `Huella` it returned. "
        f"texto (optional): a short line for Andrés, in Spanish.\n{registro}")

    @server.tool(name="hacer_cambio", description=hacer_desc,
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                                             open_world_hint=True),
                 structured_output=False)
    def hacer_cambio_tool(papel: str, cambio: dict[str, Any], huella: str, texto: str = "") -> str:
        return hacer_desde_pc(equipo, papel, cambio, huella, texto)

    comida_desc = (
        "Log what Andrés ate (told, or estimated by you from a photo) on one day, as the dictation of the app logs "
        "it. FIRST show him each food with its quantity and kcal (and the section, if any) and ask; only with his "
        "«sí» call this. If one food does not pass the checks, none is logged and the answer says why.\n"
        f"{papeles} dia: YYYY-MM-DD. alimentos: a list of objects with nombre, cantidad (> 0), unidad "
        "(g, ml or unidad), kcal (>= 0; required with a quantity), and optionally proteina_g, hidratos_g, grasa_g "
        "and estimado (true if you estimated it). texto (optional): what he said, in Spanish.\n"
        f"seccion (optional): use it when Andrés says which meal it goes in («en el desayuno», «un gel en la "
        f"carrera»), and all the foods go into that section of the day: one of {', '.join(SECCIONES)} "
        "(media_manana is «media mañana», carrera is «Nutrición en carrera», for gels and drinks of a race or a long "
        "training). If he does not say it, omit it: they go «Sin sección». Another value logs nothing.\n" + registro)

    @server.tool(name="apuntar_comida", description=comida_desc,
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                             open_world_hint=False),
                 structured_output=False)
    def apuntar_comida_tool(papel: str, dia: str, alimentos: list[dict[str, Any]], texto: str = "",
                            seccion: str | None = None) -> str:
        return apuntar_comida_desde_pc(equipo, papel, dia, alimentos, texto, seccion)

    peso_desc = (
        "Log Andrés's weight of one day (today or a past day), as the weight piece of the dictation: it replaces the "
        "one of that day. FIRST tell him the kg and the day and ask; only with his «sí» call this. A weight out of "
        f"range is not logged and the answer says why.\n{papeles} kg: the kilos. dia: YYYY-MM-DD. "
        "nota (optional).\n" + registro)

    @server.tool(name="apuntar_peso", description=peso_desc,
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                             open_world_hint=False),
                 structured_output=False)
    def apuntar_peso_tool(papel: str, kg: float, dia: str, nota: str | None = None) -> str:
        return apuntar_peso_desde_pc(equipo, papel, kg, dia, nota)


def main() -> None:
    # stdout is the MCP channel on stdio; logs must go to stderr.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()  # TRIDATA_HR_MAX / TRIDATA_HR_REST for TRIMP
    store = DataStore()
    equipo = None
    try:  # unit 065: the team's tools need the `app` extra; without it, the Garmin tools as before
        from .conversaciones.desde_pc import montar_equipo
    except ImportError as exc:
        log.warning("the team's tools are off: install the `app` extra (%s)", exc)
    else:
        medicos_dir = os.environ.get("TRIDATA_MEDICOS_DIR", "").strip()
        equipo = montar_equipo(DEFAULT_DB_PATH, store=store,
                               medicos_dir=Path(medicos_dir).expanduser() if medicos_dir else None)
    build_server(store, limites_fc=lambda: historial_fc_de(DEFAULT_DB_PATH).limites, equipo=equipo).run("stdio")


if __name__ == "__main__":
    main()
