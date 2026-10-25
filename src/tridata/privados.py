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
"""
from __future__ import annotations

import re
import subprocess  # noqa: S404 - only `git`, with a fixed argument list and no shell
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from .conversaciones.medicos import normalizar_nombre, valores_privados
from .nutricion.ajustes import historial_fc_de

HISTORIAL = "historial de git"


MINIMO_FC = 3  # different values of the stress test on one line: fewer is noise (bug 037, measured on 2026-10-25)


def _patron(valores: set[str]) -> re.Pattern[str] | None:
    if not valores:
        return None
    return re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, valores))) + r")\b")


class _Buscador:
    """How many appearances a line carries: 1 if it has `MINIMO_FC` different HR values of the test, plus one per
    blood-test value written next to the name of its test."""

    def __init__(self, fc: set[str], pares: set[tuple[str, str]]) -> None:
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
        return n


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
    buscador = _Buscador(fc, pares)
    prefijos = tuple(a.lower() for a in aceptados if a)
    donde = Counter() if donde is None else donde
    aceptadas = 0
    if fc or pares:
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
    if fc or pares:
        for ruta in _registros(registros):
            n = _contar(buscador, ruta.read_text(errors="replace").splitlines())
            donde[f"registro {ruta.name}"] += n
            en_registros += n
    return {"valores": len(fc) + len(pares), "repo": en_repo, "registros": en_registros, "aceptadas": aceptadas}


def _veces(n: int) -> str:
    return f"{n} aparición" if n == 1 else f"{n} apariciones"


def informe(resultado: dict[str, int], donde: Counter) -> str:
    """What the command prints: counts and places, never a value."""
    if not resultado["valores"]:
        return ("No hay valores de una prueba de esfuerzo en Ajustes ni de una analítica transcrita: "
                "nada que buscar.")
    lineas = [f"Valores privados buscados (prueba de esfuerzo y analítica): {resultado['valores']} (no se muestran).",
              f"Repo (ficheros y {HISTORIAL}): {_veces(resultado['repo'])}.",
              f"Registros: {_veces(resultado['registros'])}."]
    if resultado.get("aceptadas"):
        lineas.append(f"Aceptadas (commits de la lista local, no cuentan): {_veces(resultado['aceptadas'])}.")
    lineas += [f"  - {lugar}: {_veces(n)}" for lugar, n in sorted(donde.items()) if n]
    return "\n".join(lineas)
