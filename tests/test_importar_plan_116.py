"""Unit 116 (REC-58 of `llevar-plan`, R-358..R-362): importing a plan .md loses and duplicates nothing.

- R1 (R-358): the same race twice in the .md (same day, one name inside the other) is kept once, the one of the
  objectives with its target, and what is shown before the «sí» says the other was the same.
- R2 (R-359, load-bearing): a key session of a week said with its day («miércoles: cuestas») is a session of the plan
  that day of that week; without a day, or with a day out of the week, it stays only as the text of the week.
- R3 (R-360): if that day the proposal already has a session with that text, no other one is added.
- R4 (R-361): the incidents of a .md downloaded from the app come back alive, with their state, visits and lesson.
- R5 (R-362): the text of a section no row covers goes to the notes as it is; a section with a title and nothing
  else makes no note (its title is already the heading of its section).
- R6: a .md downloaded from the app, imported and downloaded again, is the same .md.

The week of the examples goes from Tuesday 2026-11-03 to Monday 2026-11-09: its Wednesday is the 4th and its Saturday
the 7th. Every datum is invented; nothing talks to Garmin or to a paid service.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime

import pytest

from tridata.conversaciones.desde_pc import _vista_plan, montar_equipo
from tridata.mcp_server import build_server
from tridata.plan import ABIERTA, CERRADA, Planes
from tridata.plan.importar import Apartado, confirmar, proponer_ordenado
from tridata.plan.markdown import escribir

HOY = date(2026, 10, 25)
AHORA = datetime(2026, 10, 25, 9, 0)
SEMANA = "semana|1|2026-11-03|2026-11-09|6:00|{clave}||"


@pytest.fixture
def planes(tmp_path) -> Planes:
    return Planes(tmp_path / "data.sqlite3")


def _a(titulo: str, parte: str, filas: list[str] | tuple[str, ...] = (), texto: str = "") -> Apartado:
    return Apartado(titulo, parte, tuple(filas), texto)


def _propuesta(*apartados: Apartado, planes: Planes | None = None) -> dict:
    return proponer_ordenado(list(apartados), None, HOY, "Plan inventado 116", planes=planes)


# -- R1 · R-358, C-453, C-454 · the same race twice ------------------------------------------------------------------

def _objetivos(*filas: str) -> Apartado:
    return _a("Objetivos", "objetivos", ["carrera|Maratón Valencia|2026-12-06|2:59|pendiente|sí", *filas])


def test_la_misma_carrera_en_objetivos_y_en_la_tabla_entra_una_vez_y_se_dice(planes):  # R1 · C-453
    p = _propuesta(_objetivos(), _a("Previsto", "previsto", ["carrera|Valencia|2026-12-06|||no", SEMANA.format(
        clave="rodajes")]), planes=planes)
    assert [(c["nombre"], c["fecha"], c["objetivo"]) for c in p["carreras"]] == [
        ("Maratón Valencia", "2026-12-06", "2:59")]
    assert p["carreras"][0]["principal"] is True
    juntadas = [a for a in p["avisos"] if "Valencia" in a]
    assert len(juntadas) == 1 and "«Valencia»" in juntadas[0] and "misma" in juntadas[0]
    assert "«Valencia»" in _vista_plan(p, {})  # said before the «sí»
    ident = confirmar(p, {}, planes, AHORA)
    assert [c["nombre"] for c in planes.leer(ident)["carreras"]] == ["Maratón Valencia"]


def test_la_de_objetivos_se_queda_con_el_objetivo_de_la_otra_si_le_falta(planes):  # R1
    p = _propuesta(_a("Previsto", "previsto", ["carrera|Maratón de Valencia|2026-12-06|3:00|pendiente|no"]),
                   _a("Objetivos", "objetivos", ["carrera|valencia|2026-12-06||pendiente|sí"]), planes=planes)
    assert [(c["nombre"], c["objetivo"], c["apartado"]) for c in p["carreras"]] == [("valencia", "3:00", 1)]
    assert any("«Maratón de Valencia»" in a for a in p["avisos"])


def test_sin_objetivos_se_queda_la_primera_y_sin_planes_tambien_se_avisa():  # R1
    p = _propuesta(_a("Previsto", "previsto", ["carrera|Behobia|2026-11-08|1:25|pendiente|no",
                                                "carrera|Behobia San Sebastián|2026-11-08|||no"]))
    assert [c["nombre"] for c in p["carreras"]] == ["Behobia"]
    assert len(p["avisos"]) == 1 and "«Behobia San Sebastián»" in p["avisos"][0]


def test_carreras_distintas_o_el_mismo_nombre_otro_dia_entran_todas(planes):  # R1 · C-454
    p = _propuesta(_a("Objetivos", "objetivos", ["carrera|Behobia|2026-11-08|1:25|pendiente|no"]),
                   _a("Previsto", "previsto", ["carrera|San Silvestre|2026-12-31|||no",
                                               "carrera|Behobia|2027-11-14|||no"]), planes=planes)
    assert [(c["nombre"], c["fecha"]) for c in p["carreras"]] == [
        ("Behobia", "2026-11-08"), ("San Silvestre", "2026-12-31"), ("Behobia", "2027-11-14")]
    assert p["avisos"] == []
    ident = confirmar(p, {}, planes, AHORA)
    assert len(planes.leer(ident)["carreras"]) == 3


def test_una_carrera_repetida_que_ya_esta_en_otro_plan_se_avisa_una_vez(planes):  # R1, with unit 115 (R-351)
    otro = _propuesta(_objetivos())
    confirmar(otro, {}, planes, AHORA)
    p = _propuesta(_objetivos(), _a("Previsto", "previsto", ["carrera|Valencia|2026-12-06|||no"]), planes=planes)
    assert len(p["carreras"]) == 1
    assert len([a for a in p["avisos"] if "ya está en el plan" in a]) == 1
    assert len([a for a in p["avisos"] if "misma" in a]) == 1


# -- R2, R3 · R-359, R-360, C-455..C-457 · the key sessions with their day (load-bearing) ---------------------------

def test_la_sesion_clave_con_su_dia_entra_como_sesion_de_ese_dia(planes):  # R2 (portante) · C-455
    clave = "miércoles: cuestas 10×200 m; sábado tirada 25 km"
    p = _propuesta(_a("Previsto", "previsto", [SEMANA.format(clave=clave)]))
    assert [(s["fecha"], s["titulo"], s["bloques"], s["clave"], s["apartado"]) for s in p["sesiones"]] == [
        ("2026-11-04", "cuestas 10×200 m", [], True, 0), ("2026-11-07", "tirada 25 km", [], True, 0)]
    assert p["semanas"][0]["clave"] == clave  # the text of the week does not change
    ident = confirmar(p, {}, planes, AHORA)
    plan = planes.leer(ident)
    assert [(s["fecha"], s["titulo"], s["clave"]) for s in plan["sesiones"]] == [
        ("2026-11-04", "cuestas 10×200 m", True), ("2026-11-07", "tirada 25 km", True)]
    assert plan["semanas"][0]["clave"] == clave
    # read as any other session: it can be changed (the Entrenador) and sent to the watch
    reloj = planes.sesiones_reloj(ident)
    assert [s["fecha"] for s in reloj] == ["2026-11-04", "2026-11-07"]
    planes.cambiar_sesion(reloj[0]["id"], "tempo 30'", ["30' a 4:20"], HOY)
    assert planes.sesion(reloj[0]["id"])["titulo"] == "tempo 30'"


@pytest.mark.parametrize("clave", ["tirada larga", "cuestas el miércoles", "Miercoles - cuestas", "Lunes series"])
def test_las_formas_del_dia(clave):  # R2: with or without accent, «:» or «-»; a day at the start of a piece only
    p = _propuesta(_a("Previsto", "previsto", [SEMANA.format(clave=clave)]))
    esperado = {"tirada larga": [], "cuestas el miércoles": [], "Miercoles - cuestas": [("2026-11-04", "cuestas")],
                "Lunes series": [("2026-11-09", "series")]}[clave]
    assert [(s["fecha"], s["titulo"]) for s in p["sesiones"]] == esperado


def test_sin_dia_o_con_un_dia_fuera_de_la_semana_no_hay_sesion(planes):  # R2 · C-456
    semanas = ["semana|1|2026-11-03|2026-11-09|6:00|tirada larga||",
               "semana|2|2026-11-10|2026-11-12|3:00|sábado: tirada 20 km; martes: series||"]
    p = _propuesta(_a("Previsto", "previsto", semanas))
    assert [(s["fecha"], s["titulo"]) for s in p["sesiones"]] == [("2026-11-10", "series")]
    assert [s["clave"] for s in p["semanas"]] == ["tirada larga", "sábado: tirada 20 km; martes: series"]
    ident = confirmar(p, {}, planes, AHORA)
    assert len(planes.leer(ident)["sesiones"]) == 1


def test_si_la_sesion_ya_esta_ese_dia_no_se_anade_otra(planes):  # R3 · C-457
    p = _propuesta(_a("Previsto", "previsto", [SEMANA.format(clave="miércoles: cuestas 10×200 m; sábado: tirada")]),
                   _a("Sesiones clave", "sesiones_clave", ["sesion|2026-11-04|Cuestas 10×200 m|15' suave|sí"]))
    assert [(s["fecha"], s["titulo"]) for s in p["sesiones"]] == [
        ("2026-11-04", "Cuestas 10×200 m"), ("2026-11-07", "tirada")]
    ident = confirmar(p, {}, planes, AHORA)
    assert [s["fecha"] for s in planes.leer(ident)["sesiones"]] == ["2026-11-04", "2026-11-07"]


# -- R4 · R-361, C-458 · the incidents come back alive ---------------------------------------------------------------

_INCIDENCIAS = ["incidencia|2026-10-01|gemelo derecho: pinchazo|abierta|",
                "visita|2026-10-03|fisio|descarga y dos días sin correr",
                "visita|2026-10-08|médico|nada roto",
                "incidencia|2026-09-10|rodilla izquierda: molestia|cerrada el 2026-10-20|estirar después de la tirada",
                "incidencia|2026-09-16|resfriado inventado"]


def test_las_molestias_de_un_md_de_la_app_vuelven_vivas(planes):  # R4 · C-458
    p = _propuesta(_a("Incidencias", "incidencias", _INCIDENCIAS))
    assert len(p["incidencias"]) == 3 and p["notas"] == []
    assert p["resumen"]["abiertas"] == 1
    assert re.search(r"3 incidencias \(1 abierta\)", _vista_plan(p, {}))
    ident = confirmar(p, {}, planes, AHORA)
    por_texto = {i["texto"]: i for i in planes.incidencias(ident)}
    gemelo = por_texto["gemelo derecho: pinchazo"]
    assert (gemelo["importada"], gemelo["estado"], gemelo["zona"], gemelo["lado"], gemelo["que_paso"],
            gemelo["desde"]) == (False, ABIERTA, "gemelo", "derecho", "pinchazo", "2026-10-01")
    assert gemelo["titulo"] == "gemelo derecho"
    assert gemelo["visitas"] == [{"fecha": "2026-10-03", "quien": "fisio", "que_dijo": "descarga y dos días sin correr"},
                                 {"fecha": "2026-10-08", "quien": "médico", "que_dijo": "nada roto"}]
    rodilla = por_texto["rodilla izquierda: molestia"]
    assert (rodilla["importada"], rodilla["estado"], rodilla["cerrada"], rodilla["leccion"], rodilla["visitas"]) == (
        False, CERRADA, "2026-10-20", "estirar después de la tirada", [])
    resfriado = por_texto["resfriado inventado"]
    assert (resfriado["importada"], resfriado["estado"]) == (True, CERRADA)  # without a state, as before
    assert [i["texto"] for i in planes.abiertas()] == ["gemelo derecho: pinchazo"]  # what the team sees


def test_una_molestia_sin_zona_y_lado_reconocibles_guarda_su_texto(planes):  # R4
    p = _propuesta(_a("Incidencias", "incidencias", ["incidencia|2026-10-01|cansancio general|abierta|",
                                                     "incidencia|2026-10-02|espalda: tirón|abierta|"]))
    ident = confirmar(p, {}, planes, AHORA)
    por_texto = {i["texto"]: i for i in planes.incidencias(ident)}
    assert (por_texto["cansancio general"]["que_paso"], por_texto["cansancio general"]["estado"]) == (
        "cansancio general", ABIERTA)
    espalda = por_texto["espalda: tirón"]
    assert (espalda["zona"], espalda["lado"], espalda["estado"]) == ("espalda", None, ABIERTA)


def test_una_visita_sin_molestia_viva_delante_no_se_pierde():  # R4, R5: nothing lost
    p = _propuesta(_a("Incidencias", "incidencias", ["visita|2026-10-03|fisio|descarga",
                                                     "incidencia|2026-09-16|resfriado inventado",
                                                     "visita|2026-10-05|médico|reposo"]))
    assert len(p["incidencias"]) == 1  # a visit goes only with a live incident before it
    assert [n["texto"] for n in p["notas"]] == ["visita|2026-10-03|fisio|descarga", "visita|2026-10-05|médico|reposo"]


def test_el_coach_sabe_mandar_el_estado_la_leccion_y_las_visitas(tmp_path):  # R4: the shape for the coach
    from tridata.storage import DataStore

    store = DataStore(tmp_path / "data.sqlite3")
    equipo = montar_equipo(tmp_path / "data.sqlite3", store=store, token=tmp_path / "google-token.json")
    tools = {t.name: t for t in asyncio.run(build_server(store, equipo=equipo).list_tools())}
    descripcion = tools["preparar_plan"].description
    assert "incidencia|fecha|texto|estado" in descripcion and "lección" in descripcion
    assert "visita|fecha|quién|qué dijo" in descripcion
    assert "clave" in descripcion and "misma carrera" in descripcion


# -- R5 · R-362, C-459, C-460 · nothing is lost ----------------------------------------------------------------------

def test_el_parrafo_que_no_queda_en_ninguna_fila_va_a_notas_tal_cual():  # R5 · C-459
    texto = ("El 2026-10-01 noté un pinchazo en el gemelo derecho.\n\n"
             "El 2026-10-12 tuve un resfriado fuerte.\n\n"
             "Dormí poco toda la semana por el trabajo.")
    p = _propuesta(_a("Sensaciones", "incidencias", ["incidencia|2026-10-01|pinchazo en el gemelo derecho",
                                                     "incidencia|2026-10-12|resfriado fuerte"], texto))
    assert len(p["incidencias"]) == 2
    assert [(n["apartado"], n["tipo"], n["texto"]) for n in p["notas"]] == [
        (0, "nota", "Dormí poco toda la semana por el trabajo.")]


def test_ni_las_cabeceras_de_tabla_ni_lo_que_fue_a_notas_se_repiten():  # R5
    texto = ("| Carrera | Fecha | Objetivo |\n|---|:---:|---|\n| Behobia | 2026-11-08 | 1:25 |\n"
             "| Algo raro inventado | mañana | nada |")
    p = _propuesta(_a("Objetivos", "objetivos", ["carrera|Behobia|2026-11-08|1:25|pendiente|no",
                                                 "esto no se lee: algo raro inventado mañana"], texto))
    assert [n["texto"] for n in p["notas"]] == ["esto no se lee: algo raro inventado mañana"]


def test_un_apartado_con_titulo_y_nada_mas_no_crea_nota(planes):  # R5: its heading is the heading of its section
    p = _propuesta(_a("Objetivos", "objetivos", ["carrera|Behobia|2026-11-08|1:25|pendiente|no"]),
                   _a("Comparativa con 2025", "comparativa"))
    assert p["notas"] == []
    ident = confirmar(p, {}, planes, AHORA)
    plan = planes.leer(ident)
    assert plan["notas"] == [] and plan["apartados"][1]["titulo"] == "Comparativa con 2025"
    assert "## Comparativa con 2025" in escribir(plan, None, HOY)


# -- R6 · the round trip with «Bajar .md» ----------------------------------------------------------------------------

_PARTE = {"Objetivos": "objetivos", "Previsto": "previsto", "Sesiones clave": "sesiones_clave",
          "Incidencias": "incidencias"}


def _como_el_coach(md: str) -> list[Apartado]:
    """The .md the app writes, ordered as the coach is asked to (`plan_forma`): one row per thing."""
    apartados = []
    for trozo in md.split("\n## ")[1:]:
        titulo, _, cuerpo = trozo.partition("\n")
        parte, filas = _PARTE[titulo], []
        tabla, sesion = None, None
        for linea in cuerpo.splitlines():
            if linea.startswith("|"):
                celdas = [c.strip() for c in linea.strip("|").split("|")]
                if set("".join(celdas)) <= set("-: "):
                    continue
                if tabla is None:
                    tabla = {"Carrera": "carrera", "Semana": "semana"}[celdas[0]]
                    continue
                filas.append("|".join([tabla, *celdas]))
                continue
            tabla = None
            if linea.startswith("### "):
                fecha, _, titulo_sesion = linea[4:].partition(" · ")
                sesion = [fecha, titulo_sesion, []]
                filas.append(sesion)
            elif linea.startswith("- ") and sesion is not None:
                sesion[2].append(linea[2:])
            elif linea.startswith("- "):
                fecha, _, texto = linea[2:].partition(": ")
                filas.append(f"incidencia|{fecha}|{texto}")
            elif linea.startswith("  - Estado: "):
                filas[-1] += f"|{linea[len('  - Estado: '):]}|"
            elif linea.startswith("  - Visita "):
                fecha, _, resto = linea[len("  - Visita "):].partition(" · ")
                quien, _, dijo = resto.partition(": ")
                filas.append(f"visita|{fecha}|{quien}|{dijo}")
            elif linea.startswith("  - Lección: "):
                i = max(k for k, f in enumerate(filas) if isinstance(f, str) and f.startswith("incidencia|"))
                filas[i] += linea[len("  - Lección: "):]
        filas = [f if isinstance(f, str) else f"sesion|{f[0]}|{f[1]}|{';'.join(f[2])}|no" for f in filas]
        apartados.append(_a(titulo, parte, filas, cuerpo.strip()))
    return apartados


def test_un_md_de_la_app_se_importa_y_se_baja_igual(tmp_path, planes):  # R6
    primero = _propuesta(
        _objetivos(),
        _a("Previsto", "previsto", [SEMANA.format(clave="miércoles: cuestas 10×200 m; sábado tirada 25 km"),
                                    "carrera|Valencia|2026-12-06|||no"]),
        _a("Sesiones clave", "sesiones_clave", ["sesion|2026-11-05|Series 5×1000|15' suave;5×1000 a 4:00|sí"]),
        _a("Incidencias", "incidencias", _INCIDENCIAS))
    uno = confirmar(primero, {}, planes, AHORA)
    md = escribir(planes.leer(uno), None, HOY, planes.incidencias(uno))
    assert md.count("Valencia") == 1 and md.count("cuestas 10×200 m") == 2  # the week and its session
    assert "  - Estado: abierta" in md and "  - Visita 2026-10-08 · médico: nada roto" in md

    otra = Planes(tmp_path / "otra.sqlite3")
    dos = confirmar(proponer_ordenado(_como_el_coach(md), None, HOY, "Plan inventado 116", planes=otra), {}, otra,
                    AHORA)
    assert escribir(otra.leer(dos), None, HOY, otra.incidencias(dos)) == md
    assert [i["texto"] for i in otra.abiertas()] == ["gemelo derecho: pinchazo"]
