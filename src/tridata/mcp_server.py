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
(read-only) and, with Andrés's «sí», `hacer_cambio` makes it as the app makes a change (Google
Calendar and the watch included); `apuntar_comida` and `apuntar_peso` log a meal or a weight as the dictation does.
Those three are the ONLY ones that write, and each one leaves a message in the conversation «Desde el PC».
Unit 081 (P-20261105-cf32bea5): what the app did with the paid AI, done by the coach: `preparar_plan` / `importar_plan`
(a plan .md the coach ordered, R-131, R-132), `get_analiticas_pendientes`, `preparar_transcripcion` /
`guardar_transcripcion` (a medical PDF the coach read, R-221, R-222), `get_recetas` (S-72; `apuntar_comida` takes the
saved kcal of a recipe or a fixed food, R-42) and `get_rutas` / `get_perfil_ruta` (S-73, R-151..R-154). The two that
save write their message in «Desde el PC» too. None of them calls a paid AI; only the routes read Garmin.
Unit 082 (REC-34): `apuntar_toma` (coffees and B12 with their hour and mg, apart from the foods, without kcal) and
`apuntar_leche_cafes` (the milk of the coffees of a day, once, spread among them), with the same «sí» and «Hecho» as
`apuntar_comida`, which takes the `hora` of its section too.
Unit 109 (REC-52, R-322): `apuntar_toma` also logs gels with caffeine and salt tablets (by hour or minute of the race),
and `guardar_producto_suplemento` saves or changes a saved gel or salt, with the same «sí» and «Hecho».
Unit 098 (REC-43, R-269..R-273): `preparar_macros` (read-only: what has no macros, and the table and fingerprint of the
team's values) and `guardar_macros` (with Andrés's «sí» and that fingerprint, as «estimado»); `get_recetas` shows the
macros of what is saved, and `apuntar_comida` asks the team to estimate those of anything not saved.
Unit 101 (REC-45, R-284): the same pair fills in what was logged without macros in the last 14 days (`apuntado:<id>`).
Unit 102 (REC-46, R-291): `preparar_zapatilla` (read-only: what the km Andrés says of a shoe would leave, and its
fingerprint) and `apuntar_zapatilla` (with his «sí» and that fingerprint, «dicho por ti»); `get_contexto` carries the
shoes in use with their km, cap and mark. Unit 103 (REC-47, R-292, R-294): the same pair with `accion` «alta» (a shoe
given here) and «carrera» (the shoe of a run counted here); `get_contexto` carries the runs without a shoe too.
Unit 115 (REC-56, REC-57): `get_plan` (read-only: the list of plans, or any plan whole); `preparar_plan` /
`importar_plan` also save a plan the coach designs (with the planned km of each week); `preparar_cambio` takes the plan
a change of the plan speaks of (`plan`), and its list carries the weeks, the phases and `estrategia_al_reloj`.

Run it with `tridata-mcp` (stdio transport) and register that command in
your MCP client, e.g. for Claude Code:

    claude mcp add tridata -- /path/to/env/bin/tridata-mcp
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .exporters import (
    CyclingMarkdownExporter, GymMarkdownExporter, MarkdownExporter,
    MetricsMarkdownExporter, SwimmingMarkdownExporter,
)
from .fitness import por_semana
from .metrics import LimitesDelDia, compute_pmc, daily_tss, pmc_hasta, trimp_for_activity
from .nutricion.ajustes import historial_fc_de
from .nutricion.registro import SECCIONES
from .storage import DEFAULT_DB_PATH, DataStore
from .sync import ALL_SPORT_TYPES, CYCLING_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES, carpeta_datos

if TYPE_CHECKING:  # imported when the team is built (`main`), so the Garmin tools load only what they use
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
  course, races and incidents; each food only for the last 14 days
  (older days: get_comidas, by dates). To change anything: show Andrés what
  preparar_cambio returns (or the estimated meal, or the weight) and wait
  for his «sí» before hacer_cambio, apuntar_comida or apuntar_peso; say it
  is done only when the tool says «Hecho».
- Before judging a training or a race, open its laps with get_activity
  (its id is in get_contexto or list_activities): the average hides a
  progressive run or a split that went wrong. Before talking about a race
  of the plan, look at its `anteriores` in get_contexto (the same race in
  other years, with their ids: open their laps too) and at the
  `comparativa` of the plan.
