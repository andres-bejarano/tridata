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
"""
from __future__ import annotations

import re
import subprocess  # noqa: S404 - only `git`, with a fixed argument list and no shell
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from .conversaciones.medicos import valores_privados
from .nutricion.ajustes import historial_fc_de

HISTORIAL = "historial de git"


def _patron(valores: set[str]) -> re.Pattern[str] | None:
    if not valores:
        return None
    return re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, valores))) + r")\b")


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


def _lineas_del_historial(repo: Path) -> Iterator[str]:
    """Only what each commit added or removed: no hashes, dates or hunk headers (they are full of numbers)."""
    for linea in _git(repo, "log", "-p", "--all", "--format=", "--no-color", "--no-ext-diff"):
        if linea[:1] in "+-" and not linea.startswith(("+++", "---")):
            yield linea


def _registros(rutas: Iterable[Path]) -> Iterator[Path]:
    for ruta in rutas:
        if ruta.is_file():
            yield ruta
        elif ruta.is_dir():
            yield from sorted(p for p in ruta.rglob("*.log") if p.is_file())


def _contar(patron: re.Pattern[str] | None, lineas: Iterable[str]) -> int:
    return 0 if patron is None else sum(len(patron.findall(linea)) for linea in lineas)


def comprobar(db_path: Path, repo: Path, registros: Iterable[Path], *, donde: Counter | None = None) -> dict[str, int]:
    """`{valores, repo, registros}`: how many values were looked for and how many times they appear. `donde`, if
    given, gets the count per place (a file of the repo, `historial de git`, or a log's file name)."""
    valores = {str(v) for v in historial_fc_de(db_path).valores_de_prueba()} | valores_privados(db_path)
    patron = _patron(valores)
    donde = Counter() if donde is None else donde
    if patron is not None:
        for nombre, lineas in _ficheros_del_repo(repo):
            donde[nombre] += _contar(patron, lineas)
        donde[HISTORIAL] += _contar(patron, _lineas_del_historial(repo))
    en_repo = sum(donde.values())
    en_registros = 0
    if patron is not None:
        for ruta in _registros(registros):
            n = _contar(patron, ruta.read_text(errors="replace").splitlines())
            donde[f"registro {ruta.name}"] += n
            en_registros += n
    return {"valores": len(valores), "repo": en_repo, "registros": en_registros}


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
    lineas += [f"  - {lugar}: {_veces(n)}" for lugar, n in sorted(donde.items()) if n]
    return "\n".join(lineas)
