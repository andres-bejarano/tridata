"""The assistant changes what was logged and the plan of a day (unit 022, REC-13 of `pedir-al-asistente`).

Substitute a food or correct its quantity (one action, one write, G-40), move a food to another day, the levels,
the % and the planned training of a day, and the «you had X, not Y» warning (R-77). Unit level over the catalogue
with a seeded temporary SQLite, and confirmation with Starlette's TestClient against the routes of unit 021.
Every `CambioInterpretado` is written by hand (what a double of Claude would return); Claude is never called.
Plans: R1-R8, R12 of the unit · R-74..R-77, R-79, G-39, G-40. Every value is invented.

Today is Thursday 2026-10-22. Logged today: «yogur natural de prueba» 125 g (75 kcal), «pasta de prueba» 100 g
(360 kcal), «plátano de prueba» 120 g (108 kcal) and «arroz de prueba» 200 g (457 kcal): 1,000 kcal. Ingredient
«manzana de prueba» 80 kcal per unit. Plan −15 %, weight target 72 kg for 2027-02-01. Sunday 2026-10-18: 60 min of
«Bici suave», 480 kcal.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.ia import CambioInterpretado, ListaInterpretada
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.nutricion.asistente import Contexto, Peticion, catalogo, confirmar, proponer, rehacer, texto_acciones
from tridata.nutricion.recetas import Recetario
from tridata.web import crear_app

HOY = date(2026, 10, 22)  # a Thursday
AYER = HOY - timedelta(days=1)
MANANA = HOY + timedelta(days=1)
MARTES = HOY - timedelta(days=2)
DOMINGO = date(2026, 10, 18)
BICI = {"activity_id": "prueba-bici", "activity_date": DOMINGO.isoformat(), "activity_type": "cycling",
        "name": "Bici suave", "duration_seconds": 3600, "calories": 480}


class Base:
    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1980, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000,
                              "objetivo_pct": -15, "limite_saludable_pct": 25, "altura_cm": 175,
                              "objetivo_peso": {"kg": 72, "fecha": "2027-02-01"}}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.pesos = RegistroPeso(self.db)
        self.recetario = Recetario(self.db)
        self.recetario.crear_fijo({"nombre": "manzana de prueba", "kcal": 80, "por": "unidad"})
        self.recientes = [BICI]
        self.ctx = Contexto(registro=self.registro, objetivos=self.objetivos, ajustes=self.ajustes, pesos=self.pesos,
                            recetario=self.recetario, hoy=lambda: HOY, recientes=lambda: self.recientes)
        self.yogur = self.apuntar("yogur natural de prueba", HOY, 125, 75, "10:00")
        self.pasta = self.apuntar("pasta de prueba", HOY, 100, 360, "14:00")
        self.platano = self.apuntar("plátano de prueba", HOY, 120, 108, "17:30")
        self.apuntar("arroz de prueba", HOY, 200, 457, "21:00")

    def apuntar(self, nombre, dia, cantidad, kcal, hora="12:00", unidad="g"):
        fila = self.registro.guardar([Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad=unidad, kcal=kcal)],
                                     hoy=HOY, texto=nombre, sin_cambios=True)[0]
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE alimento_apuntado SET creado = ? WHERE id = ?", (f"{dia.isoformat()}T{hora}:00", fila["id"]))
        return fila["id"]

    def total(self, dia=HOY):
        return round(sum(a["kcal"] or 0 for a in self.registro.apuntes_del_dia(dia)))

    def tarjetas(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT COUNT(*) FROM tarjeta_asistente").fetchone()[0]


@pytest.fixture
def b(tmp_path):
    return Base(tmp_path)


def _uno(b, dicho="texto de prueba", pedido=HOY, **campos):
    (resultado,) = proponer([CambioInterpretado(**campos)], b.ctx, Peticion(texto=dicho, fecha=pedido))
    return resultado


# -- the catalogue ------------------------------------------------------------------------------------------------

def test_el_catalogo_gana_las_acciones_del_dia():
    ids = set(catalogo())
    assert {"cambiar_alimento", "mover_alimento", "niveles_dia", "objetivo_dia", "entreno_previsto"} <= ids
    assert {"pasos_dia", "quitar_alimento"} <= ids  # unit 021 stays
    texto = texto_acciones()
    for i in ("cambiar_alimento", "mover_alimento", "niveles_dia", "objetivo_dia", "entreno_previsto"):
        assert f"- {i}:" in texto


# -- R1 · R-74, C-94: substitute, one action ----------------------------------------------------------------------

def test_sustituir_por_algo_guardado_una_sola_accion(b):  # R1 (the bearing criterion)
    t = _uno(b, "quita el yogur, al final fue una manzana", accion="cambiar_alimento", objeto="yogur",
             texto="manzana de prueba", cantidad=1, unidad="unidad", kcal_estimadas=95)
    assert t["tipo"] == "propuesta" and t["accion"] == "cambiar_alimento"
    assert t["texto"] == ("Voy a cambiar en el 22-10: yogur natural de prueba 125 g (75 kcal) → "
                          "manzana de prueba 1 unidad (80 kcal), de tu ingrediente")
    assert (t["antes"], t["despues"], t["dia"]) == (
        "yogur natural de prueba 125 g (75 kcal)", "manzana de prueba 1 unidad (80 kcal)", "2026-10-22")
    assert b.registro.alimento(b.yogur)["nombre"] == "yogur natural de prueba" and b.tarjetas() == 0  # nothing yet
    hecho = confirmar(t, b.ctx)
    assert "manzana de prueba" in hecho["hecho"]
    fila = b.registro.alimento(b.yogur)  # the same row, changed: never removed and added in two writes
    assert (fila["nombre"], fila["cantidad"], fila["unidad"], fila["kcal"]) == ("manzana de prueba", 1, "unidad", 80)
    assert fila["origen"].startswith("de tu ingrediente") and fila["estimado"] is False and fila["estado"] == "comido"
    assert [a["nombre"] for a in b.registro.apuntes_del_dia(HOY)].count("yogur natural de prueba") == 0
    assert b.total() == 1005


def test_sustituir_por_algo_no_guardado_es_estimado(b):  # R1
    t = _uno(b, "quita el yogur, al final fue una pera de prueba", accion="cambiar_alimento", objeto="yogur",
             texto="pera de prueba", cantidad=1, unidad="unidad", kcal_estimadas=85)
    assert t["texto"] == ("Voy a cambiar en el 22-10: yogur natural de prueba 125 g (75 kcal) → "
                          "pera de prueba 1 unidad (85 kcal), estimado")
    confirmar(t, b.ctx)
    fila = b.registro.alimento(b.yogur)
    assert (fila["nombre"], fila["kcal"], fila["estimado"], fila["origen"]) == ("pera de prueba", 85, True, None)
    assert b.total() == 1010


def test_sustituir_por_algo_no_guardado_sin_kcal_no_propone(b):  # G-40
    r = _uno(b, accion="cambiar_alimento", objeto="yogur", texto="pera de prueba", cantidad=1, unidad="unidad")
    assert r["tipo"] == "no" and "pera de prueba" in r["texto"]


def test_cambiar_lo_que_no_esta(b):
    r = _uno(b, accion="cambiar_alimento", objeto="lentejas", cantidad=200, unidad="g")
    assert r == {"tipo": "no", "texto": "Hoy no tienes ningún lentejas apuntado"}


# -- R2 · R-74, C-95: the quantity ----------------------------------------------------------------------------------

def test_cambiar_la_cantidad(b):  # R2
    t = _uno(b, "la pasta eran 120 gramos, no 100", accion="cambiar_alimento", objeto="pasta", cantidad=120,
             unidad="g", antes_dicho="100")
    assert t["texto"] == "Voy a cambiar pasta de prueba del 22-10: 100 g (360 kcal) → 120 g (432 kcal)"
    assert t["avisos"] == []  # what he said of before matches
    assert [c["clave"] for c in t["campos"]] == ["cantidad"]
    confirmar(t, b.ctx)
    fila = b.registro.alimento(b.pasta)
    assert (fila["cantidad"], fila["kcal"]) == (120, 432)
    assert b.total() == 1072


def test_corregir_la_cantidad_en_la_tarjeta(b):  # R2 · R-71
    t = _uno(b, accion="cambiar_alimento", objeto="pasta", cantidad=120, unidad="g")
    otra = rehacer(t, b.ctx, valores={"cantidad": 150})
    assert otra["texto"].endswith("→ 150 g (540 kcal)") and otra["corregida"] is True
    mala = rehacer(t, b.ctx, valores={"cantidad": -3})
    assert mala["error"] and "mayor que 0" in mala["error"]


# -- R3 · R-75, C-96: move ------------------------------------------------------------------------------------------

def test_mover_a_manana_queda_planeado(b):  # R3
    t = _uno(b, "el plátano pásalo a mañana", accion="mover_alimento", objeto="plátano", fecha=MANANA)
    assert t["texto"] == "Voy a mover plátano de prueba 120 g (108 kcal) del 22-10 al 23-10, como planeado"
    assert t["dia"] == "2026-10-22"
    confirmar(t, b.ctx)
    assert b.total() == 892
    (movido,) = b.registro.apuntes_del_dia(MANANA)
    assert (movido["id"], movido["estado"]) == (b.platano, "planeado")


def test_mover_a_ayer_queda_comido(b):  # R3
    t = _uno(b, accion="mover_alimento", objeto="plátano", fecha=AYER)
    assert t["texto"].endswith("del 22-10 al 21-10, como comido")
    confirmar(t, b.ctx)
    assert b.registro.alimento(b.platano)["estado"] == "comido"
    assert b.registro.alimento(b.platano)["dia"] == "2026-10-21"


def test_mover_al_mismo_dia_no(b):
    r = _uno(b, accion="mover_alimento", objeto="plátano", fecha=HOY)
    assert r["tipo"] == "no" and "ya está" in r["texto"]


# -- R4 · R-76, C-97: the levels --------------------------------------------------------------------------------------

def test_niveles_de_hoy(b):  # R4
    t = _uno(b, "hoy niveles 20, 15 y 10", accion="niveles_dia", valores=[-20, -15, -10], dia=HOY)
    assert t["texto"] == "Voy a cambiar los niveles del 22-10: −10 / 0 / +10 % → −20 / −15 / −10 % (déficit)"
    assert any(c["clave"] == "niveles" for c in t["campos"])
    assert b.objetivos.del_dia(HOY) is None
    confirmar(t, b.ctx)
    assert b.objetivos.del_dia(HOY)["niveles"] == [-20, -15, -10]
    assert [m["nivel"] for m in b.ctx.resumen(HOY)["margenes"]] == [-20, -15, -10]


def test_mas_de_cinco_niveles_no(b):  # R4
    r = _uno(b, accion="niveles_dia", valores=[1, 2, 3, 4, 5, 6], dia=HOY)
    assert r == {"tipo": "no", "texto": "los niveles del día son como mucho 5"}


def test_corregir_los_niveles_en_la_tarjeta(b):  # R4 · corrigible
    t = _uno(b, accion="niveles_dia", valores=[-20, -15, -10], dia=HOY)
    otra = rehacer(t, b.ctx, valores={"niveles": "-25, -10"})
    assert otra["texto"].endswith("→ −25 / −10 % (déficit)") and otra["error"] is None


# -- R5 · R-76, C-98: the % and the delay ---------------------------------------------------------------------------

def test_hoy_mantenimiento_con_el_retraso(b):  # R5
    t = _uno(b, "hoy mantenimiento", accion="objetivo_dia", valor=0, dia=HOY)
    assert t["texto"] == "Voy a cambiar el objetivo del 22-10: −15 % → 0 %"
    assert any("1 día más tarde" in a for a in t["avisos"])
    confirmar(t, b.ctx)
    assert b.objetivos.del_dia(HOY)["porcentaje"] == 0


def test_objetivo_igual_no_propone(b):
    r = _uno(b, accion="objetivo_dia", valor=-15, dia=HOY)
    assert r["tipo"] == "no" and "ya es" in r["texto"]


def test_objetivo_fuera_de_rango_como_a_mano(b):  # G-39
    r = _uno(b, accion="objetivo_dia", valor=-70, dia=HOY)
    assert r == {"tipo": "no", "texto": "el % del objetivo del día tiene que estar entre -50 y 50 %"}


# -- R6 · R-76, C-99: the planned training -------------------------------------------------------------------------

def test_entreno_previsto_como_el_del_domingo(b):  # R6
    t = _uno(b, "hoy además 45 minutos de bici suave como el del domingo", accion="entreno_previsto",
             objeto="bici suave", cantidad=45, fecha=DOMINGO, dia=HOY)
    assert t["tipo"] == "propuesta" and t["dia"] == "2026-10-22"
    assert "45 min" in t["texto"] and "360 kcal" in t["texto"] and "18-10" in t["texto"]
    assert t["parametros"]["previsto"]["kcal"] == 360
    confirmar(t, b.ctx)
    previsto = b.objetivos.del_dia(HOY)["previsto"]
    assert (previsto["actividad_id"], previsto["minutos"], previsto["kcal"]) == ("prueba-bici", 45, 360)


def test_entreno_que_no_esta_en_los_ultimos_14_dias(b):  # R6
    b.recientes = []
    r = _uno(b, accion="entreno_previsto", objeto="bici suave", cantidad=45, fecha=DOMINGO, dia=HOY)
    assert r == {"tipo": "no", "texto": "no encuentro ese entreno en los últimos 14 días"}


# -- R7 · R-77, C-101: what he says of before does not match -------------------------------------------------------

def test_los_pasos_que_dice_no_cuadran(b):  # R7
    t = _uno(b, "hoy en vez de 8 mil pasos son 15 mil", accion="pasos_dia", valor=15000, dia=HOY, antes_dicho="8 mil")
    assert t["texto"] == "Voy a cambiar los pasos previstos del 22-10: 10.000 → 15.000"
    assert t["avisos"] == ["tenías 10.000, no 8.000"]


def test_la_cantidad_que_dice_no_cuadra(b):  # R7
    t = _uno(b, accion="cambiar_alimento", objeto="pasta", cantidad=120, unidad="g", antes_dicho="90")
    assert t["avisos"] == ["tenías 100 g, no 90 g"]


# -- R12: a past day for the plan of the day ---------------------------------------------------------------------------

@pytest.mark.parametrize("campos", [
    {"accion": "niveles_dia", "valores": [-20, -10]},
    {"accion": "objetivo_dia", "valor": 0},
    {"accion": "entreno_previsto", "objeto": "bici suave", "cantidad": 45},
])
def test_un_dia_pasado_no(b, campos):  # R12
    r = _uno(b, "el martes niveles 20 y 10", dia=MARTES, **campos)
    assert r["tipo"] == "no" and r["texto"].startswith("ese día ya pasó")
    assert b.objetivos.del_dia(MARTES) is None


# -- R1, R3, R8 through the web: confirming writes like by hand, on the day of the card -----------------------------

class Web:
    def __init__(self, tmp_path):
        self.b = Base(tmp_path)
        self.interprete = MagicMock()
        self.hoy = HOY
        self.cliente = TestClient(crear_app(self.b.registro, self.interprete, self.b.ajustes, hoy=lambda: self.hoy,
                                            objetivos=self.b.objetivos, recetario=self.b.recetario,
                                            pesos=self.b.pesos))

    def tarjeta(self, dicho, **campos):
        self.interprete.interpretar.side_effect = lambda t, hoy: ListaInterpretada(
            alimentos=[], cambios=[CambioInterpretado(**campos)])
        r = self.cliente.post("/api/interpretar", json={"texto": dicho})
        assert r.status_code == 200, r.text
        (t,) = r.json()["cambios"]
        return t

    def confirmar(self, t):
        return self.cliente.post("/api/asistente/confirmar", json={"tarjeta": t})


@pytest.fixture
def w(tmp_path):
    return Web(tmp_path)


def test_web_sustituir_y_el_dia_suma_lo_nuevo(w):  # R1
    t = w.tarjeta("quita el yogur, al final fue una manzana", accion="cambiar_alimento", objeto="yogur",
                  texto="manzana de prueba", cantidad=1, unidad="unidad")
    r = w.confirmar(t)
    assert r.status_code == 200, r.text
    assert r.json()["hecho"].startswith("Hecho: ")
    dia = w.cliente.get("/api/dia?fecha=2026-10-22").json()
    assert round(dia["total_kcal"]) == 1005
    assert any(a["nombre"] == "manzana de prueba" and a["origen"].startswith("de tu ingrediente")
               for a in dia["alimentos"])


def test_web_mover_a_manana(w):  # R3
    t = w.tarjeta("el plátano pásalo a mañana", accion="mover_alimento", objeto="plátano", fecha=MANANA)
    assert w.confirmar(t).status_code == 200
    assert w.b.registro.alimento(w.b.platano)["estado"] == "planeado" and w.b.total() == 892


def test_web_despues_de_medianoche_vale_el_dia_de_la_tarjeta(w):  # R8 · R-79
    t = w.tarjeta("quita el yogur", accion="cambiar_alimento", objeto="yogur", texto="manzana de prueba", cantidad=1,
                  unidad="unidad")
    assert "del 22-10" in t["texto"] or "en el 22-10" in t["texto"]
    w.hoy = MANANA  # confirmed at 00:05 of the 23rd
    r = w.confirmar(t)
    assert r.status_code == 200, r.text
    assert w.b.registro.alimento(w.b.yogur)["dia"] == "2026-10-22"
    assert w.b.registro.apuntes_del_dia(MANANA) == [] and w.b.total() == 1005
    w.hoy = HOY  # another request on the 22nd, confirmed two days later
    t = w.tarjeta("quita la pasta", accion="quitar_alimento", objeto="pasta")
    w.hoy = HOY + timedelta(days=2)
    assert w.confirmar(t).status_code == 200
    assert all(a["nombre"] != "pasta de prueba" for a in w.b.registro.apuntes_del_dia(HOY))


def test_web_si_lo_de_antes_cambio_no_se_aplica(w):  # G-41 with the new actions
    t = w.tarjeta("la pasta eran 120 gramos", accion="cambiar_alimento", objeto="pasta", cantidad=120, unidad="g")
    w.b.registro.cambiar(w.b.pasta, {"nombre": "pasta de prueba", "cantidad": 110, "unidad": "g", "kcal": 396},
                         hoy=HOY)
    r = w.confirmar(t)
    assert r.status_code == 409
    assert r.json()["tarjeta"]["texto"].endswith("110 g (396 kcal) → 120 g (432 kcal)")
    assert w.b.registro.alimento(w.b.pasta)["cantidad"] == 110