- get_contexto carries only the plan in course: read any other plan
  (past or future) whole with get_plan. To design a whole new plan, or
  import a plan .md, give it in its parts to preparar_plan and, with his
  «sí», importar_plan. Weeks, phases and sessions of any plan, and the
  strategy of a race to the watch: preparar_cambio with its plan.
- To read a new medical PDF, log with his recipes or
  see the profile of a route:
  get_analiticas_pendientes / preparar_transcripcion /
  guardar_transcripcion, get_recetas, get_rutas / get_perfil_ruta. Same
  rule: what saves is shown first and done only with his «sí».
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

    # week -> sport -> totals; the grouping is shared with Fitness › weeks (unit 090), the sums are this tool's own
    grupos = por_semana(store.get_activities_with_laps(ALL_SPORT_TYPES), first_monday, weeks,
                        lambda a: _sport_of(a) or "other")
    totals: dict[date, dict[str, dict[str, float]]] = {}
    for monday, by_sport_acts in grupos.items():
        totals[monday] = {}
        for sport, acts in by_sport_acts.items():
            t = totals[monday][sport] = {"n": 0, "hours": 0.0, "km": 0.0, "trimp": 0.0}
            for a in acts:
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
                                          apuntar_toma_desde_pc, guardar_producto_desde_pc, hacer_desde_pc,
                                          leche_cafes_desde_pc, preparar_desde_pc, texto_contexto)
    from .nutricion.suplementacion import FORMAS, HORAS_TIPICAS
    from .nutricion.asistente import texto_acciones

    @server.tool(name="get_contexto", annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
                 structured_output=False)
    def get_contexto_tool() -> str:
        """Everything the team of the app sees today, in one text: its instructions and the catalogue of changes,
        the medical transcriptions already made, and the data in JSON (the last 8 weeks day by day with their kcal,
        weight, steps, resting HR, sleep, HRV and trainings, and each food only for the last 14 days: the foods of
        earlier days are asked with get_comidas by dates; earlier weeks summed up; the settings and
        objective of today; races; HR zones; the plan in course with its sessions and incidents). Unit 082: each day
        also carries `suplementacion` (coffees and B12 with their hour and mg, the caffeine of the day, the last dose
        and the milk of the coffees) and `horas_comidas` (the hour of each section with food, «~» if typical), next to
        its sleep, to relate them; unit 100: only the last 14 days carry them, the earlier ones only the caffeine of
        the day. Unit 109: `suplementacion` also carries the gels and salt tablets (by hour, and «en_carrera» by
        minute of the race), the caffeine by origin (`cafeina_cafe_mg`, `cafeina_geles_mg`) and the sodium; the days
        after today with gels or salt carry it in `planeado_despues_de_hoy`; `productos_suplemento`, his saved gels
        and salts. Unit 114: the plan carries its `comparativa` (with other years) and each race not yet run its
        `anteriores` (the same race in other years: id, date, km, time and pace). The laps are not here: before
        judging a training or a race, open its laps with get_activity, and before talking about a race look at its
        `anteriores` (and their laps) and the `comparativa`. Unit 115: only the plan in course is here; any other
        plan, with get_plan. Read-only."""
        return texto_contexto(equipo)

    papeles = f"papel: who you are, one of {', '.join(PAPELES_PC)}."
    registro = (f"When it is done, it is written down in «{TITULO}» of the app (what was done, by `papel`, and when). "
                "Say it is done only if the answer starts with «Hecho»; otherwise tell Andrés what it says: nothing "
                "was done.")
    forma = ("cambio: an object with `accion` (an id of the list below) and the fields that action uses: objeto, "
             "campo, valor, valores, texto, cantidad, unidad, kcal_estimadas, lineas, dia, desde, hasta, fecha (dates "
             "YYYY-MM-DD; hasta: the last day of several days in a row, only for niveles_dia and objetivo_dia), "
             "antes_dicho, falta, hora (HH:MM, only when Andrés says it), eleccion (a whole number), plan (the plan a "
             "change of the plan speaks of, by its name or its id from get_plan, only when it is not the one in "
             "course); the rest omitted or null.")
    preparar_desc = (
        "Say what ONE change to Andrés's data or plan would do, exactly as the app would do it (its text, before and "
        "after, where, the lines for the watch and its warnings), and its fingerprint (`Huella`). It changes "
        "NOTHING. Show Andrés that result as it is and ask him; only after his «sí» call hacer_cambio with the same "
        "`cambio` and that `huella`. If he says no, do nothing. If he corrects it, prepare it again.\n"
        "If it answers «Hay más de uno» (a food logged more than once that day with that name), it lists each one "
        "with its número, quantity, kcal and section: ask Andrés which one and prepare it again with the same "
        "`cambio` plus eleccion: that número (and give hacer_cambio that same `cambio`). A número that is not one "
        "of those does nothing.\n"
        f"{forma} texto (optional): what Andrés asked, in his words.\n\n" + texto_acciones())

    @server.tool(name="preparar_cambio", description=preparar_desc,
                 annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False), structured_output=False)
    def preparar_cambio_tool(cambio: dict[str, Any], texto: str = "") -> str:
        return preparar_desde_pc(equipo, cambio, texto)

    hacer_desc = (
        "Make ONE change that preparar_cambio already showed and Andrés answered «sí» to: it is applied as the app "
        "applies a change (Google Calendar updated; `mandar_a_garmin` sends the session to his watch). "
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
        "(g, ml or unidad), kcal (>= 0; required with a quantity), proteina_g, hidratos_g, grasa_g (grams for that "
        "quantity) and estimado (true if you estimated it). A food that is one of his saved recipes or fixed foods (by "
        "its name or an alias, see get_recetas), a variant of a recipe named «Receta · variante» («Garbanzos de la "
        "abuela · sin chorizo», by grams), or with guardado: {tipo: receta, fijo or variante, id}, takes its kcal, its "
        "macros and its origin from what is saved (yours are ignored): show him those, worked out from get_recetas "
        "(macros_100g of a recipe or a variant, the macros of a fixed food), as its kcal. If he names a change of a "
        "saved recipe that is not saved as a variant («sin chorizo»), offer to save it first (preparar_cambio, "
        "variante_nueva) instead of logging it as a loose food. postre: true on a food he says is the dessert («de "
        "postre un yogur»): it goes in the same section, shown apart at the end of it, and adds up as always. For anything else, estimate its protein, "
        "carbs and fat (proteina_g, hidratos_g, grasa_g) together with its kcal and show them to Andrés in the same "
        "confirmation, before his «sí»; it is logged as you give it, as estimated (estimado true). If it only looks "
        "like a saved one, ask him first. texto (optional): what he said, in Spanish.\n"
        f"seccion (optional): use it when Andrés says which meal it goes in («en el desayuno», «un gel en la "
        f"carrera»), and all the foods go into that section of the day: one of {', '.join(SECCIONES)} "
        "(media_manana is «media mañana», carrera is «Nutrición en carrera», for gels and drinks of a race or a long "
        "training). If he does not say it, omit it: they go «Sin sección». Another value logs nothing.\n"
        "hora (optional, HH:MM, only with seccion): when Andrés says when he ate it («cené a las diez y cuarto»), that "
        "section of that day keeps that hour; without it the app shows the typical one ("
        + ", ".join(f"{k} {v}" for k, v in HORAS_TIPICAS.items()) + ").\n" + registro)

    @server.tool(name="apuntar_comida", description=comida_desc,
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                             open_world_hint=False),
                 structured_output=False)
    def apuntar_comida_tool(papel: str, dia: str, alimentos: list[dict[str, Any]], texto: str = "",
                            seccion: str | None = None, hora: str | None = None) -> str:
        return apuntar_comida_desde_pc(equipo, papel, dia, alimentos, texto, seccion, hora)

    toma_desc = (
        "Log Andrés's coffees, B12, gels and salt tablets of one day in Nutrición › Día › «Suplementación», apart "
        "from the meals and "
        "WITHOUT kcal (the total and the margin do not change; the milk goes with apuntar_leche_cafes). FIRST show him "
        "each dose with its form, its hour and the mg of caffeine you estimate, and ask; only with his «sí» call this. "
        "All of them or none; the answer says why if not.\n"
        f"{papeles} dia: YYYY-MM-DD. tomas: a list of objects with que (cafe or b12), hora (HH:MM; for «a media "
        "tarde» propose one and let him see it) and, for a coffee: forma, one of "
        f"{', '.join(FORMAS)} (expreso, doble and ingles at a café; italiana with gramos of coffee and fraccion of "
        "the pot he drinks, 0.5 for half, 1 for all of it; soluble with cucharadas and gramos of each), mg (the "
        "caffeine you estimate, always) and solo (true if he says it had no milk). For the B12, dosis (optional "
        "text) and nothing else. Without hora: a dose of today takes the hour it is now; of another day nothing is "
        "logged and the answer says «falta la hora»: ask him. texto (optional): what he said, in Spanish.\n"
        "Gels with caffeine and salt tablets (also for a race or a long training on a day that has not come yet, "
        "«la tirada del domingo»): que gel or sal, producto (its name), cantidad (how many, > 0; default 1), mg (gel: "
        "the mg of caffeine PER GEL) and sodio_mg (the mg of sodium per unit; required for a salt tablet), and hora "
        "(HH:MM) or minuto (the minute of the race, an integer: «en el min 60»); one dose per moment (two gels at "
        "minutes 60 and 90 are two doses). If producto is one of his saved products (get_contexto, "
        "productos_suplemento) and he does not say the mg, omit mg and sodio_mg: the saved ones are used, × cantidad. "
        "Without hora or minuto nothing is logged and the answer says «falta la hora o el minuto»: ask him. Logging a "
        "gel does NOT log its kcal: ask him whether he also wants it as a food in «Nutrición en carrera» "
        "(apuntar_comida, seccion carrera). When a gel or a salt tablet is not saved yet, offer to save it with "
        "guardar_producto_suplemento.\n"
        + registro)

    @server.tool(name="apuntar_toma", description=toma_desc,
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                             open_world_hint=False),
                 structured_output=False)
    def apuntar_toma_tool(papel: str, dia: str, tomas: list[dict[str, Any]], texto: str = "") -> str:
        return apuntar_toma_desde_pc(equipo, papel, dia, tomas, texto)

    producto_desc = (
        "Save one of Andrés's gels with caffeine or salt tablets as a product, so its values are used each time it "
        "is logged with apuntar_toma (by its name), or change the saved one with that name (capitals do not matter). "
        "FIRST show him the name, the kind and the values per unit and ask; only with his «sí» call this. Changing "
        "or saving a product never changes the doses already logged.\n"
        f"{papeles} nombre: its name («Gel 226 cola»). tipo: gel or sal. mg_cafeina: the mg of caffeine per gel "
        "(required for a gel; 0 if it has none; omit it for a salt). sodio_mg: the mg of sodium per unit (required "
        "for a salt). To change one, give its name and only what changes. texto (optional): what he said.\n"
        + registro)

    @server.tool(name="guardar_producto_suplemento", description=producto_desc,
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                             open_world_hint=False),
                 structured_output=False)
    def guardar_producto_suplemento_tool(papel: str, nombre: str, tipo: str, mg_cafeina: float | None = None,
                                         sodio_mg: float | None = None, texto: str = "") -> str:
        return guardar_producto_desde_pc(equipo, papel, nombre, tipo, mg_cafeina, sodio_mg, texto)

    leche_desc = (
        "Log the milk Andrés put in his coffees of one day, as he says it at the end of the day («hoy 250 ml de leche "
        "en los cafés»): it is logged ONCE as a food of that day with its kcal (it adds to the total like any food) "
        "and spread in equal parts among the coffees of that day not marked solo (only to see when it was taken). "
        "Said again for the same day, it replaces the one logged. FIRST tell him the ml and the kcal and ask; only "
        f"with his «sí» call this.\n{papeles} dia: YYYY-MM-DD. ml: the millilitres (> 0). kcal: the kcal of those "
        "ml (from his saved milk in get_recetas if he has one, or estimated). nombre (optional): which milk. texto "
        "(optional): what he said.\n" + registro)

    @server.tool(name="apuntar_leche_cafes", description=leche_desc,
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                             open_world_hint=False),
                 structured_output=False)
    def apuntar_leche_cafes_tool(papel: str, dia: str, ml: float, kcal: float | None = None,
                                 nombre: str | None = None, texto: str = "") -> str:
        return leche_cafes_desde_pc(equipo, papel, dia, ml, kcal, nombre, texto)

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

    _tools_081(server, equipo, papeles, registro)


