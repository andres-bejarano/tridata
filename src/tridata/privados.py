"""`tridata comprobar-privados` (unit 031, R-110, C-151, G-49): the values of the stress test never leave this PC.

It reads from the local database the HR max, HR rest and zone limits saved while a stress test was their origin
(`HistorialFC.valores_de_prueba`) and looks for each one as a whole word in the repo — the tracked files and the
lines added or removed in its whole history (`git log -p`) — and in the logs (`*.log` under the folders given, or
the files given). It says HOW MANY times they appear, and where (relative file names), and NEVER which value: a
checker that printed the values would be the leak itself.

Unit 032 (R12 · R-119, C-160, G-50): it also looks for the values of the medical transcriptions saved in the same
database (`conversaciones.medicos.valores_privados`): the blood test and the stress test the Asistente reads. Only
their distinctive numbers (with decimals, in both spellings, or of 3 digits or more): one or two whole digits appear
everywhere in a repository and would never let it say 0.

Bug 037: a number on its own is not a leak — «140» or «1.5» are everywhere in code and logs, and ~2,000 false
positives hid a real one. A line counts when it carries three or more different values of the stress test (a zone
table, a set of bounds), or a value of a blood test next to the name of its test. Commits listed in
`~/.tridata/privados-aceptados` (a leak the user accepted; one SHA or prefix per line, `#` for comments) do not
count, and the report says how many appearances were accepted.

Unit 058 (P-20261029-5bc337db): it also looks for the activities saved from Garmin and the races of the plans — the
tests of the 057 copied a real day and nothing saw it. An activity counts when a line carries its date and, on the same
line, its duration (minutes as `36'`, `36 min` or `36 * 60`, or the whole seconds) and its distance (km to one decimal,
or metres, both rounded to the tenth of a km), or its start hour if it has no distance. A race counts when a line carries
its name (without its final parenthesis, accents or case) and its date or its objective. A date with other numbers, or
a race name alone, is not a leak: invented fixtures use real-looking dates and race names are public.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess  # noqa: S404 - only `git`, with a fixed argument list and no shell
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .conversaciones.medicos import normalizar_nombre, valores_privados
from .nutricion.ajustes import historial_fc_de

HISTORIAL = "historial de git"


MINIMO_FC = 3  # different values of the stress test on one line: fewer is noise (bug 037, measured on 2026-10-25)


def _patron(valores: set[str]) -> re.Pattern[str] | None:
    if not valores:
        return None
    return re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, valores))) + r")\b")


_FECHA = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_HORA = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")
_PARENTESIS_FINAL = re.compile(r"\s*\([^)]*\)\s*$")


@dataclass(frozen=True)
class _Actividad:
    minutos: re.Pattern[str]  # `36'`, `36 min`, `36 * 60` or the whole seconds
    km: float | None  # rounded to the tenth
    hora: str | None  # `HH:MM` of its start


@dataclass(frozen=True)
class _Carrera:
    nombre: re.Pattern[str]  # over the normalized line
    fecha: str | None
    objetivo: re.Pattern[str] | None


def _actividades(db_path: Path) -> dict[str, list[_Actividad]]:
    """`{date: its activities}` from the local database, read only (nothing is created)."""
    por_dia: dict[str, list[_Actividad]] = {}
    for (payload,) in _filas(db_path, "activities", "SELECT payload FROM activities"):
        a = json.loads(payload)
        segundos = a.get("duration_seconds")
        if not a.get("activity_date") or not segundos:
            continue
        minutos, enteros = round(float(segundos) / 60), round(float(segundos))
        patron = re.compile(rf"(?<![\d.,]){minutos}\s*(?:'|′|min\b|\*\s*60\b)|(?<![\d.,]){enteros}(?![\d.,]?\d)")
        metros = a.get("distance_meters")
        inicio = str(a.get("start_time_local") or "")
        por_dia.setdefault(a["activity_date"], []).append(_Actividad(
            minutos=patron, km=round(float(metros) / 1000, 1) if metros else None,
            hora=inicio[11:16] if len(inicio) >= 16 else None))
    return por_dia


def _carreras(db_path: Path) -> list[_Carrera]:
    """The races of every plan in the local database, read only."""
    carreras = []
    for nombre, fecha, objetivo in _filas(db_path, "plan_carrera", "SELECT nombre, fecha, objetivo FROM plan_carrera"):
        nucleo = normalizar_nombre(_PARENTESIS_FINAL.sub("", str(nombre or "")))
        if not nucleo:
            continue
        meta = normalizar_nombre(str(objetivo or ""))
        carreras.append(_Carrera(nombre=re.compile(rf"(?<!\w){re.escape(nucleo)}(?!\w)"), fecha=fecha or None,
                                 objetivo=re.compile(rf"(?<![\w:]){re.escape(meta)}(?![\w:])") if meta else None))
    return carreras


def _filas(db_path: Path, tabla: str, consulta: str) -> list[tuple]:
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(f"{Path(db_path).as_uri()}?mode=ro", uri=True)
    try:
        hay = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)).fetchone()
        return conn.execute(consulta).fetchall() if hay else []
    finally:
        conn.close()


def _kms(linea: str) -> set[float]:
    """Every distance a line may carry, in km to the tenth: a number with decimals under 1000 is km, any other of 100
    or more is metres. Dates and hours are taken out first."""
    kms = set()
    for t in _NUMERO.findall(_HORA.sub(" ", _FECHA.sub(" ", linea))):
        v = float(t.replace(",", "."))
        if ("." in t or "," in t) and v < 1000:
            kms.add(round(v, 1))
        elif v >= 100:
            kms.add(round(v / 1000, 1))
    return kms


class _Buscador:
    """How many appearances a line carries: 1 if it has `MINIMO_FC` different HR values of the test, plus one per
    blood-test value written next to the name of its test, one per activity and one per race of the plan (058)."""

    def __init__(self, fc: set[str], pares: set[tuple[str, str]],
                 actividades: dict[str, list[_Actividad]] | None = None, carreras: list[_Carrera] | None = None) -> None:
        self._actividades = actividades or {}
        self._carreras = carreras or []
        self._fc = _patron(fc)
        self._numeros = _patron({n for _, n in pares})
        self._nombres: dict[str, list[re.Pattern[str]]] = {}
        for nombre, n in pares:
            self._nombres.setdefault(n, []).append(re.compile(r"\b" + re.escape(nombre) + r"\b"))

    def contar(self, linea: str) -> int:
        n = 0
        if self._fc is not None and len(set(self._fc.findall(linea))) >= MINIMO_FC:
            n += 1
        if self._numeros is not None:
            numeros = set(self._numeros.findall(linea))
            if numeros:
                normal = normalizar_nombre(linea)
                n += sum(1 for v in numeros for nombre in self._nombres[v] if nombre.search(normal))
        return n + self._de_actividades(linea) + self._de_carreras(linea)

    def _de_actividades(self, linea: str) -> int:
        dias = [d for d in set(_FECHA.findall(linea)) if d in self._actividades] if self._actividades else []
        if not dias:
            return 0
        kms = _kms(linea)
        return sum(1 for d in dias for a in self._actividades[d]
                   if a.minutos.search(linea) and (a.km in kms if a.km is not None else bool(a.hora and a.hora in linea)))

    def _de_carreras(self, linea: str) -> int:
        if not self._carreras:
            return 0
        normal = normalizar_nombre(linea)
        return sum(1 for c in self._carreras if c.nombre.search(normal) and (
            (c.fecha is not None and c.fecha in linea) or (c.objetivo is not None and c.objetivo.search(normal))))


def _git(repo: Path, *args: str) -> Iterator[str]:
    proceso = subprocess.Popen(  # noqa: S603
        ["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,  # noqa: S607
        text=True, errors="replace",
    )
    if proceso.stdout is None:  # PIPE always gives one
        raise RuntimeError("git gave no output")
    with proceso.stdout:
        yield from proceso.stdout
    if proceso.wait() != 0:
        raise RuntimeError(f"git {args[0]} failed in {repo} (is it a git repository?)")


def _ficheros_del_repo(repo: Path) -> Iterator[tuple[str, Iterable[str]]]:
    for nombre in "".join(_git(repo, "ls-files", "-z")).split("\0"):
        ruta = repo / nombre
        if nombre and ruta.is_file():
            yield nombre, ruta.read_text(errors="replace").splitlines()


def _lineas_del_historial(repo: Path) -> Iterator[tuple[str, str]]:
    """`(commit, line)` for what each commit added or removed: no hashes, dates or hunk headers (full of numbers)."""
    commit = ""
    for linea in _git(repo, "log", "-p", "--all", "--format=%x00%H", "--no-color", "--no-ext-diff"):
        if linea.startswith("\x00"):
            commit = linea[1:].strip()
        elif linea[:1] in "+-" and not linea.startswith(("+++", "---")):
            yield commit, linea


def _registros(rutas: Iterable[Path]) -> Iterator[Path]:
    for ruta in rutas:
        if ruta.is_file():
            yield ruta
        elif ruta.is_dir():
            yield from sorted(p for p in ruta.rglob("*.log") if p.is_file())


def _contar(buscador: _Buscador, lineas: Iterable[str]) -> int:
    return sum(buscador.contar(linea) for linea in lineas)


def aceptados_de(ruta: Path) -> list[str]:
    """The commits of the local list of accepted leaks: one SHA or prefix per line, `#` for comments. No file, none."""
    if not ruta.is_file():
        return []
    return [linea.split("#", 1)[0].strip() for linea in ruta.read_text().splitlines() if linea.split("#", 1)[0].strip()]


def comprobar(db_path: Path, repo: Path, registros: Iterable[Path], *, donde: Counter | None = None,
              aceptados: Iterable[str] = ()) -> dict[str, int]:
    """`{valores, repo, registros, aceptadas}`: how many values were looked for, how many times they appear and how
    many appearances are in accepted commits (not counted in `repo`). `donde`, if given, gets the count per place (a
    file of the repo, `historial de git`, or a log's file name)."""
    fc = {str(v) for v in historial_fc_de(db_path).valores_de_prueba()}
    pares = valores_privados(db_path)
    actividades, carreras = _actividades(db_path), _carreras(db_path)
    buscador = _Buscador(fc, pares, actividades, carreras)
    hay = bool(fc or pares or actividades or carreras)
    prefijos = tuple(a.lower() for a in aceptados if a)
    donde = Counter() if donde is None else donde
    aceptadas = 0
    if hay:
        for nombre, lineas in _ficheros_del_repo(repo):
            donde[nombre] += _contar(buscador, lineas)
        for commit, linea in _lineas_del_historial(repo):
            n = buscador.contar(linea)
            if n and prefijos and commit.lower().startswith(prefijos):
                aceptadas += n
            else:
                donde[HISTORIAL] += n
    en_repo = sum(donde.values())
    en_registros = 0
    if hay:
        for ruta in _registros(registros):
            n = _contar(buscador, ruta.read_text(errors="replace").splitlines())
            donde[f"registro {ruta.name}"] += n
            en_registros += n
    return {"valores": len(fc) + len(pares), "actividades": sum(map(len, actividades.values())),
            "carreras": len(carreras), "repo": en_repo, "registros": en_registros, "aceptadas": aceptadas}


def _veces(n: int) -> str:
    return f"{n} aparición" if n == 1 else f"{n} apariciones"


def informe(resultado: dict[str, int], donde: Counter) -> str:
    """What the command prints: counts and places, never a value."""
    actividades, carreras = resultado.get("actividades", 0), resultado.get("carreras", 0)
    if not (resultado["valores"] or actividades or carreras):
        return ("No hay valores de una prueba de esfuerzo en Ajustes, ni de una analítica transcrita, ni actividades, "
                "ni carreras del plan: nada que buscar.")
    lineas = [f"Valores privados buscados (prueba de esfuerzo y analítica): {resultado['valores']} (no se muestran).",
              f"Buscadas también: {actividades} {'actividad' if actividades == 1 else 'actividades'} y {carreras} "
              f"{'carrera' if carreras == 1 else 'carreras'} del plan (no se muestran).",
              f"Repo (ficheros y {HISTORIAL}): {_veces(resultado['repo'])}.",
              f"Registros: {_veces(resultado['registros'])}."]
    if resultado.get("aceptadas"):
        lineas.append(f"Aceptadas (commits de la lista local, no cuentan): {_veces(resultado['aceptadas'])}.")
    lineas += [f"  - {lugar}: {_veces(n)}" for lugar, n in sorted(donde.items()) if n]
    return "\n".join(lineas)
