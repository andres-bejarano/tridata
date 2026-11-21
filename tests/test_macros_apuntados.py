"""Unit 101 (REC-45 of `consultar-margen`, mapa v44): the macros of what was already logged, reviewed by the team.

Plans: R-284, R-285 · C-358..C-361, S-93. R3, R4 and R6 of the unit (R-284 is the criterion that carries it).

- R3: `preparar_macros` without propuestas lists, besides what is saved without macros, what was logged without them
  in the last 14 days (eaten and planned), each with its clave `apuntado:<id>`, its day, quantity and kcal. What comes
  from a saved ingredient, recipe or common food that has macros (by its `origen`, or by its name without capitals
  nor accents and the same base) comes with them already worked out for its quantity. Older than 14 days: out.
- R4: `guardar_macros` with the fingerprint saves them in each accepted logged food («estimado»; «etiqueta» if Andrés
  corrected them), `rechazar` leaves them without; no kcal changes. Without the fingerprint, or if the list changed,
  nothing is saved and the new one comes back.
- R6: the context says how many foods of the last 14 days have no macros; `preparar_macros` says to offer them.

Every datum is invented; every database is a temporary SQLite; nothing talks to an outside service.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tridata.conversaciones import Conversaciones
from tridata.conversaciones.contexto import INSTRUCCIONES, montar
from tridata.conversaciones.desde_pc import TITULO, montar_equipo
from tridata.mcp_server import build_server
from tridata.nutricion import Alimento, RegistroComidas
from tridata.nutricion.recetas import Recetario
from tridata.storage import DataStore

HOY = date(2026, 11, 21)
AHORA = datetime(2026, 11, 21, 10, 0)
HUELLA = re.compile(r"Huella: ([0-9a-f]{12})")
PAN = "Pan de molde inventado"


def _tablas(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _apuntados(db: Path) -> dict[int, sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return {f["id"]: f for f in conn.execute("SELECT * FROM alimento_apuntado ORDER BY id")}
    finally:
        conn.close()


class Equipo:
    """The team of the PC over a temporary SQLite, through the MCP server as the coach calls it."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.store = DataStore(self.db)
        self.equipo = montar_equipo(self.db, store=self.store, medicos_dir=None, token=tmp_path / "token.json",
                                    hoy=lambda: HOY, ahora=lambda: AHORA)
        self.recetario: Recetario = self.equipo.asistente.recetario
        self.registro: RegistroComidas = self.equipo.asistente.registro
        self.server = build_server(self.store, equipo=self.equipo)

    def llamar(self, nombre: str, argumentos: dict | None = None) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos or {}))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)

    def huella(self, texto: str) -> str:
        encontrada = HUELLA.search(texto)
        assert encontrada, texto
        return encontrada.group(1)

    def apuntar(self, dia: date, nombre: str, cantidad: float, unidad: str, kcal: float, **extra) -> int:
        (fila,) = self.registro.guardar([Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad=unidad, kcal=kcal,
                                                  **extra)], hoy=HOY, texto=nombre, sin_cambios=True)
        return fila["id"]

    def mensajes_pc(self) -> list[dict]:
        conversaciones = Conversaciones(self.db)
        return [m for c in conversaciones.lista() if c["titulo"] == TITULO for m in conversaciones.mensajes(c["id"])]

    def contexto(self) -> dict:
        a = self.equipo.asistente
        ctx = montar(registro=a.registro, pesos=a.pesos, ajustes=a.ajustes, store=self.store, transcripciones=[],
                     aviso_medico=None, hoy=HOY, planes=a.planes)
        return json.loads(ctx.bloques[-1])


@pytest.fixture
def eq(tmp_path) -> Equipo:
    return Equipo(tmp_path)