def _tools_081(server: MCPServer, equipo: Equipo, papeles: str, registro: str) -> None:
    """Unit 081 (P-20261105-cf32bea5): the plan .md, the medical PDF, the recipes and the routes, by the coach."""
    from .conversaciones.desde_pc import (analiticas_pendientes, guardar_macros_desde_pc,
                                          guardar_transcripcion_desde_pc, importar_plan_desde_pc,
                                          perfil_ruta_desde_pc, plan_desde_pc, preparar_macros_desde_pc,
                                          preparar_plan_desde_pc,
                                          preparar_transcripcion_desde_pc, recetario_texto, rutas_desde_pc)
    from .conversaciones.desde_pc import MAX_DIAS_COMIDAS, comidas_desde_pc

    lee = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    guarda = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                             open_world_hint=False)

    plan_forma = (
        "plan: {nombre (the «# » title of the .md, optional), apartados: [one per section of the .md, in its order: "
        "{titulo (its heading, \"\" for what comes before the first one), parte, filas, texto (the section as it is, "
        "without its heading)}]}. parte: objetivos (races and objectives), semanas_hechas (weeks done, with what was "
        "done), previsto (weeks to come), sesiones_clave, nutricion (race nutrition), incidencias (pains, injuries, "
        "illnesses), comparativa (with another year) or nota (none of them: it is kept as a note, as it is). filas: "
        "one line per thing, its kind first and its fields separated by «|», an absent field empty; dates "
        "YYYY-MM-DD, times H:MM, km with a decimal comma:\n"
        "- carrera|nombre|fecha|objetivo|estado o resultado (pendiente, or ✅ and the time)|principal (sí o no)\n"
        "- semana|número|desde|hasta|tiempo previsto|sesiones clave en pocas palabras|tiempo en movimiento hecho|km "
        "hechos|km previstos (the last one optional; a week to come leaves the two done empty)\n"
        "- sesion|fecha|título|bloques separados por «;»|clave (sí o no)|hora (HH:MM, only if it says it)\n"
        "- fase|nombre|semana desde|semana hasta (numbers of weeks of the plan)\n"
        "- nutricion|producto|pauta · incidencia|fecha|texto · comparativa|texto · nota|texto\n"
        "Copy, never invent: a row that cannot be read goes to the notes with its text. elecciones (optional): for a "
        "week with a difference, {\"4\": \"garmin\"} to keep Garmin's instead of the plan's.")

    @server.tool(name="preparar_plan", annotations=lee, structured_output=False, description=(
        "Say what saving a whole training plan would save, as the import of the app: a plan .md you order in its "
        "parts, or a new plan you design yourself for an objective (its races, phases, weeks with their dates, "
        "planned time and km, and sessions with their day and blocks, in the same parts). It shows the plan whole, "
        "each week already done whose Garmin numbers are not the plan's (the plan's is proposed: it comes from "
        "Strava), each race already in another plan that day and what fits nowhere (saved as a note of the plan, as "
        "it is), with its fingerprint (`Huella`); a week that ends before it starts or overlaps another is refused, "
        "naming it. It saves NOTHING and changes no other plan. Show Andrés the result and ask; only after his «sí» "
        "call importar_plan with the same plan, elecciones and that huella.\n" + plan_forma))
    def preparar_plan_tool(plan: dict[str, Any] | str, elecciones: dict[str, str] | None = None) -> str:
        return preparar_plan_desde_pc(equipo, plan, elecciones)

    @server.tool(name="importar_plan", annotations=guarda, structured_output=False, description=(
        "Save the plan preparar_plan already showed and Andrés answered «sí» to, as a new plan, as the import of the "
        "app saves it. NEVER call it without his «sí». If anything changed since (Garmin, the plan, the choices) "
        f"nothing is saved and the new version comes back: show it to him again.\n{papeles} plan, elecciones: the "
        "same given to preparar_plan. huella: the `Huella` it returned. texto (optional): a short line for Andrés, "
        f"in Spanish.\n{registro}"))
    def importar_plan_tool(papel: str, plan: dict[str, Any] | str, huella: str,
                           elecciones: dict[str, str] | None = None, texto: str = "") -> str:
        return importar_plan_desde_pc(equipo, papel, plan, huella, elecciones, texto)

    @server.tool(name="get_plan", annotations=lee, structured_output=False, description=(
        "Read Andrés's training plans. Without plan: the list of the plans saved (past, in course and to come), each "
        "with its id, name, main race and date, first and last week, and which one is in course. With plan (its name "
        "or its id): that plan whole, as get_contexto gives the plan in course: races (with their target, result and "
        "strategy), phases, weeks with planned (time and km) and done, sessions by day with their id and blocks, "
        "nutrition, notes, comparison and incidents. To give your opinion of a plan («¿qué te parece?») read it "
        "here; what you propose to change goes with preparar_cambio and his «sí». Read-only: it changes nothing."))
    def get_plan_tool(plan: str | None = None) -> str:
        return plan_desde_pc(equipo, plan)

    @server.tool(name="get_analiticas_pendientes", annotations=lee, structured_output=False, description=(
        "The PDF of the folder of Andrés's medical documents (blood tests, stress tests) that have no transcription "
        "yet (new, or changed since it was read) and where each one is on this PC, so you can read it. Read-only; "
        "the ones already transcribed are in get_contexto."))
    def get_analiticas_pendientes_tool() -> str:
        return analiticas_pendientes(equipo)

    transcripcion_forma = (
        "fichero: its name as get_analiticas_pendientes says it (or its path). transcripcion: {tipo (analitica, "
        "prueba_esfuerzo or otro), titulo (what document it is, short), fecha (YYYY-MM-DD of the document, or null), "
        "valores: [{prueba, valor (as it appears, with its comma or dot), unidad, rango (its reference range), fecha "
        "(only if it is not the document's)}] — in a stress test also each stage with its power or speed, HR, oxygen "
        "uptake or lactate, and the thresholds and maximums —, notas: [the conclusions of the report, literal and "
        "short]}. Transcribe, never interpret nor invent.")

    @server.tool(name="preparar_transcripcion", annotations=lee, structured_output=False, description=(
        "Say what would be kept as the transcription of one medical PDF you read: how many values and of what date, "
        "and its fingerprint (`Huella`). It saves NOTHING. Show Andrés that and ask; only after his «sí» call "
        "guardar_transcripcion with the same fichero, transcripcion and that huella. A file that is not in the "
        "folder, or a transcription without values, is refused and the answer says why.\n" + transcripcion_forma))
    def preparar_transcripcion_tool(fichero: str, transcripcion: dict[str, Any]) -> str:
        return preparar_transcripcion_desde_pc(equipo, fichero, transcripcion)

    @server.tool(name="guardar_transcripcion", annotations=guarda, structured_output=False, description=(
        "Keep the transcription preparar_transcripcion already showed and Andrés answered «sí» to as the one of that "
        "PDF: from then on get_contexto (and the whole team) has it. NEVER call it without his «sí». If the PDF or the "
        f"transcription changed since, nothing is kept.\n{papeles} fichero, transcripcion: the same given to "
        "preparar_transcripcion. huella: the `Huella` it returned. texto (optional): a short line for Andrés, in "
        f"Spanish.\n{registro}"))
    def guardar_transcripcion_tool(papel: str, fichero: str, transcripcion: dict[str, Any], huella: str,
                                   texto: str = "") -> str:
        return guardar_transcripcion_desde_pc(equipo, papel, fichero, transcripcion, huella, texto)

    @server.tool(name="get_recetas", annotations=lee, structured_output=False, description=(
        "Andrés's saved recipes and fixed foods (ingredientes), in JSON: id, nombre, alias; a recipe with its "
        "estado (definitiva: weighed; provisional: not weighed; sin_kcal or por_revisar: cannot be used), kcal_100g "
        "(or kcal_100g_aprox when not weighed) and kcal_receta (the whole recipe, for a fraction of it); a fixed food "
        "with its kcal per 100g or per unidad (comun: of the list of common foods). When he names one of them (or an "
        "alias) with a quantity, its kcal are these: a recipe by grams (unidad g) or by fraction (unidad receta, "
        "cantidad 0.5 for half), a fixed food in its own unit. Macros (protein, carbs, fat in grams): a fixed food "
        "with proteina, hidratos, grasa on the base of its kcal and macros_origen (estimado or etiqueta); a recipe "
        "with macros_100g and macros_receta (the whole recipe), or sin_macros (how many ingredients have none: then "
        "it has no macros). A portion carries them in proportion to its kcal. Missing ones: preparar_macros. "
        "Unit 112: a recipe may have variantes (id, nombre such as «sin chorizo», kcal_100g and macros_100g if they "
        "have them): log one as «Receta · variante» by grams (or guardado tipo variante with its id), never with the "
        "recipe's kcal; save, change or delete one with preparar_cambio (variante_nueva, variante_cambiar, "
        "variante_borrar). Read-only."))
    def get_recetas_tool() -> str:
        return recetario_texto(equipo)

    @server.tool(name="get_comidas", annotations=lee, structured_output=False, description=(
        "What Andrés ate on the days you ask for, by dates: get_contexto carries each food only for the last 14 days "
        "(today and the 13 before); the earlier days come there with their totals but without their foods. desde, "
        f"hasta: dates AAAA-MM-DD, both included, at most {MAX_DIAS_COMIDAS} days each time (ask in several pieces "
        "for more). In JSON, each day with its fecha and dia (weekday), and the same detail as the recent days of "
        "get_contexto: comidas (each food with its quantity, kcal and estado comido or planeado), horas_comidas (the "
        "hour of each meal, «~» if it is the typical one) and suplementacion (coffees and B12 with their hour and mg, "
        "the caffeine of the day, the last dose and the milk of the coffees). A day with nothing logged comes with "
        "only its date. A range that is too long, backwards or with a date not understood says what to fix and gives "
        "no data. Read-only."))
    def get_comidas_tool(desde: str, hasta: str) -> str:
        return comidas_desde_pc(equipo, desde, hasta)

    @server.tool(name="preparar_macros", annotations=lee, structured_output=False, description=(
        "Fill in, once, the protein, carbs and fat (macros) of Andrés's saved ingredients and recipes that have none, "
        "and of the foods he logged without them in the last 14 days (today and the 13 before, eaten and planned). "
        "Without propuestas: what has no macros yet (his ingredients, the common ones, and the estimated lines of his "
        "recipes in use), each with its clave, its base and its kcal; then what he logged without macros, each with "
        "its clave apuntado:<id>, its day, quantity and kcal: «calculado de …» (it comes from a saved ingredient, "
        "recipe or common food that has macros: the app already worked them out for its quantity) or «a estimar». "
        "When get_contexto says alimentos_sin_macros, offer him to review them. With propuestas: [{clave, proteina, "
        "hidratos, grasa}] (grams for its base: per 100 g or per unit as its kcal; for a recipe line or a logged "
        "food, for that quantity; a logged food already calculated goes with its clave alone; if Andrés corrects the "
        "macros of a logged food, add corregido: true and they are saved «de etiqueta»), the table that would be "
        "saved, warning a row whose 4·P + 4·H + 9·G is far from its kcal, the recipes in use that are recomputed, "
        "and its fingerprint (`Huella`). No kcal ever changes. Impossible values (negative, or more than 100 g per "
        "100 g) are refused with the reason. It saves NOTHING: show Andrés the table and ask; only after his «sí» "
        "call guardar_macros with the same propuestas and that huella."))
    def preparar_macros_tool(propuestas: list[dict[str, Any]] | None = None) -> str:
        return preparar_macros_desde_pc(equipo, propuestas)

    @server.tool(name="guardar_macros", annotations=guarda, structured_output=False, description=(
        "Save the macros preparar_macros already showed and Andrés answered «sí» to, as «estimado» (a logged food "
        "with corregido: true, «de etiqueta»), and recompute his recipes in use (no kcal changes; what was logged "
        "changes only in the macros of the apuntado:<id> rows). NEVER call it without his «sí». "
        "rechazar (optional): the claves he said no to; they stay without macros. If anything changed since it was "
        f"prepared nothing is saved and the new version comes back: show it to him again.\n{papeles} propuestas: the "
        "same given to preparar_macros. huella: the `Huella` it returned. texto (optional): a short line for Andrés, "
        f"in Spanish.\n{registro}"))
    def guardar_macros_tool(papel: str, propuestas: list[dict[str, Any]], huella: str,
                            rechazar: list[str] | None = None, texto: str = "") -> str:
        return guardar_macros_desde_pc(equipo, papel, propuestas, huella, rechazar, texto)

    from .conversaciones.desde_pc import apuntar_zapatilla_desde_pc, preparar_zapatilla_desde_pc

    zapatilla_forma = ("zapatilla: its name as get_contexto says it (or part of it, e.g. «Nimbus»); for accion alta, "
                       "the name of the new shoe. km: the km Andrés says it has in total (a number, a decimal "
                       "allowed); for alta, its km at the start; for carrera, the km of that run. accion (optional): "
                       "«km» (by default: the km of a shoe in use), «alta» (unit 103: give a new running shoe, counted "
                       "here from now with the runs of Garmin; needs uso, and tope or the one of its name) or "
                       "«carrera» (unit 103: the shoe of a run counted here, e.g. one of carreras_sin_zapatilla of "
                       "get_contexto, or to change the one the plan gave). tope (alta): its cap in km, a whole number "
                       "from 50 to 3000. uso (alta): «suave» (rodajes), «calidad» (series, ritmo, tiradas), "
                       "«competicion» or «trail» (unit 106: the runs Garmin keeps as trail go to it, whatever the "
                       "plan says). carrera (carrera): its date (año-mes-día) or its id from get_contexto.")

    @server.tool(name="preparar_zapatilla", annotations=lee, structured_output=False, description=(
        "Say what saving the km Andrés says one of his running shoes in use has would leave (unit 102): its km now and "
        "where they come from (Strava, once a day, counted here, or said by him), and how it would stay, with its "
        "fingerprint (`Huella`). Also (unit 103) a new shoe given here (accion alta) or the shoe of one run "
        "(accion carrera). It saves NOTHING. Show Andrés the result and ask; only after his «sí» call "
        "apuntar_zapatilla with the same zapatilla, km, accion, tope, uso, carrera and that huella. If the name fits "
        f"more than one, or none, it says which there are.\n{zapatilla_forma}"))
    def preparar_zapatilla_tool(zapatilla: str, km: float, accion: str = "km", tope: int | None = None,
                                uso: str | None = None, carrera: str | None = None) -> str:
        return preparar_zapatilla_desde_pc(equipo, zapatilla, km, accion, tope, uso, carrera)

    @server.tool(name="apuntar_zapatilla", annotations=guarda, structured_output=False, description=(
        "Save what preparar_zapatilla already showed and Andrés answered «sí» to: the km of a shoe («dicho por ti», "
        "or a new start for one counted here), a new shoe (accion alta) or the shoe of a run (accion carrera). NEVER "
        "call it without his «sí». If anything changed since it was prepared nothing is saved and the new version "
        f"comes back: show it to him again.\n{papeles} {zapatilla_forma} The same given to preparar_zapatilla. "
        "huella: the `Huella` it returned. texto (optional): a short line for Andrés, in "
        f"Spanish.\n{registro}"))
    def apuntar_zapatilla_tool(papel: str, zapatilla: str, km: float, huella: str, texto: str = "",
                               accion: str = "km", tope: int | None = None, uso: str | None = None,
                               carrera: str | None = None) -> str:
        return apuntar_zapatilla_desde_pc(equipo, papel, zapatilla, km, huella, texto, accion, tope, uso, carrera)

    garmin_lee = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    @server.tool(name="get_rutas", annotations=garmin_lee, structured_output=False, description=(
        "The routes saved in Andrés's Garmin Connect and his last runs done, each with its id, to ask for the profile "
        "of one with get_perfil_ruta. If Garmin does not answer it says so: then ask him for the GPX; if Garmin is "
        "limiting the requests it says to wait: tell him so and do not ask for the GPX. Read-only."))
    def get_rutas_tool() -> str:
        return rutas_desde_pc(equipo)

    @server.tool(name="get_perfil_ruta", annotations=garmin_lee, structured_output=False, description=(
        "The profile of ONE route to prepare a race: its distance, climb, the altitude every 100 m and its stretches "
        "of climb (subida), descent (bajada) and flat (llano) with their climb and slope. Never its coordinates. "
        "Give one of: garmin (the id of a route saved in Garmin, from get_rutas), actividad (the id of a run done) or "
        "gpx (the path of a GPX file inside Andrés's sports folder, e.g. routes/x.gpx; files outside it are not read). "
        "Without altitude, or if Garmin does not answer, it says so: ask "
        "Andrés for a GPX (if Garmin is limiting the requests, it says to wait: no GPX then). Read-only."))
    def get_perfil_ruta_tool(garmin: int | None = None, actividad: str | None = None, gpx: str | None = None) -> str:
        return perfil_ruta_desde_pc(equipo, garmin, actividad, gpx)


def main() -> None:
    # stdout is the MCP channel on stdio; logs must go to stderr.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()  # TRIDATA_HR_MAX / TRIDATA_HR_REST for TRIMP
    store = DataStore()
    # unit 083: the team's tools need nothing beyond the `mcp` extra (no paid AI), so they are always there
    from .conversaciones.desde_pc import montar_equipo

    docs = carpeta_datos()  # unit 104: TRIDATA_DOCS; the medical PDF are its health/ (TRIDATA_MEDICOS_DIR is gone)
    equipo = montar_equipo(DEFAULT_DB_PATH, store=store, medicos_dir=docs / "health" if docs else None,
                           docs_dir=docs)
    build_server(store, limites_fc=lambda: historial_fc_de(DEFAULT_DB_PATH).limites, equipo=equipo).run("stdio")


if __name__ == "__main__":
    main()
