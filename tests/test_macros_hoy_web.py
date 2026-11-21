"""Unit 099 (REC-43 of `consultar-margen`): the macros in GET /api/hoy and in the context of the team.

Plans: R-276, R-277, R-279 · C-341, C-346, C-350. R4, R5 and R7 of the unit. The card's data in /api/hoy (`hoy`,
`ayer`, `semanas`) with the `comida` block as before, and the same figures in `contexto.montar` (what the coach reads
with `get_contexto`). Every value is invented; nothing calls Garmin or any outside service.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from tridata.conversaciones.contexto import INSTRUCCIONES, montar
from tridata.models import Activity
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.plan import Planes
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 10, 21)  # a Wednesday
AYER = HOY - timedelta(days=1)


class Entorno:
    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                             desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.store = DataStore(self.db)
        self.planes = Planes(self.db)
        self.cliente = TestClient(crear_app(self.registro, MagicMock(), self.ajustes, hoy=lambda: HOY,
                                            pesos=self.pesos, objetivos=self.objetivos, store=self.store,
                                            ahora=lambda: datetime(2026, 10, 21, 10, 0)))

    def comer(self, dia: date, kcal: float, p=None, h=None, g=None) -> None:
        self.registro.guardar([Alimento(nombre="comida inventada", dia=dia, cantidad=1, unidad="ración", kcal=kcal,
                                        proteina_g=p, hidratos_g=h, grasa_g=g)],
                              hoy=HOY, texto="comida inventada", sin_cambios=True)

    def dia_con_macros(self) -> None:
        """70 kg; yesterday 1 h 30 of bike with 150 g P, 380 g H and 25 % fat (C-346); today 95 g P so far."""
        self.pesos.apuntar(AYER - timedelta(days=3), 70, hoy=HOY)
        self.store.save_activities([Activity(activity_id="bici-ayer", activity_date=AYER,
                                             activity_type="road_biking", name="Bici inventada",
                                             duration_seconds=5400)])
        self.comer(AYER, 2000, 150, 380, 2000 * 0.25 / 9)
        self.comer(HOY, 1000, 95, 120, 1000 * 0.22 / 9)

    def contexto(self) -> dict:
        ctx = montar(registro=self.registro, pesos=self.pesos, ajustes=self.ajustes, store=self.store,
                     transcripciones=[], aviso_medico=None, hoy=HOY, planes=self.planes)
        return json.loads(ctx.bloques[-1])


# -- R4, R5 · R-276, R-277: /api/hoy brings the card's data; Comida as before ---------------------------------------

def test_api_hoy_trae_macros_de_hoy_ayer_y_semanas(tmp_path):  # R4 · C-346
    e = Entorno(tmp_path)
    e.dia_con_macros()
    antes = e.cliente.get("/api/dia").json()
    h = e.cliente.get("/api/hoy").json()
    m = h["macros"]
    assert set(m) == {"hoy", "ayer", "semanas"}
    hoy, ayer = m["hoy"], m["ayer"]
    assert hoy["fecha"] == HOY.isoformat() and ayer["fecha"] == AYER.isoformat()
    assert hoy["proteina"]["g"] == 95 and hoy["proteina"]["objetivo_g"] == [112, 140]
    assert hoy["hidratos"]["objetivo_g"] == [210, 350]  # today: no training, no session
    assert hoy["grasa"]["pct_kcal"] == 22 and hoy["grasa"]["objetivo_pct_min"] == 20
    assert all(hoy[x]["veredicto"] is None for x in ("proteina", "hidratos", "grasa"))  # today: «llevas»
    assert ayer["horas"] == 1.5 and ayer["hidratos"]["objetivo_g"] == [420, 700]
    assert (ayer["proteina"]["veredicto"], ayer["hidratos"]["veredicto"], ayer["grasa"]["veredicto"]) == (
        "alto", "bajo", "bien")
    assert len(m["semanas"]) == 8 and m["semanas"][-1]["lunes"] == "2026-10-19"
    assert m["semanas"][-1]["proteina"] == {"media_g_kg": 2.1, "en_rango": 0, "dias": 1}
    # the Comida block is the same as before: its keys and figures from the same day as /api/dia
    assert set(h["comida"]) == {"total_kcal", "nivel", "queda_kcal", "objetivo_kcal", "pct", "planeado_kcal"}
    assert h["comida"]["total_kcal"] == antes["total_kcal"] == 1000


def test_api_hoy_aproximado_sin_veredicto(tmp_path):  # R5 · C-341
    e = Entorno(tmp_path)
    e.pesos.apuntar(AYER, 70, hoy=HOY)
    e.comer(AYER, 1300, 150, 380, 40)
    e.comer(AYER, 700)
    e.comer(HOY, 1300, 100, 150, 40)
    e.comer(HOY, 700)
    m = e.cliente.get("/api/hoy").json()["macros"]
    assert m["hoy"]["estado"] == "aproximado" and m["hoy"]["aviso"] == "aproximado: macros del 65 % de las kcal"
    assert m["ayer"]["estado"] == "aproximado"
    assert all(m["ayer"][x]["veredicto"] is None for x in ("proteina", "hidratos", "grasa"))
    assert m["semanas"][-1]["sin_macros"] is True  # no day with a verdict this week


def test_api_hoy_sin_nada(tmp_path):  # R2, R5: no weight and nothing eaten
    m = Entorno(tmp_path).cliente.get("/api/hoy").json()["macros"]
    assert m["hoy"]["estado"] == "sin_macros" and m["hoy"]["aviso"] == "sin macros"
    assert m["hoy"]["aviso_peso"] == "apunta tu peso para verlo en gramos"
    assert m["hoy"]["proteina"]["objetivo_g"] is None and m["hoy"]["proteina"]["objetivo_g_kg"] == [1.6, 2.0]
    assert all(s["sin_macros"] for s in m["semanas"])


# -- R7 · R-279, C-350: the same in the context of the team --------------------------------------------------------

def test_el_contexto_lleva_los_macros_del_dia_y_el_bloque_macros(tmp_path):  # R7 · C-350
    e = Entorno(tmp_path)
    e.dia_con_macros()
    d = e.contexto()
    dias = {x["fecha"]: x for x in d["dias"]}
    assert dias[HOY.isoformat()]["macros"] == {"proteina_g": 95, "hidratos_g": 120, "grasa_g": 24.4, "pct_kcal": 100}
    assert dias[AYER.isoformat()]["macros"]["proteina_g"] == 150
    assert "macros" not in dias[(HOY - timedelta(days=5)).isoformat()]  # a day without macros carries none
    m = d["macros"]
    assert "proteína 95 de 112-140 g" in m["hoy"]["texto"]
    assert m["hoy"]["proteina"]["objetivo_g"] == [112, 140]
    assert m["ayer"]["proteina"]["veredicto"] == "alto"
    assert len(m["semanas"]) == 8
    # the same figures as the card of Hoy
    assert m == {**e.cliente.get("/api/hoy").json()["macros"], "hoy": m["hoy"]}
    assert {k: v for k, v in m["hoy"].items() if k != "texto"} == e.cliente.get("/api/hoy").json()["macros"]["hoy"]


def test_las_instrucciones_dicen_que_es_macros(tmp_path):  # R7
    assert '"macros"' in INSTRUCCIONES
    assert not any(c.isdigit() for c in INSTRUCCIONES.split('"macros"', 1)[1].split("\n", 1)[0])  # G-38: no figures


def test_el_contexto_con_macros_se_repite_byte_a_byte(tmp_path):  # R7 · P3 of unit 032
    e = Entorno(tmp_path)
    e.dia_con_macros()
    uno = montar(registro=e.registro, pesos=e.pesos, ajustes=e.ajustes, store=e.store, transcripciones=[],
                 aviso_medico=None, hoy=HOY, planes=e.planes)
    dos = montar(registro=e.registro, pesos=e.pesos, ajustes=e.ajustes, store=e.store, transcripciones=[],
                 aviso_medico=None, hoy=HOY, planes=e.planes)
    assert uno.bloques == dos.bloques