def _base(eq: Equipo) -> dict:
    """C-358, C-359: 9 foods without macros in the last 14 days — 3 of «Pan de molde» (saved, with macros now) and 6
    more, one of them planned — plus one of 20 days ago and one that already has its macros."""
    pan = eq.recetario.crear_fijo({"nombre": PAN, "kcal": 260, "por": "100g"})
    desde_pan = eq.recetario.para_apuntar("fijo", pan["id"], 60)  # logged from the saved one, before its macros
    ids = {
        "pan_origen": eq.registro.guardar([Alimento.desde_dict({**desde_pan, "dia": (HOY - timedelta(days=2))
                                                                .isoformat()})], hoy=HOY, texto="pan",
                                          sin_cambios=True)[0]["id"],
        "pan_nombre": eq.apuntar(HOY - timedelta(days=5), "PAN DE MOLDE INVENTADO", 80, "g", 208),  # by its name
        "pan_hoy": eq.apuntar(HOY, "pan de molde inventado", 40, "g", 104),
        "galleta": eq.apuntar(HOY - timedelta(days=1), "Galleta Rustica inventada", 30, "g", 140),
        "pan_racion": eq.apuntar(HOY - timedelta(days=3), "Pan de molde inventado", 2, "rebanada", 150),  # other base
        "yogur": eq.apuntar(HOY - timedelta(days=4), "yogur raro inventado", 125, "g", 90),
        "bocadillo": eq.apuntar(HOY - timedelta(days=13), "bocadillo inventado", 1, "ración", 450),  # the 14th day
        "tortilla": eq.apuntar(HOY - timedelta(days=7), "tortilla inventada", 150, "g", 300),
        "cena": eq.apuntar(HOY + timedelta(days=1), "cena planeada inventada", 1, "ración", 600),  # planned
        "viejo": eq.apuntar(HOY - timedelta(days=20), "potaje viejo inventado", 300, "g", 400),  # C-359: out
        "con": eq.apuntar(HOY - timedelta(days=1), "batido inventado", 1, "vaso", 200, proteina_g=20, hidratos_g=20,
                          grasa_g=4.5),
    }
    eq.recetario.cambiar_fijo(pan["id"], {"nombre": PAN, "kcal": 260, "por": "100g", "proteina": 9, "hidratos": 48,
                                          "grasa": 3.5})
    return ids


CALCULADOS = ("pan_origen", "pan_nombre", "pan_hoy")
A_ESTIMAR = ("galleta", "pan_racion", "yogur", "bocadillo", "tortilla", "cena")


def _propuestas(ids: dict) -> list[dict]:
    """The 3 calculated ones by their clave alone; the rest estimated by the team; the biscuit corrected by Andrés."""
    estimados = {"pan_racion": (6, 40, 2), "yogur": (4, 12, 3), "bocadillo": (18, 55, 17), "tortilla": (12, 15, 21),
                 "cena": (35, 60, 22)}
    return ([{"clave": f"apuntado:{ids[k]}"} for k in CALCULADOS]
            + [{"clave": f"apuntado:{ids[k]}", "proteina": p, "hidratos": h, "grasa": g}
               for k, (p, h, g) in estimados.items()]
            + [{"clave": f"apuntado:{ids['galleta']}", "proteina": 2.5, "hidratos": 20, "grasa": 5.5, "corregido": True}])


# -- R3 · R-284, C-358, C-359: the list of what was logged without macros ------------------------------------------

def test_lo_que_falta_trae_lo_apuntado_de_14_dias_con_los_calculados(eq):  # R3 · C-358, C-359 (R-284)
    ids = _base(eq)
    antes = _tablas(eq.db)
    falta = eq.recetario.macros_que_faltan(hoy=HOY)
    apuntados = {f["clave"]: f for f in falta["filas"] if f["clave"].startswith("apuntado:")}
    assert set(apuntados) == {f"apuntado:{ids[k]}" for k in (*CALCULADOS, *A_ESTIMAR)}  # 9: not the old one
    pan = apuntados[f"apuntado:{ids['pan_nombre']}"]
    assert (pan["dia"], pan["cantidad"], pan["unidad"], pan["kcal"]) == ((HOY - timedelta(days=5)).isoformat(), 80,
                                                                         "g", 208)
    assert pan["calculado"] == {"proteina": 7.2, "hidratos": 38.4, "grasa": 2.8}  # 80 g of 9 / 48 / 3.5 per 100 g
    assert apuntados[f"apuntado:{ids['pan_origen']}"]["calculado"] == {"proteina": 5.4, "hidratos": 28.8, "grasa": 2.1}
    assert apuntados[f"apuntado:{ids['pan_hoy']}"]["calculado"] == {"proteina": 3.6, "hidratos": 19.2, "grasa": 1.4}
    assert all(apuntados[f"apuntado:{ids[k]}"]["calculado"] is None for k in A_ESTIMAR)  # «rebanada»: not its base
    assert f"apuntado:{ids['viejo']}" not in apuntados and f"apuntado:{ids['con']}" not in apuntados
    assert _tablas(eq.db) == antes  # read only


