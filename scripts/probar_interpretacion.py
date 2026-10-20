#!/usr/bin/env python
"""Manual test of the Claude interpretation with INVENTED dictations (unit 002 R12, unit 007).

Calls the real Claude API (costs a few cents) with the key in `.env`, so it is NOT part of
the test suite and only runs by hand:

    python scripts/probar_interpretacion.py                         # the 20-30 food dictations
    python scripts/probar_interpretacion.py --piezas                # only the 5-8 weight / new-day ones
    python scripts/probar_interpretacion.py --modelo claude-sonnet-5-5

A list counts as right when it has the expected foods, quantities, units and days (kcal are
not judged). A pieces case also needs the right weight (kg and day), objective of the day (type,
%, levels, steps, whether it has a planned training) and number of doubtful phrases. Below 9
out of 10, repeat with Sonnet and choose with both results in front.
"""
# No `from __future__ import annotations`: the tests load this file with importlib without
# registering it in sys.modules, and @dataclass then cannot resolve string annotations.
import argparse
import json
import os
import sys
import time
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


def _piezas(resultado: Any) -> dict:
    """Every piece of an interpretation as plain data (a bare list is foods only)."""
    if isinstance(resultado, list):
        return {"alimentos": [_como_dict(a) for a in resultado], "peso": None, "objetivo_dia": None, "dudosas": []}
    datos = _como_dict(resultado)
    return {"alimentos": [_como_dict(a) for a in datos.get("alimentos") or []], "peso": datos.get("peso"),
            "objetivo_dia": datos.get("objetivo_dia"), "dudosas": list(datos.get("dudosas") or [])}


def _vacio(objetivo: dict | None) -> bool:
    return not objetivo or all(v in (None, "", []) for v in objetivo.values())


def _numeros(valores: list | None) -> list[float] | None:
    return sorted(float(v) for v in valores) if valores else None


def comparar_piezas(esperado: dict, obtenido: dict) -> list[str]:
    """What is wrong with the pieces of one interpretation (empty = right). Notes and kcal are not judged."""
    errores = []
    e_peso, o_peso = esperado["peso"], obtenido.get("peso")
    if e_peso is None and o_peso is not None:
        errores.append(f"se inventa un peso {o_peso.get('kg')}")
    elif e_peso is not None and o_peso is None:
        errores.append(f"falta el peso {e_peso['kg']}")
    elif e_peso is not None:
        if abs(float(o_peso["kg"]) - e_peso["kg"]) > 0.001:
            errores.append(f"peso {o_peso['kg']} en vez de {e_peso['kg']}")
        if str(o_peso["dia"]) != e_peso["dia"]:
            errores.append(f"peso del {o_peso['dia']} en vez del {e_peso['dia']}")
        if e_peso.get("nota") and not o_peso.get("nota"):
            errores.append("el peso no trae su nota")
    e_obj, o_obj = esperado["objetivo_dia"], obtenido.get("objetivo_dia")
    if e_obj is None:
        if not _vacio(o_obj):
            errores.append(f"se inventa un objetivo del día {o_obj}")
    elif _vacio(o_obj):
        errores.append("falta el objetivo del día")
    else:
        if e_obj["tipo"] and not any(_normal(t) in _normal(o_obj.get("tipo")) for t in e_obj["tipo"]):
            errores.append(f"tipo de día {o_obj.get('tipo')!r} en vez de {e_obj['tipo'][0]!r}")
        if not e_obj["tipo"] and o_obj.get("tipo"):
            errores.append(f"se inventa el tipo de día {o_obj.get('tipo')!r}")
        for clave in ("porcentaje", "pasos"):
            if (o_obj.get(clave) is None) != (e_obj[clave] is None) or (
                    e_obj[clave] is not None and abs(float(o_obj[clave]) - e_obj[clave]) > 0.001):
                errores.append(f"{clave} {o_obj.get(clave)} en vez de {e_obj[clave]}")
        if _numeros(o_obj.get("niveles")) != _numeros(e_obj["niveles"]):
            errores.append(f"niveles {o_obj.get('niveles')} en vez de {e_obj['niveles']}")
        if bool(o_obj.get("entreno")) != bool(e_obj["entreno"]):
            errores.append(f"entreno previsto {o_obj.get('entreno')!r}: {'faltaba' if e_obj['entreno'] else 'sobra'}")
    if len(obtenido.get("dudosas") or []) != esperado["dudosas"]:
        errores.append(f"{len(obtenido.get('dudosas') or [])} frases dudosas en vez de {esperado['dudosas']}: "
                       f"{obtenido.get('dudosas')}")
    errores += comparar(esperado["alimentos"], obtenido.get("alimentos") or [])
    return errores


def evaluar_piezas(interprete: Any, datos: dict) -> Resultado:
    """Only the weight / new-day cases (`piezas`), with the time each answer took (Q-5: ≤ 5 s)."""
    from tridata.ia import ErrorInterpretacion

    hoy = date.fromisoformat(datos["hoy"])
    resultado = Resultado()
    for caso in datos["piezas"]:
        inicio = time.monotonic()
        try:
            errores = comparar_piezas(caso["esperado"], _piezas(interprete.interpretar(caso["texto"], hoy)))
        except ErrorInterpretacion as exc:
            errores = [f"sin interpretación: {exc}"]
        segundos = time.monotonic() - inicio
        resultado.total += 1
        resultado.aciertos += not errores
        resultado.detalles.append((f"{caso['texto']}  [{segundos:.1f} s]", errores))
    return resultado


def evaluar(interprete: Any, datos: dict) -> Resultado:
    from tridata.ia import ErrorInterpretacion

    hoy = date.fromisoformat(datos["hoy"])
    resultado = Resultado()
    for caso in datos["casos"]:
        try:
            obtenidos = _piezas(interprete.interpretar(caso["texto"], hoy))["alimentos"]
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
    parser.add_argument("--piezas", action="store_true",
                        help="only the 5-8 weight / new-day cases (unit 007), with the time of each answer")
    args = parser.parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Falta ANTHROPIC_API_KEY en .env: esta prueba llama a la API de verdad.", file=sys.stderr)
        return 1

    datos = json.loads(args.casos.read_text(encoding="utf-8"))
    resultado = (evaluar_piezas if args.piezas else evaluar)(InterpreteClaude(modelo=args.modelo), datos)
    for texto, errores in resultado.detalles:
        print(f"{'OK   ' if not errores else 'FALLO'}  {texto}")
        for error in errores:
            print(f"         - {error}")
    proporcion = resultado.aciertos / resultado.total if resultado.total else 0.0
    que = "interpretaciones con todas sus piezas" if args.piezas else "listas"
    print(f"\nModelo {args.modelo}: {resultado.aciertos}/{resultado.total} {que} bien ({proporcion:.0%}).")
    if proporcion < UMBRAL and args.modelo != MODELO_ALTERNATIVO:
        print(f"Menos de 9 de cada 10: repite con --modelo {MODELO_ALTERNATIVO} y elige con los dos resultados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
