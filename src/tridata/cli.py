"""Command-line entry point.

Examples:
    # First run: backfill everything since Feb 1st, 2026
    tridata sync --since 2026-02-01

    # Daily cron run: just catch up on whatever's missing (and then close the pending days, unit 014)
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

    # The whole food history (weights, days with foods, expenditure, objective, margin, recipes) in one
    # readable file that the two imports below can load back (unit 015); offline, no Garmin credentials
    tridata export-nutricion --out exports/nutricion.md

    # Verified copy of the store (plus extra files, e.g. a food diary), keep 14
    tridata backup --extra deficit/deficit.md

    # Food log web app on this PC only (needs the `app` extra and .env values)
    tridata web

    # One-off import of deficit.md and its weight chart: first a dry run, then for real
    tridata importar-deficit --md deficit.md --html grafica_peso_deficit.html --simular
    tridata importar-deficit --md deficit.md --html grafica_peso_deficit.html

    # Load the recipes, fixed foods and pot tares of deficit.md into the Recetas tab (once)
    tridata importar-recetas --md deficit.md --simular
    tridata importar-recetas --md deficit.md

    # Give back their foods to the days imported as one total line (bug 020); the totals do not change
    tridata importar-detalle --md deficit.md --simular
    tridata importar-detalle --md deficit.md

    # Unit 031: how many times the stress-test values saved in Ajustes appear in the repo and the logs (never which);
    # unit 032: also the values of the blood test and stress test transcribed for the Asistente
    tridata comprobar-privados --registros ~/.tridata /path/to/sync.log
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from garminconnect import GarminConnectTooManyRequestsError

from .exporters import (
    CyclingMarkdownExporter, GymMarkdownExporter, JSONExporter, MarkdownExporter,
    MetricsMarkdownExporter, PMCMarkdownExporter, RunningMarkdownExporter,
    SwimmingMarkdownExporter,
)
from .garmin_client import GarminAuthError, GarminClient, GarminRateLimitError
from .metrics import compute_pmc, daily_tss
from .nutricion.ajustes import historial_fc_de
from .storage import DEFAULT_DB_PATH, DataStore
from .sync import (
    ALL_SPORT_TYPES, CYCLING_TYPES, GYM_TYPES, RUNNING_TYPES, SWIMMING_TYPES, SyncEnMarcha, SyncService, cerrojo_sync,
    desde_para_actualizar,
)

DEFAULT_BACKUP_DIR = DEFAULT_DB_PATH.parent / "backups"
PRIVADOS_ACEPTADOS = DEFAULT_DB_PATH.parent / "privados-aceptados"  # bug 037: leaks the user accepted, local

EXPORTERS = {
    "json": JSONExporter,
    "markdown": MarkdownExporter,
}

# -- unit 018: sync.log stays clean (R-60, Q-3): a failure is one line, no trace, no e-mail, no response body --
LARGO_LOG = 200
CODIGO_LIMITE = 75  # `tridata sync` exits with this on a 429: daily_update.sh then waits longer (G-45)
CODIGO_EN_MARCHA = 76  # unit 030 (G-48): another sync (the app's button) holds the lock; daily_update.sh retries
EN_MARCHA_LOG = "sync: ya hay una sincronización con Garmin en marcha (el botón de la app); no se lanza otra"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def _ahora() -> datetime:
    """The clock of the sync record (a test puts its own)."""
    return datetime.now()


def limpiar_para_log(texto: str) -> str:
    """One line for sync.log: no e-mail (it says `<email>`), nothing from the first «{» or «[» on (Garmin's
    response bodies, which carry health data) and at most `LARGO_LOG` characters."""
    email = os.environ.get("GARMIN_EMAIL")
    if email:
        texto = texto.replace(email, "<email>")
    texto = _EMAIL.sub("<email>", texto)
    corte = min((i for i in (texto.find("{"), texto.find("[")) if i >= 0), default=-1)
    if corte >= 0:
        texto = texto[:corte].rstrip() + " …"
    texto = " ".join(texto.split())
    return texto if len(texto) <= LARGO_LOG else texto[:LARGO_LOG - 1] + "…"


class _LogLimpio(logging.Filter):
    """On the handlers of `main()`: drops traces (`exc_info`, `stack_info`) and cleans every message, also the
    `logger.exception` of garminconnect and garth, which add the response body."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = limpiar_para_log(record.getMessage())
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def _motivo_del_fallo(exc: BaseException) -> tuple[str, int]:
    """`(motivo, exit code)` of a failed sync: sesion/1, limite/75 (a 429) or conexion/1 (anything else)."""
    if isinstance(exc, (GarminRateLimitError, GarminConnectTooManyRequestsError)):
        return "limite", CODIGO_LIMITE
    if isinstance(exc, GarminAuthError):
        return "sesion", 1
    return "conexion", 1


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

    nutricion_cmd = sub.add_parser(
        "export-nutricion",
        help="Rebuild nutricion.md: weights, every day eaten, expenditure, objective, margin and recipes",
    )
    nutricion_cmd.add_argument("--out", type=Path, default=Path("exports/nutricion.md"))

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

    importar_cmd = sub.add_parser(
        "importar-deficit",
        help="Import the weights and day foods of deficit.md once, reviewing the dubious ones first",
    )
    importar_cmd.add_argument("--md", type=Path, required=True, help="The deficit.md file.")
    importar_cmd.add_argument(
        "--html", type=Path, default=None,
        help="The weight chart (grafica_peso_deficit.html), only to cross-check the weights.",
    )
    importar_cmd.add_argument(
        "--simular", action="store_true", default=False,
        help="Dry run: show what would be imported and the dubious items, write nothing.",
    )

    recetas_cmd = sub.add_parser(
        "importar-recetas",
        help="Load the recipes, fixed foods and pot tares of deficit.md into the Recetas tab (what is there wins)",
    )
    recetas_cmd.add_argument("--md", type=Path, required=True, help="The deficit.md file.")
    recetas_cmd.add_argument(
        "--simular", action="store_true", default=False,
        help="Dry run: show what would be loaded, write nothing.",
    )

    detalle_cmd = sub.add_parser(
        "importar-detalle",
        help="Put the foods of deficit.md into the days imported as one total line (the totals do not change)",
    )
    detalle_cmd.add_argument("--md", type=Path, required=True, help="The deficit.md file.")
    detalle_cmd.add_argument(
        "--simular", action="store_true", default=False,
        help="Dry run: show what each day would get, write nothing.",
    )

    privados_cmd = sub.add_parser(
        "comprobar-privados",
        help="Count how many times the stress-test values saved in Ajustes appear in the repo and the logs",
    )
    privados_cmd.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[2],
        help="The git repository to look in (default: this checkout).",
    )
    privados_cmd.add_argument(
        "--registros", type=Path, nargs="*", default=None, metavar="PATH",
        help="Log files, or folders whose *.log are read (default: ~/.tridata).",
    )

    return parser


def _cmd_comprobar_privados(repo: Path, registros: list[Path] | None) -> int:
    """Unit 031 (R9): only counts and places; exit 1 if any value appears."""
    from . import privados

    donde: Counter = Counter()
    try:
        resultado = privados.comprobar(DEFAULT_DB_PATH, repo, registros or [DEFAULT_DB_PATH.parent], donde=donde,
                                       aceptados=privados.aceptados_de(PRIVADOS_ACEPTADOS))
    except RuntimeError as exc:
        print(f"comprobar-privados: {exc}", file=sys.stderr)
        return 2
    print(privados.informe(resultado, donde))
    return 1 if resultado["repo"] or resultado["registros"] else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for handler in logging.getLogger().handlers:  # unit 018: sync.log never gets a trace nor a response body
        if not any(isinstance(f, _LogLimpio) for f in handler.filters):
            handler.addFilter(_LogLimpio())
    load_dotenv()

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "importar-deficit":
        # Before opening the store: a dry run must not touch the database at all.
        return _cmd_importar(args.md, args.html, args.simular)
    if args.command == "importar-recetas":
        return _cmd_importar_recetas(args.md, args.simular)
    if args.command == "importar-detalle":
        return _cmd_importar_detalle(args.md, args.simular, db_path=DEFAULT_DB_PATH, backup_dir=DEFAULT_BACKUP_DIR)
    if args.command == "export-nutricion":
        return _cmd_export_nutricion(args.out)
    if args.command == "comprobar-privados":
        return _cmd_comprobar_privados(args.repo, args.registros)

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
        normal = not args.laps and not args.weather  # only the normal sync is written down (R-90)
        try:
            # unit 030 (G-48): the same lock as the app's «Actualizar Garmin», next to the database; nothing of
            # Garmin is built while the other one runs
            with cerrojo_sync(DEFAULT_DB_PATH.parent):
                svc = SyncService(GarminClient(email, password), store)
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
        except SyncEnMarcha:  # unit 030: not a failure of Garmin, nothing is written down; one line in sync.log
            print(EN_MARCHA_LOG, file=sys.stderr)
            return CODIGO_EN_MARCHA
        except Exception as exc:  # noqa: BLE001 - unit 018: one line with the motive, what was saved stays (G-2)
            motivo, codigo = _motivo_del_fallo(exc)
            print(f"sync FAILED: {motivo} ({type(exc).__name__}: {limpiar_para_log(str(exc))})", file=sys.stderr)
            if normal:
                store.marcar_sync_fallo(_ahora(), motivo)
            return codigo
        if normal:
            store.marcar_sync_ok(_ahora())
            print("Sync complete.")
            _cerrar_dias(store)
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
        # unit 031 (G-49): each day with the HR confirmed in Ajustes for it (else .env), as Fitness, Hoy and the MCP
        tss_series = daily_tss(activities, historial_fc_de(DEFAULT_DB_PATH).limites)
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


def _cerrar_dias(store: DataStore) -> None:
    """Unit 014: after a good `tridata sync`, close the pending days (food, Garmin, corrections, calibration).

    It only reads what the sync just brought. A failure here is printed and never changes the exit code of
    the sync: the daily routine goes on exporting. Only counts are printed (this goes to sync.log): no kcal.
    """
    try:
        from .nutricion import Cierres

        r = Cierres(DEFAULT_DB_PATH, store=store).al_dia(date.today())
    except Exception as exc:  # noqa: BLE001 - said, and the sync still ends well
        print(f"Cierre del día: no se ha podido poner al día ({type(exc).__name__}: {exc}).", file=sys.stderr)
        return
    cal = r["calibracion"]
    calibrado = ("sin recalibrar" if cal is None else "calibración descartada (fuera de rango)"
                 if cal["descartada_motivo"] else "kcal/paso recalibradas")
    print(f"Cierre del día: {r['comidas_cerradas']} con la comida cerrada, {r['cerrados_garmin']} cerrados con "
          f"Garmin, {r['esperan']} esperan a Garmin, {r['correcciones']} correcciones; {calibrado}.")


def _cmd_export_nutricion(out: Path, *, db_path: Path | None = None, hoy: date | None = None) -> int:
    """Unit 015: rewrite nutricion.md whole from the base. Offline: it never asks for Garmin credentials (G-9).

    It prints only the path and the counts (it goes to sync.log): never a kcal or a weight (R-63).
    """
    from .nutricion import historial_md

    db_path = db_path or DEFAULT_DB_PATH
    historial = historial_md.componer(db_path, DataStore(db_path), hoy or date.today())
    ruta = historial_md.escribir(historial.texto, out)
    print(f"Wrote {ruta} · {historial.dias} días, {historial.pesos} pesos, {historial.recetas} recetas")
    return 0


def _cmd_backup(store: DataStore, dest_root: Path, keep: int, extras: list[Path]) -> int:
    missing = [p for p in extras if not p.is_file()]
    if missing:
        print(f"--extra file(s) not found: {', '.join(map(str, missing))}", file=sys.stderr)
        return 1

    # Timestamped folders sort chronologically, which rotation relies on. A second backup in the same
    # second gets «-001», «-002»…: "T080000" < "T080000-001" < "T080001", so the order still holds (unit 028).
    dest_root.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    dest = dest_root / sello
    for n in range(1, 1000):
        try:
            dest.mkdir()
            break
        except FileExistsError:
            dest = dest_root / f"{sello}-{n:03d}"
    else:
        print(f"Too many backups in the same second under {dest_root}.", file=sys.stderr)
        return 1
    dest_root.chmod(0o700)
    dest.chmod(0o700)
    if isinstance(store, _SoloCopia) and not store.ruta.is_file():
        # unit 028 (R2): nothing to lose, and never an empty SQLite that looks like a good copy
        counts: dict[str, int] = {}
        print(f"No hay base que copiar en {store.ruta}: la copia solo lleva los ficheros de la importación.")
    else:
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


class _SoloCopia(DataStore):
    """The store, opened only to copy it: unlike `DataStore()`, it creates no table.

    So a failed backup before an import leaves the database exactly as it was; and if there is no
    database, `_cmd_backup` copies nothing of it instead of creating an empty one (unit 028).
    """

    def __init__(self, db_path: Path) -> None:  # on purpose: no super().__init__, no schema
        self._db_path = Path(db_path)

    @property
    def ruta(self) -> Path:
        return self._db_path


def _leer_fichero(ruta: Path, opcion: str) -> bytes | None:
    if not ruta.is_file():
        print(f"No encuentro el fichero de {opcion}: {ruta}", file=sys.stderr)
        return None
    return ruta.read_bytes()


def _revisar(dudosos: list, preguntar: Callable[[str], str]) -> dict[int, tuple[str, float | None]]:
    """Ask the dubious items one by one: Intro accepts the proposal, a number corrects it, «o» omits it."""
    from .nutricion import importar_deficit as imp

    decisiones: dict[int, tuple[str, float | None]] = {}
    for i, dudoso in enumerate(dudosos, 1):
        print(f"\nDudoso {i}/{len(dudosos)} · {dudoso.apartado}" + (f" · {dudoso.dia}" if dudoso.dia else ""))
        print(f"  Texto: «{dudoso.texto}»")
        print(f"  Motivo: {dudoso.motivo}")
        if not dudoso.corregible:
            opciones = "solo se puede omitir: «o» para omitir (apúntalo a mano en la app si hace falta), sin Intro ni número"
        elif dudoso.propuesta is not None:
            print(f"  Propuesta: {imp.numero(dudoso.propuesta)}")
            opciones = "Intro acepta la propuesta · un número la corrige · «o» lo omite"
        else:
            opciones = "sin propuesta: escribe un número para corregirlo o «o» para omitirlo (Intro no vale)"
        while True:
            respuesta = preguntar(f"  [{opciones}] > ").strip()
            if respuesta.lower() == "o":
                decisiones[i - 1] = (imp.OMITIDO, None)
                break
            if not dudoso.corregible:
                print("  Este solo se puede omitir: escribe «o».")
                continue
            if respuesta == "":
                if dudoso.propuesta is None:
                    print("  No hay propuesta: escribe un número o «o».")
                    continue
                decisiones[i - 1] = (imp.ACEPTADO, dudoso.propuesta)
                break
            texto = respuesta.replace(",", ".") if dudoso.tipo == "peso" else respuesta.replace(".", "")
            try:
                valor = float(texto)
            except ValueError:
                print("  No es un número: Intro, un número o «o».")
                continue
            if not imp.valor_valido(dudoso, valor):
                print("  Ese número no es razonable (kcal del día entre 500 y 8.000; peso entre 30 y 250 kg).")
                continue
            decisiones[i - 1] = (imp.CORREGIDO, valor)
            break
    return decisiones


def _cmd_importar(
    md: Path, html: Path | None, simular: bool, *, db_path: Path = DEFAULT_DB_PATH,
    backup_dir: Path = DEFAULT_BACKUP_DIR, preguntar: Callable[[str], str] = input, hoy: date | None = None,
) -> int:
    """`tridata importar-deficit`: read → summary and dubious items → (dry run ends) → previous import
    warning → review → «¿Guardar?» → backup → write what is missing → import record → final summary.

    Everything it prints goes to the terminal only (never to sync.log): it is Andrés's health data.
    """
    from .nutricion import importar_deficit as imp

    hoy = hoy or date.today()
    md_bytes = _leer_fichero(md, "--md")
    html_bytes = _leer_fichero(html, "--html") if html is not None else None
    if md_bytes is None or (html is not None and html_bytes is None):
        return 1
    try:
        lectura = imp.leer(md_bytes.decode("utf-8"), html_bytes.decode("utf-8") if html_bytes is not None else None,
                           hoy)
    except imp.ErrorImportacion as exc:
        print(f"No se puede importar: {exc}.", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        print("No se puede importar: los ficheros tienen que estar en UTF-8.", file=sys.stderr)
        return 1

    print(lectura.resumen())
    if lectura.referencias:
        print(f"  Recetas o alimentos de referencia (para guardar-receta): {', '.join(lectura.referencias)}")
    if lectura.taras:
        print(f"  Taras (para guardar-receta): {', '.join(lectura.taras)}")
    if html is None:
        print("  Sin --html: los pesos no se cruzan con la gráfica.")
    for i, dudoso in enumerate(lectura.dudosos, 1):
        propuesta = f" · propuesta: {imp.numero(dudoso.propuesta)}" if dudoso.propuesta is not None else ""
        print(f"  Dudoso {i}: {dudoso.apartado} · «{dudoso.texto}» · {dudoso.motivo}{propuesta}")
    if simular:
        print("Simulación: no se ha guardado nada.")
        return 0

    try:
        anterior = imp.Importaciones(db_path).ultima()
        if anterior is not None:
            previo = anterior["resumen"].get("resumen", {})
            n_pesos = previo.get("pesos_nuevos", 0) + previo.get("pesos_ya", 0)
            n_dias = previo.get("dias_nuevos", 0) + previo.get("dias_ya", 0)
            aviso = (f"Ya importaste el {anterior['fecha'][:10]} ({n_pesos} pesos, {n_dias} días). "
                     "¿Importar otra vez? Solo se añadirá lo que falte [s/N] ")
            if preguntar(aviso).strip().lower() != "s":
                print("No se ha guardado nada.")
                return 0
        decisiones = _revisar(lectura.dudosos, preguntar)
        if preguntar("\n¿Guardar? [s/N] ").strip().lower() != "s":
            print("No se ha guardado nada.")
            return 0
    except (EOFError, KeyboardInterrupt):
        print("\nNo se ha guardado nada.")
        return 1

    extras = [md] + ([html] if html is not None else [])
    try:
        codigo = _cmd_backup(_SoloCopia(db_path), backup_dir, 14, extras)
    except Exception as exc:  # any failure of the copy stops the import before writing
        print(f"La copia de seguridad ha fallado ({exc}): no se ha guardado nada.", file=sys.stderr)
        return 1
    if codigo != 0:
        print("La copia de seguridad ha fallado: no se ha guardado nada.", file=sys.stderr)
        return 1

    dudosos = []
    for i, dudoso in enumerate(lectura.dudosos):
        decision, valor = decisiones.get(i, (imp.OMITIDO, None))
        dudosos.append({**dudoso.como_dict(), "decision": decision, "valor": valor})
    registro = {"referencias": lectura.referencias, "taras": lectura.taras, "dudosos": dudosos}

    # unit 028 (R4): each day and each weight is saved on its own, so a failure halfway keeps what was
    # saved; the counters say where it stopped, and the import is recorded as partial
    avance = _Avance()
    try:
        resumen = imp.aplicar(lectura, decisiones, _comidas_contadas(db_path, avance), _pesos_contados(db_path, avance),
                              hoy)
    except Exception as exc:  # any failure while saving: say where it stopped, never a traceback
        parcial = {"pesos_nuevos": avance.pesos, "dias_nuevos": avance.dias, "parcial": True,
                   "cortado_en": avance.dia}
        try:
            imp.Importaciones(db_path).registrar(imp.huella(md_bytes, html_bytes), {"resumen": parcial, **registro})
            registrada = "queda registrada como parcial"
        except Exception as exc_registro:  # noqa: BLE001 - the store itself may be what failed
            registrada = f"y no se ha podido registrar ({exc_registro})"
        print(f"La importación se ha cortado al guardar el {avance.dia} ({exc}): llegó a guardar "
              f"{avance.pesos} pesos y {avance.dias} días, {registrada}. Repítela: solo añadirá lo que falte.",
              file=sys.stderr)
        return 1
    imp.Importaciones(db_path).registrar(imp.huella(md_bytes, html_bytes),
                                         {"resumen": resumen.como_dict(), **registro})
    print(resumen.texto())
    return 0


class _Avance:
    """How far `imp.aplicar` got: the day it was saving and how many weights and days it saved (unit 028)."""

    def __init__(self) -> None:
        self.dia: str | None = None
        self.pesos = 0
        self.dias = 0


def _comidas_contadas(db_path: Path, avance: _Avance) -> Any:
    """A `RegistroComidas` that writes down in `avance` each day it saves (imported lazily, like the rest)."""
    from .nutricion import RegistroComidas

    class ComidasContadas(RegistroComidas):
        def importar_dia(self, dia: date, alimentos: list, hoy: date) -> int:
            avance.dia = dia.isoformat()
            hechas = super().importar_dia(dia, alimentos, hoy)
            if hechas:
                avance.dias += 1
            return hechas

    return ComidasContadas(db_path)


def _pesos_contados(db_path: Path, avance: _Avance) -> Any:
    """A `RegistroPeso` that writes down in `avance` each weight it saves."""
    from .nutricion import RegistroPeso

    class PesosContados(RegistroPeso):
        def apuntar(self, dia: Any, kg: Any, nota: str | None = None, *, hoy: date | None = None) -> dict:
            avance.dia = dia.isoformat() if isinstance(dia, date) else str(dia)
            guardado = super().apuntar(dia, kg, nota, hoy=hoy)
            avance.pesos += 1
            return guardado

    return PesosContados(db_path)


def _cmd_importar_recetas(md: Path, simular: bool, *, db_path: Path = DEFAULT_DB_PATH,
                          backup_dir: Path = DEFAULT_BACKUP_DIR, hoy: date | None = None) -> int:
    """`tridata importar-recetas` (unit 011, D7): read the two tables of deficit.md → what each row
    becomes and whether it is already there → (dry run ends) → backup → add what is missing.

    Everything it prints goes to the terminal only (never to sync.log).
    """
    from .nutricion import importar_deficit as imp
    from .nutricion.recetas import Recetario, leer_cargas

    hoy = hoy or date.today()
    md_bytes = _leer_fichero(md, "--md")
    if md_bytes is None:
        return 1
    try:
        filas = imp.filas_de_recetas(md_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        print("No se puede importar: el fichero tiene que estar en UTF-8.", file=sys.stderr)
        return 1
    cargas = leer_cargas(filas.referencias, filas.taras)
    if not cargas:
        print("No hay recetas, alimentos fijos ni taras en ese fichero: no se ha guardado nada.")
        return 0

    def imprimir(resultado: list) -> None:
        for carga, estado in resultado:
            print(f"  {carga.describir()} · {estado}")

    if simular:
        # unit 028 (R1): `Recetario()` creates and migrates tables, so it only ever opens a throwaway copy;
        # the real store is opened read-only, to copy it, and its bytes stay the same
        if db_path.is_file():
            with tempfile.TemporaryDirectory(prefix="tridata-simular-") as carpeta:
                copia = Path(carpeta) / "data.sqlite3"
                origen = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
                destino = sqlite3.connect(copia)
                try:
                    origen.backup(destino)
                finally:
                    destino.close()
                    origen.close()
                imprimir(Recetario(copia).cargar_de_deficit(cargas, hoy, simular=True))
        else:
            imprimir([(c, "nueva") for c in cargas])
        print("Simulación: no se ha guardado nada.")
        return 0

    try:
        codigo = _cmd_backup(_SoloCopia(db_path), backup_dir, 14, [md])
    except Exception as exc:  # any failure of the copy stops the load before writing
        print(f"La copia de seguridad ha fallado ({exc}): no se ha guardado nada.", file=sys.stderr)
        return 1
    if codigo != 0:
        print("La copia de seguridad ha fallado: no se ha guardado nada.", file=sys.stderr)
        return 1
    resultado = Recetario(db_path).cargar_de_deficit(cargas, hoy)
    imprimir(resultado)
    nuevas = sum(estado == "nueva" for _, estado in resultado)
    print(f"Guardado: {nuevas} nuevas · {sum(estado == 'ya estaba' for _, estado in resultado)} ya estaban "
          "(no se tocan). Lo que está «por revisar» no se usa al dictar hasta que lo abras y lo guardes.")
    return 0


def _cmd_importar_detalle(md: Path, simular: bool, *, db_path: Path = DEFAULT_DB_PATH,
                          backup_dir: Path = DEFAULT_BACKUP_DIR,
                          preguntar: Callable[[str], str] | None = None) -> int:
    """`tridata importar-detalle` (bug 020): read the «Registro diario» → what happens to each day and its new
    lines → (dry run ends) → «¿Guardar?» → backup → replace day by day → summary.

    Only the days whose lines are all the imported total change, and each one keeps its total. Everything it
    prints goes to the terminal only (never to sync.log): it is Andrés's health data.
    """
    from .nutricion import RegistroComidas
    from .nutricion import importar_deficit as imp

    preguntar = preguntar or input
    md_bytes = _leer_fichero(md, "--md")
    if md_bytes is None:
        return 1
    if not db_path.is_file():
        print(f"No hay base de datos en {db_path}: no hay días importados que completar.", file=sys.stderr)
        return 1
    try:
        plan = imp.planear_detalle(md_bytes.decode("utf-8"), imp.lineas_de_la_base(db_path),
                                   imp.decididos(imp.Importaciones(db_path).ultima()))
    except imp.ErrorImportacion as exc:
        print(f"No se puede completar el detalle: {exc}.", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        print("No se puede completar el detalle: el fichero tiene que estar en UTF-8.", file=sys.stderr)
        return 1

    for dia in plan.dias:
        print(dia.texto())
    print(plan.resumen())
    if simular:
        print("Simulación: no se ha guardado nada.")
        return 0
    if not plan.cambian():
        print("No hay ningún día que cambiar: no se ha guardado nada.")
        return 0
    try:
        if preguntar("¿Guardar? [s/N] ").strip().lower() != "s":
            print("No se ha guardado nada.")
            return 0
    except (EOFError, KeyboardInterrupt):
        print("\nNo se ha guardado nada.")
        return 1

    try:
        codigo = _cmd_backup(_SoloCopia(db_path), backup_dir, 14, [md])
    except Exception as exc:  # any failure of the copy stops before writing
        print(f"La copia de seguridad ha fallado ({exc}): no se ha guardado nada.", file=sys.stderr)
        return 1
    if codigo != 0:
        print("La copia de seguridad ha fallado: no se ha guardado nada.", file=sys.stderr)
        return 1

    registro = RegistroComidas(db_path)
    for dia in plan.cambian():
        try:
            hecho = registro.sustituir_total_importado(dia.dia, dia.lineas, dia.total or 0, imp.TOTAL_IMPORTADO)
        except ValueError as exc:
            print(f"  {dia.dia}: {exc}", file=sys.stderr)
            hecho = False
        if not hecho:
            dia.resultado = imp.CAMBIADO_ENTRETANTO
            print(dia.texto())
    print(plan.resumen())
    return 0


def _actualizador_garmin(store: DataStore, entorno: Mapping[str, str] | None = None) -> Callable[[date], None] | None:
    """The «Actualizar Garmin» button: the same sync as `tridata sync`, from the day of the last good sync to
    `dia` (unit 018, R-90: at most 45 days), and always at least yesterday and today (unit 030, R-100).

    `revalidate_days` covers every one of those days, so they are fetched again even if they have a row.
    Nothing talks to Garmin until it is called. None without GARMIN_EMAIL / GARMIN_PASSWORD (the button says so).
    """
    entorno = os.environ if entorno is None else entorno
    email, password = entorno.get("GARMIN_EMAIL"), entorno.get("GARMIN_PASSWORD")
    if not email or not password:
        return None

    def actualizar(dia: date) -> None:
        desde = min(dia - timedelta(days=1), desde_para_actualizar(store.estado_sync(), dia))
        SyncService(GarminClient(email, password), store).sync(since=desde, until=dia,
                                                               revalidate_days=(dia - desde).days + 1)

    return actualizar


def _cmd_web(host: str, port: int, db_path: Path = DEFAULT_DB_PATH) -> int:
    try:
        import uvicorn

        from .ia import InterpreteClaude, TranscriptorWhisper
        from .ia.conversacion import ConversadorClaude
        from .nutricion import Ajustes, ObjetivosDia, Recetario, RegistroComidas, RegistroPeso
        from .nutricion.asistente import nombres_apuntados, texto_acciones
        from .web import crear_app
    except ImportError as exc:
        print(f"Missing the `app` extra ({exc}). Install it: pip install -e '.[app]'", file=sys.stderr)
        return 1
    # First start: basal and kcal per step are copied ONCE from .env as settings "since always";
    # from then on they are changed in the Ajustes page and .env no longer decides any day.
    ajustes = Ajustes(db_path)
    try:
        ajustes.sembrar_desde_entorno()
        ajustes.vigentes(date.today()).config_gasto()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    store = DataStore(db_path)
    # Dictation (unit 006): Whisper is the optional `voz` extra. Without it the app starts the same
    # and writing works; with it the model loads in the background once the server is up.
    transcriptor = None
    if TranscriptorWhisper.disponible():
        transcriptor = TranscriptorWhisper()
        print(f"Dictation: loading Whisper {transcriptor.modelo} on the GPU in the background.")
    else:
        print("Aviso: dictado desactivado: instala el extra voz (pip install -e '.[voz]').", file=sys.stderr)
    # Unit 011: Claude gets the NAMES and aliases of the saved recipes and fixed foods, read on every call.
    # Unit 021: and the NAMES logged on the day of the request and the fixed list of the assistant's actions.
    recetario = Recetario(db_path)
    registro = RegistroComidas(db_path)
    interprete = InterpreteClaude(guardados=recetario.nombres)
    interprete.apuntado, interprete.acciones = nombres_apuntados(registro), texto_acciones
    # Unit 032: the Asistente's conversations (Haiku 5.5 unless TRIDATA_MODELO_ASISTENTE says otherwise) and the
    # folder of the medical PDFs, read once each on this PC (TRIDATA_MEDICOS_DIR; without it, the context says so).
    medicos_dir = os.environ.get("TRIDATA_MEDICOS_DIR", "").strip()
    if not medicos_dir:
        print("Aviso: sin TRIDATA_MEDICOS_DIR en .env el asistente no verá tu analítica ni tu prueba de esfuerzo.",
              file=sys.stderr)
    app = crear_app(registro, interprete, ajustes,
                    pesos=RegistroPeso(db_path), objetivos=ObjetivosDia(db_path),
                    store=store, actualizar_garmin=_actualizador_garmin(store), transcriptor=transcriptor,
                    recetario=recetario, conversador=ConversadorClaude(),
                    medicos_dir=Path(medicos_dir).expanduser() if medicos_dir else None)
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
