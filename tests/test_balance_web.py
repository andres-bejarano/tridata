"""Unit 017: the Balance routes (week, chart, list of weeks, spread and back to the plan) and `barras` in /api/peso.

Plans: `cerrar-dia` R-24, R-25, «Historial y gráfica»; `consultar-margen` R-19, C-19, G-21, R-20, C-20, G-22;
Q-3. Starlette TestClient over a temporary SQLite and `DataStore`; the Claude interpreter is a `MagicMock`
and Garmin is never called. Every value is invented. «Today» is Wednesday 2026-10-21; plan −10 %, healthy
limit 25 %, the race «Triatlón de prueba» on Sunday 25 with one maintenance day.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.models import DailyStats
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, Cierres, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 10, 21)  # a Wednesday
LUNES = date(2026, 10, 19)
MARTES = date(2026, 10, 20)
AJUSTES = {"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000, "objetivo_pct": -10,
           "limite_saludable_pct": 25}


class Web:
    def __init__(self, tmp_path, hoy=HOY, **extra):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({**AJUSTES, **extra}, desde=DESDE_SIEMPRE)
        self.ajustes.anadir_carrera("Triatlón de prueba", "2026-10-25", 1)
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.store = DataStore(self.db)
        self.cierres = Cierres(self.db, ajustes=self.ajustes, pesos=self.pesos, objetivos=self.objetivos,
                               store=self.store, registro=self.registro)
        self.hoy = hoy
        self.cliente = TestClient(crear_app(self.registro, MagicMock(), self.ajustes, hoy=lambda: self.hoy,
                                            pesos=self.pesos, objetivos=self.objetivos, store=self.store,
                                            cierres=self.cierres))

    def comer(self, dia, kcal, nombre="Comida", *, planeado=False):
        alimento = Alimento(nombre=nombre, dia=dia, cantidad=None, unidad=None, kcal=kcal)
        hoy = dia - timedelta(days=1) if planeado else dia
        self.registro.guardar([alimento], hoy=hoy, texto=nombre, sin_cambios=True)

    def semana_de_prueba(self):
        """The table of the contract: Monday closed with Garmin (2,500 real, 2,400 estimated), Tuesday's food
        closed, today 760 eaten and open."""
        self.comer(LUNES, 2650)
        self.store.save_daily_stats(DailyStats(stat_date=LUNES, steps=20000, total_calories=2500))
        self.comer(MARTES, 2150)
        self.comer(HOY, 760)
        self.cierres.al_dia(HOY)

    def historial(self, desde, hasta=HOY):
        for i in range((hasta - desde).days + 1):
            dia = desde + timedelta(days=i)
            self.comer(dia, 2000 + (i % 7) * 100 - 300, "total del día (importado)")
            if i % 2 == 0:
                self.pesos.apuntar(dia, round(76 - i * 0.02, 1), hoy=HOY)
        self.cierres.al_dia(hasta)

    def get(self, ruta, estado=200):
        r = self.cliente.get(ruta)
        assert r.status_code == estado, r.text
        return r.json()

    def post(self, ruta, cuerpo, estado=200):
        r = self.cliente.post(ruta, json=cuerpo)
        assert r.status_code == estado, r.text
        return r.json()

    def filas_objetivo(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT dia, porcentaje FROM objetivo_dia ORDER BY dia").fetchall()


@pytest.fixture
def w(tmp_path):
    web = Web(tmp_path)
    web.semana_de_prueba()
    return web


# -- R1, R2: the week --------------------------------------------------------------------------------

def test_semana_de_prueba(w):  # R1 · R-24, R-25
    s = w.get("/api/balance/semana")
    assert (s["desde"], s["hasta"], s["hoy"], s["es_actual"]) == ("2026-10-19", "2026-10-25", "2026-10-21", True)
    f = {x["dia"]: x for x in s["filas"]}
    assert len(s["filas"]) == 7
    lunes, martes, hoy = f["2026-10-19"], f["2026-10-20"], f["2026-10-21"]
    assert (lunes["comido_kcal"], lunes["gasto_kcal"], lunes["gasto_origen"], lunes["estado"]) == (
        2650, 2500, "garmin", "cerrado_garmin")
    assert (lunes["balance_kcal"], lunes["balance_pct"], lunes["cuenta"]) == (150, 6.0, True)
    assert (martes["comido_kcal"], martes["gasto_kcal"], martes["gasto_origen"], martes["balance_pct"]) == (
        2150, 2000, "estimado", 7.5)
    assert (hoy["estado"], hoy["balance_kcal"], hoy["cuenta"]) == ("abierto", -1240, False)
    assert [(f[x]["objetivo_pct"], f[x]["motivo"]) for x in ("2026-10-22", "2026-10-24", "2026-10-25")] == [
        (-10, "ajuste"), (0, "carrera Triatlón de prueba"), (0, "carrera Triatlón de prueba")]
    assert (s["acumulado_kcal"], s["por_encima_kcal"]) == (300, 750)
    assert (s["semana"]["dias"], s["semana"]["balance_medio_kcal"], s["semana"]["balance_pct"]) == (2, 150, 6.7)
    assert s["reparto_posible"] is True and s["volver_al_plan"] is False
    assert "alimentos" not in str(s)


def test_semana_pasada_no_reparte(w):  # R6, R8
    s = w.get("/api/balance/semana?fecha=2026-10-14")
    assert (s["desde"], s["es_actual"]) == ("2026-10-12", False)
    assert "reparto_posible" not in s or s["reparto_posible"] is None


def test_ver_la_semana_no_cambia_nada(w):  # R2 · R-19, C-19, G-21
    antes = w.filas_objetivo()
    w.get("/api/balance/semana")
    w.get("/api/balance?rango=4s")
    w.get("/api/balance/semanas")
    w.post("/api/balance/repartir", {"aplicar": False})
    assert w.filas_objetivo() == antes == []
    dia = w.get("/api/dia")
    assert dia["objetivo"]["porcentaje"] == -10 and dia["objetivo"]["kcal"] == 1800
    assert next(m["kcal"] for m in dia["margenes"] if m["nivel"] == -10) == 1040  # the plan's level, unmarked


def test_el_lunes_siguiente_empieza_de_cero(w):  # R2 · R-19
    w.hoy = date(2026, 10, 26)
    s = w.get("/api/balance/semana")
    assert s["desde"] == "2026-10-26" and s["acumulado_kcal"] == 0 and s["por_encima_kcal"] == 0
    assert s["reparto_posible"] == "Vas en tu objetivo o por debajo esta semana: no hay nada que repartir"


# -- R3, R4: spread, apply, back to the plan ----------------------------------------------------------

def test_proponer_no_guarda(w):  # R3 · R-20, C-20, G-22
    p = w.post("/api/balance/repartir", {"aplicar": False})["propuesta"]
    assert [(x["dia"], x["nuevo_pct"], x["kcal"], x["objetivo_kcal"]) for x in p["dias"]] == [
        ("2026-10-21", -22.5, 250, 1550), ("2026-10-22", -22.5, 250, 1550), ("2026-10-23", -22.5, 250, 1550)]
    assert p["texto"].startswith("Llevas 750 kcal por encima de tu objetivo esta semana. Para compensarlo: "
                                 "miércoles 21, jueves 22 y viernes 23 a −22,5 % (1.550 kcal; −250 cada uno). "
                                 "Sábado 24 y domingo 25 no cambian: mantenimiento por el Triatlón de prueba")
    assert w.filas_objetivo() == []


def test_aplicar(w):  # R4 · R-20, C-20
    p = w.post("/api/balance/repartir", {"aplicar": False})["propuesta"]
    r = w.post("/api/balance/repartir", {"aplicar": True, "propuesta": p})
    assert w.filas_objetivo() == [("2026-10-21", -22.5), ("2026-10-22", -22.5), ("2026-10-23", -22.5)]
    f = {x["dia"]: x for x in r["semana"]["filas"]}
    assert [(f[d]["objetivo_pct"], f[d]["objetivo_origen"]) for d in ("2026-10-21", "2026-10-22", "2026-10-23")] == [
        (-22.5, "dia")] * 3
    assert f["2026-10-24"]["objetivo_pct"] == 0
    assert r["semana"]["volver_al_plan"] is True
    dia = w.get("/api/dia")
    assert dia["objetivo"]["porcentaje"] == -22.5 and dia["objetivo"]["kcal"] == 1550
    assert next(m["kcal"] for m in dia["margenes"] if m.get("objetivo")) == 790
    assert r["dia"]["objetivo"]["kcal"] == 1550


def test_aplicar_con_un_cambio_en_medio_no_guarda(w):  # R4
    p = w.post("/api/balance/repartir", {"aplicar": False})["propuesta"]
    w.comer(MARTES, 100, "Olvido")
    r = w.post("/api/balance/repartir", {"aplicar": True, "propuesta": p}, estado=409)
    assert w.filas_objetivo() == []
    assert r["propuesta"]["por_encima_kcal"] == 850 and r["propuesta"] != p
    assert "error" in r


def test_aplicar_sin_propuesta_es_400(w):  # R4
    w.post("/api/balance/repartir", {"aplicar": True}, estado=400)
    assert w.filas_objetivo() == []


def test_volver_al_plan(w):  # R4
    p = w.post("/api/balance/repartir", {"aplicar": False})["propuesta"]
    w.post("/api/balance/repartir", {"aplicar": True, "propuesta": p})
    w.post("/api/balance/volver-al-plan", {}, estado=400)
    assert [x[1] for x in w.filas_objetivo()] == [-22.5] * 3
    r = w.post("/api/balance/volver-al-plan", {"confirmado": True})
    assert [x[1] for x in w.filas_objetivo()] == [None] * 3
    assert w.get("/api/dia")["objetivo"]["porcentaje"] == -10
    assert r["semana"]["volver_al_plan"] is False


def test_nada_que_repartir_y_sin_dias(tmp_path):  # R6 · R-19, R-20
    w = Web(tmp_path)
    w.comer(LUNES, 1800)  # just on its objective
    w.comer(MARTES, 1700)  # under it
    w.comer(HOY, 500)
    w.cierres.al_dia(HOY)
    texto = "Vas en tu objetivo o por debajo esta semana: no hay nada que repartir"
    assert w.get("/api/balance/semana")["reparto_posible"] == texto
    assert w.post("/api/balance/repartir", {"aplicar": False})["propuesta"]["texto"] == texto
    w2 = Web(tmp_path / "otra", hoy=date(2026, 10, 25))
    w2.comer(LUNES, 2650)
    w2.comer(date(2026, 10, 25), 2000)
    w2.cierres.al_dia(date(2026, 10, 25))
    w2.cierres.cerrar_comida(date(2026, 10, 25), "boton")  # Sunday with its food closed
    assert w2.get("/api/balance/semana")["reparto_posible"] == (
        "No quedan días esta semana para repartirlo; la semana que viene empieza de cero")


# -- R7, R8, R9: chart, list of weeks, what travels ----------------------------------------------------

@pytest.fixture
def historial(tmp_path):
    web = Web(tmp_path)
    web.historial(date(2026, 6, 1))
    return web


def test_rangos(historial):  # R7
    cuatro = historial.get("/api/balance?rango=4s")
    assert (cuatro["desde"], cuatro["hasta"], cuatro["agrupado"], len(cuatro["barras"])) == (
        "2026-09-24", "2026-10-21", "dia", 28)
    assert cuatro["barras"][-1]["provisional"] is True
    tres = historial.get("/api/balance?rango=3m")
    assert (tres["desde"], tres["agrupado"], len(tres["barras"])) == ("2026-07-23", "dia", 91)
    todo = historial.get("/api/balance?rango=todo")
    assert (todo["desde"], todo["agrupado"], len(todo["barras"])) == ("2026-06-01", "semana", 21)
    assert todo["medias"] and todo["objetivo"] is None


def test_rango_desde_agosto(tmp_path):  # R7
    w = Web(tmp_path)
    w.historial(date(2026, 8, 3))
    for rango in ("3m", "todo"):
        s = w.get(f"/api/balance?rango={rango}")
        assert (s["desde"], s["agrupado"], len(s["barras"])) == ("2026-08-03", "dia", 80)


def test_semanas(tmp_path):  # R8
    w = Web(tmp_path)
    for i in range(5):  # the week of 12 Oct: 5 days closed with Garmin at 2,400, 1,980 eaten
        dia = date(2026, 10, 12) + timedelta(days=i)
        w.comer(dia, 1980)
        w.store.save_daily_stats(DailyStats(stat_date=dia, steps=20000, total_calories=2400))
    w.pesos.apuntar(date(2026, 10, 13), 72.6, hoy=HOY)
    w.pesos.apuntar(date(2026, 10, 15), 73.0, hoy=HOY)
    w.semana_de_prueba()
    lista = w.get("/api/balance/semanas")["semanas"]
    assert [s["desde"] for s in lista] == ["2026-10-19", "2026-10-12"]
    assert lista[1] == {"desde": "2026-10-12", "hasta": "2026-10-18", "dias": 5, "acumulado_kcal": -2100,
                        "balance_medio_kcal": -420, "balance_pct": -17.5, "peso_medio_kg": 72.8}
    assert lista[0]["peso_medio_kg"] is None


def test_lo_que_viaja_y_cuanto_tarda(historial):  # R9 · Q-3
    for ruta in ("/api/balance?rango=todo", "/api/balance/semanas"):
        inicio = time.perf_counter()
        r = historial.cliente.get(ruta)
        segundos = time.perf_counter() - inicio
        assert r.status_code == 200
        assert "alimentos" not in r.text and "importado" not in r.text and "nombre" not in r.text
        assert segundos < 2, f"{ruta}: {segundos:.2f} s"


@pytest.mark.parametrize("ruta", ["/api/balance?rango=1a", "/api/balance", "/api/balance/semana?fecha=2026-13-01",
                                  "/api/balance/semana?fecha=ayer"])
def test_rango_o_fecha_mal_es_400(w, ruta):  # R9
    r = w.cliente.get(ruta)
    assert r.status_code == 400 and "error" in r.json()


def test_sin_datos(tmp_path):  # R9
    w = Web(tmp_path)
    assert w.get("/api/balance?rango=4s")["vacio"] is True
    assert w.get("/api/balance/semanas")["semanas"] == []
    assert w.get("/api/balance/semana")["vacio"] is True


# -- R12: /api/peso brings its bars ---------------------------------------------------------------------

def test_peso_trae_las_barras(w):  # R12
    for hace, kg in ((10, 73.0), (1, 72.6), (0, 72.4)):
        w.pesos.apuntar(HOY - timedelta(days=hace), kg, hoy=HOY)
    datos = w.get("/api/peso")
    assert [(b["dia"], b["balance_kcal"], b["provisional"]) for b in datos["barras"]] == [
        ("2026-10-19", 150, False), ("2026-10-20", 150, False), ("2026-10-21", -1240, True)]
    assert [p["kg"] for p in datos["pesos"]] == [73.0, 72.6, 72.4]
    assert set(datos) >= {"hasta", "dias", "pesos", "medias", "objetivo", "barras"}