def test_preparar_macros_sin_propuestas_ensena_lo_apuntado(eq):  # R3 · C-358
    ids = _base(eq)
    antes = _tablas(eq.db)
    texto = eq.llamar("preparar_macros")
    for k in (*CALCULADOS, *A_ESTIMAR):
        assert f"apuntado:{ids[k]}" in texto, k
    assert f"apuntado:{ids['viejo']}" not in texto and "potaje viejo" not in texto
    linea_pan = next(x for x in texto.splitlines() if f"apuntado:{ids['pan_nombre']}" in x)
    assert (HOY - timedelta(days=5)).isoformat() in linea_pan and "80 g" in linea_pan and "208" in linea_pan
    assert f"calculado de tu ingrediente «{PAN}»" in linea_pan and "7,2" in linea_pan
    assert "a estimar" in next(x for x in texto.splitlines() if f"apuntado:{ids['galleta']}" in x)
    assert not HUELLA.search(texto)
    assert _tablas(eq.db) == antes and eq.mensajes_pc() == []


def test_lo_apuntado_de_una_receta_con_macros_sale_calculado(eq):  # R3: by its `origen` («de tu receta …»)
    rec = eq.recetario
    rec.crear_fijo({"nombre": "avena inventada", "kcal": 370, "por": "100g"})
    porridge = rec.crear_receta({"nombre": "Porridge inventado", "peso_neto_g": 200, "ingredientes": [
        {"nombre": "avena inventada", "cantidad": 50, "unidad": "g", "kcal": 1, "guardado": "avena inventada"}]}, HOY)
    datos = rec.para_apuntar("receta", porridge["id"], 100)
    (fila,) = eq.registro.guardar([Alimento.desde_dict({**datos, "nombre": "mi porridge", "dia": HOY.isoformat()})],
                                  hoy=HOY, texto="porridge", sin_cambios=True)
    rec.cambiar_fijo(rec.fijos()[0]["id"], {"nombre": "avena inventada", "kcal": 370, "por": "100g", "proteina": 13,
                                            "hidratos": 60, "grasa": 7})
    (que,) = [f for f in rec.macros_que_faltan(hoy=HOY)["filas"] if f["clave"] == f"apuntado:{fila['id']}"]
    assert que["calculado"] == {"proteina": 3.2, "hidratos": 15.0, "grasa": 1.8}  # half of 50 g of oats: 25 g


def test_sin_hoy_solo_lo_guardado_como_antes(eq):  # «NO debe haber cambiado»: the 098 listing stays
    _base(eq)
    assert not any(f["clave"].startswith("apuntado:") for f in eq.recetario.macros_que_faltan()["filas"])


# -- R4 · R-284, C-358, C-360: saving them with the «sí» ------------------------------------------------------------

def test_guardar_con_la_huella_pone_macros_en_lo_apuntado_sin_cambiar_kcal(eq):  # R4 · C-358 (R-284)
    ids = _base(eq)
    antes = _apuntados(eq.db)
    propuestas = _propuestas(ids)
    texto = eq.llamar("preparar_macros", {"propuestas": propuestas})
    assert _apuntados(eq.db) == antes  # preparar writes nothing
    linea = next(x for x in texto.splitlines() if f"apuntado:{ids['pan_origen']}" in x)
    assert "5,4" in linea and "28,8" in linea
    hecho = eq.llamar("guardar_macros", {"papel": "nutricionista", "propuestas": propuestas,
                                         "huella": eq.huella(texto), "rechazar": [f"apuntado:{ids['yogur']}"]})
    assert hecho.startswith("Hecho"), hecho
    despues = _apuntados(eq.db)
    assert {i: f["kcal"] for i, f in despues.items()} == {i: f["kcal"] for i, f in antes.items()}  # no kcal changes
    con = [i for i, f in despues.items() if all(f[k] is not None for k in ("proteina_g", "hidratos_g", "grasa_g"))
           and i != ids["con"]]
    assert len(con) == 8  # 3 calculated + 4 estimated + the biscuit; the yogurt said no
    pan = despues[ids["pan_nombre"]]
    assert (pan["proteina_g"], pan["hidratos_g"], pan["grasa_g"], pan["macros_origen"]) == (7.2, 38.4, 2.8, "estimado")
    galleta = despues[ids["galleta"]]
    assert (galleta["proteina_g"], galleta["macros_origen"]) == (2.5, "etiqueta")  # Andrés corrected it
    assert despues[ids["tortilla"]]["grasa_g"] == 21 and despues[ids["tortilla"]]["macros_origen"] == "estimado"
    yogur = despues[ids["yogur"]]
    assert (yogur["proteina_g"], yogur["macros_origen"]) == (None, None)
    assert despues[ids["viejo"]]["proteina_g"] is None  # older than 14 days: untouched
    for i in (ids["viejo"], ids["con"]):
        assert dict(despues[i]) == dict(antes[i])
    (mensaje,) = eq.mensajes_pc()
    assert mensaje["papel"] == "nutricionista" and "Hecho" in mensaje["texto"]
    falta = eq.recetario.macros_que_faltan(hoy=HOY)["filas"]
    assert [f["clave"] for f in falta if f["clave"].startswith("apuntado:")] == [f"apuntado:{ids['yogur']}"]


