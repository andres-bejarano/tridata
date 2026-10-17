#!/usr/bin/env python
"""Manual test of the Claude interpretation with 20-30 INVENTED dictations (unit 002, R12).

Calls the real Claude API (costs a few cents) with the key in `.env`, so it is NOT part of
the test suite and only runs by hand:

    python scripts/probar_interpretacion.py                         # model from .env or Haiku
    python scripts/probar_interpretacion.py --modelo claude-sonnet-5-5

A list counts as right when it has the expected foods, quantities, units and days (kcal are
not judged). Below 9 out of 10, repeat with Sonnet and choose with both results in front.
"""
# No `from __future__ import annotations`: the tests load this file with importlib without
# registering it in sys.modules, and @dataclass then cannot resolve string annotations.
import argparse
import json
import os
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

CASOS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "dictados.json"
UMBRAL = 0.9
MODELO_ALTERNATIVO = "claude-sonnet-5-5"
_UNIDADES = {
    "g": "g", "gr": "g", "gramo": "g", "gramos": "g",
    "ml": "ml", "mililitro": "ml", "mililitros": "ml",
    "unidad": "unidad", "unidades": "unidad", "ud": "unidad", "uds": "unidad", "u": "unidad",
    "pieza": "unidad", "piezas": "unidad",
}


@dataclass
class Resultado:
    aciertos: int = 0
    total: int = 0
    detalles: list[tuple[str, list[str]]] = field(default_factory=list)


def _normal(texto: str | None) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return sin_tildes.lower().strip()


def _unidad(texto: str | None) -> str | None:
    return _UNIDADES.get(_normal(texto), _normal(texto)) if texto else None


def _encaja_nombre(esperado: dict, nombre: str) -> bool:
    claves = esperado["nombre"] if isinstance(esperado["nombre"], list) else [esperado["nombre"]]
    return any(_normal(c) in _normal(nombre) for c in claves)


def comparar(esperados: list[dict], obtenidos: list[dict]) -> list[str]:
    """What is wrong with one interpreted list (empty = right). kcal are not judged."""
    errores = []
    libres = list(obtenidos)
    for e in esperados:
        nombre = e["nombre"][0] if isinstance(e["nombre"], list) else e["nombre"]
        hallado = next((o for o in libres if _encaja_nombre(e, o["nombre"])), None)
        if hallado is None:
            errores.append(f"falta «{nombre}»")
            continue
        libres.remove(hallado)
        if str(hallado["dia"]) != e["dia"]:
            errores.append(f"«{nombre}»: día {hallado['dia']} en vez de {e['dia']}")
        if bool(hallado.get("dudoso")) != bool(e.get("dudoso")):
            errores.append(f"«{nombre}»: dudoso={hallado.get('dudoso')} en vez de {bool(e.get('dudoso'))}")
        if e.get("dudoso") or e.get("cantidad_libre"):
            if e.get("cantidad_libre") and hallado["cantidad"] is None:
                errores.append(f"«{nombre}»: sin cantidad estimada")
            continue
        if e["cantidad"] is None:
            if hallado["cantidad"] is not None:
                errores.append(f"«{nombre}»: se inventa la cantidad {hallado['cantidad']}")
            continue
        if hallado["cantidad"] is None or abs(float(hallado["cantidad"]) - e["cantidad"]) > 0.01 * max(1, e["cantidad"]):
            errores.append(f"«{nombre}»: cantidad {hallado['cantidad']} en vez de {e['cantidad']}")
        if _unidad(hallado.get("unidad")) != e["unidad"]:
            errores.append(f"«{nombre}»: unidad {hallado.get('unidad')!r} en vez de {e['unidad']!r}")
    errores += [f"sobra «{o['nombre']}»" for o in libres]
    return errores


def _como_dict(alimento: Any) -> dict:
    return alimento.model_dump(mode="json") if hasattr(alimento, "model_dump") else dict(alimento)


def evaluar(interprete: Any, datos: dict) -> Resultado:
    from tridata.ia import ErrorInterpretacion

    hoy = date.fromisoformat(datos["hoy"])
    resultado = Resultado()
    for caso in datos["casos"]:
        try:
            obtenidos = [_como_dict(a) for a in interprete.interpretar(caso["texto"], hoy)]
            errores = comparar(caso["esperado"], obtenidos)
        except ErrorInterpretacion as exc:
            errores = [f"sin interpretación: {exc}"]
        resultado.total += 1
        resultado.aciertos += not errores
        resultado.detalles.append((caso["texto"], errores))
    return resultado


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    from tridata.ia import MODELO_POR_DEFECTO, InterpreteClaude

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--modelo", default=os.environ.get("TRIDATA_MODELO_TEXTO") or MODELO_POR_DEFECTO)
    parser.add_argument("--casos", type=Path, default=CASOS)
    args = parser.parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Falta ANTHROPIC_API_KEY en .env: esta prueba llama a la API de verdad.", file=sys.stderr)
        return 1

    datos = json.loads(args.casos.read_text(encoding="utf-8"))
    resultado = evaluar(InterpreteClaude(modelo=args.modelo), datos)
    for texto, errores in resultado.detalles:
        print(f"{'OK   ' if not errores else 'FALLO'}  {texto}")
        for error in errores:
            print(f"         - {error}")
    proporcion = resultado.aciertos / resultado.total if resultado.total else 0.0
    print(f"\nModelo {args.modelo}: {resultado.aciertos}/{resultado.total} listas bien ({proporcion:.0%}).")
    if proporcion < UMBRAL and args.modelo != MODELO_ALTERNATIVO:
        print(f"Menos de 9 de cada 10: repite con --modelo {MODELO_ALTERNATIVO} y elige con los dos resultados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
