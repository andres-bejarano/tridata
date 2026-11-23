#!/usr/bin/env python3
"""Personal files never enter the repo, which is public (unit 105).

    ficheros_personales.py RUTA...   checks those paths
    ficheros_personales.py --staged  checks what is staged for the next commit (the pre-commit hook)
    ficheros_personales.py           checks every versioned file (`scripts/ci/lint`)

Rejected (case does not matter): PDFs; GPX tracks, except invented ones in `tests/fixtures/`
(«inventad» or «sintetic» in the name); anything inside `inscripciones/` or `inscriptions/`;
names starting with «deficit» (same fixture exception); names containing «.env» or starting
with «env», except exactly `.env.example`. Exits 1 naming each file and why.
Standard library only, and runs on Python 3.8 (the system `python3`).
"""
from __future__ import annotations

import subprocess
import sys

CARPETAS_VETADAS = ("inscripciones", "inscriptions")
MARCAS_INVENTADO = ("inventad", "sintetic", "sintétic")


def _fixture_inventado(partes: list[str], nombre: str) -> bool:
    dentro = len(partes) >= 3 and partes[0] == "tests" and partes[1] == "fixtures"
    return dentro and any(marca in nombre for marca in MARCAS_INVENTADO)


def motivo(ruta: str) -> str | None:
    """Why this path cannot be versioned, or None if it can."""
    partes = ruta.replace("\\", "/").lower().split("/")
    nombre = partes[-1]
    if any(parte in CARPETAS_VETADAS for parte in partes[:-1]):
        return "papeles de inscripciones (carpeta inscripciones/)"
    if nombre.endswith(".pdf"):
        return "PDF (analíticas, inscripciones y otros papeles personales)"
    if nombre.endswith(".gpx") and not _fixture_inventado(partes, nombre):
        return "GPX real (solo valen los inventados de tests/fixtures/)"
    if nombre.startswith("deficit") and not _fixture_inventado(partes, nombre):
        return "diario de déficit personal"
    if nombre != ".env.example" and (".env" in nombre or nombre.startswith("env")):
        return "fichero de entorno (.env: credenciales)"
    return None


def _git_nombres(*args: str) -> list[str]:
    salida = subprocess.run(["git", *args, "-z"], capture_output=True, check=True).stdout  # noqa: S603, S607
    return [n for n in salida.decode("utf-8", "surrogateescape").split("\0") if n]


def main(argv: list[str]) -> int:
    if argv == ["--staged"]:
        rutas = _git_nombres("diff", "--cached", "--name-only", "--diff-filter=ACMR")
        donde = "el commit"
    elif argv:
        rutas = argv
        donde = "la lista"
    else:
        rutas = _git_nombres("ls-files")
        donde = "el repositorio"
    vetados = [(ruta, m) for ruta in rutas for m in [motivo(ruta)] if m]
    if not vetados:
        return 0
    print(f"ficheros_personales: {donde} lleva ficheros personales y el repo es público:", file=sys.stderr)
    for ruta, m in vetados:
        print(f"  {ruta}: {m}", file=sys.stderr)
    print("  Sácalos (git rm --cached <fichero>) y, si deben quedarse en tu máquina, al .gitignore.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