def test_sin_la_huella_o_si_la_lista_cambio_no_guarda_nada(eq):  # R4 · C-360
    ids = _base(eq)
    propuestas = _propuestas(ids)
    texto = eq.llamar("preparar_macros", {"propuestas": propuestas})
    antes = _tablas(eq.db)
    otra = eq.llamar("guardar_macros", {"papel": "nutricionista", "propuestas": propuestas, "huella": "0" * 12})
    assert not otra.startswith("Hecho") and "cambiado" in otra and HUELLA.search(otra)
    assert _tablas(eq.db) == antes and eq.mensajes_pc() == []
    pan = next(f for f in eq.recetario.fijos() if f["nombre"] == PAN)  # the saved one changes: so do the calculated
    eq.recetario.cambiar_fijo(pan["id"], {"nombre": PAN, "kcal": 260, "por": "100g", "proteina": 10, "hidratos": 47,
                                          "grasa": 3.5})
    antes = _tablas(eq.db)
    vieja = eq.llamar("guardar_macros", {"papel": "nutricionista", "propuestas": propuestas,
                                         "huella": eq.huella(texto)})
    assert not vieja.startswith("Hecho") and "cambiado" in vieja
    linea = next(x for x in vieja.splitlines() if f"apuntado:{ids['pan_nombre']}" in x)
    assert eq.huella(vieja) != eq.huella(texto) and "37,6" in linea  # the new list: 80 g at 47 g per 100 g
    assert _tablas(eq.db) == antes and eq.mensajes_pc() == []


def test_lo_de_hace_mas_de_14_dias_no_se_puede_guardar(eq):  # R3, R4 · C-359
    ids = _base(eq)
    propuestas = [{"clave": f"apuntado:{ids['viejo']}", "proteina": 20, "hidratos": 40, "grasa": 10}]
    antes = _tablas(eq.db)
    texto = eq.llamar("preparar_macros", {"propuestas": propuestas})
    assert "no es nada que falte" in texto and not HUELLA.search(texto)
    assert _tablas(eq.db) == antes


def test_lo_que_no_esta_calculado_necesita_sus_macros(eq):  # R4: only the calculated ones go by their clave alone
    ids = _base(eq)
    texto = eq.llamar("preparar_macros", {"propuestas": [{"clave": f"apuntado:{ids['tortilla']}"}]})
    assert "tortilla inventada" in texto and "faltan sus macros" in texto and not HUELLA.search(texto)


def test_macros_imposibles_de_lo_apuntado_se_rechazan(eq):  # R4: per 100 g they cannot pass 100 g
    ids = _base(eq)
    propuestas = [{"clave": f"apuntado:{ids['galleta']}", "proteina": 10, "hidratos": 20, "grasa": 5}]  # 35 g in 30 g
    texto = eq.llamar("preparar_macros", {"propuestas": propuestas})
    assert "100 g" in texto and not HUELLA.search(texto)


# -- R6 · R-285, C-361: the context and the description ------------------------------------------------------------

def test_el_contexto_dice_cuantos_alimentos_de_14_dias_no_tienen_macros(eq):  # R6 · C-361
    _base(eq)
    assert eq.contexto()["alimentos_sin_macros"] == "9 alimentos de los últimos 14 días sin macros"


def test_sin_nada_que_falte_el_contexto_no_lo_dice(eq):  # R6
    eq.apuntar(HOY, "batido inventado", 1, "vaso", 200, proteina_g=20, hidratos_g=20, grasa_g=4.5)
    assert "alimentos_sin_macros" not in eq.contexto()


def test_las_instrucciones_y_preparar_macros_le_dicen_que_se_los_ofrezca(eq):  # R6, R3
    assert '"alimentos_sin_macros"' in INSTRUCCIONES and "preparar_macros" in INSTRUCCIONES
    herramientas = {t.name: t for t in asyncio.run(eq.server.list_tools())}
    preparar = herramientas["preparar_macros"].description
    assert "apuntado:" in preparar and "14 days" in preparar and "offer" in preparar
    assert "corregido" in herramientas["guardar_macros"].description
    assert "«sí»" in herramientas["guardar_macros"].description
