"""Unit 099 (REC-43 of `consultar-margen`): the macros of the day against their objective, and their 8 weeks.

Plans: R-274..R-278 · G-65 · C-338..C-341, C-344, C-346, C-347. R1-R6 of the unit. `nutricion/macros.py` alone,
without the web: the ranges by hours (and their edges), the hours (Garmin or the plan, the larger), the verdict,
the 80 % threshold (R-277, the criterion that carries the unit) and the weeks. Every value is invented.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from tridata.models import Activity
from tridata.nutricion import Alimento, RegistroComidas, RegistroPeso
from tridata.nutricion.macros import (
    AVISO_SIN_PESO,
    del_dia,
    franja_hidratos,
    horas_de_entreno,
    objetivo,
    semanas,
    vista,
)
from tridata.plan import Planes
from tridata.storage import DataStore

HOY = date(2026, 10, 21)  # a Wednesday
AYER = HOY - timedelta(days=1)
AHORA = datetime(2026, 10, 21, 9, 0)


def _comido(kcal, p=None, h=None, g=None, estado="comido") -> dict:
    """One logged food as `RegistroComidas.apuntes_del_dia` gives it (only what the macros read)."""
    return {"kcal": kcal, "proteina_g": p, "hidratos_g": h, "grasa_g": g, "estado": estado}


# -- R1 · R-274, G-65, C-338, C-339: the ranges by hours, and their edges ------------------------------------------

@pytest.mark.parametrize(("horas", "franja"), [
    (0, (3, 5)), (0.5, (5, 7)), (1, (6, 10)), (1.5, (6, 10)), (3, (6, 10)), (3.5, (8, 12)),
], ids=["sin-entreno", "media-hora", "una-hora", "hora-y-media", "tres-horas", "tres-y-media"])
def test_franja_de_hidratos_por_horas(horas, franja):  # R1 · G-65: from 1 to 3 h both included
    assert franja_hidratos(horas) == franja


def test_objetivo_con_70_kg_y_hora_y_media_de_bici():  # R1 · C-338
    o = objetivo(70, 1.5)
    assert o["proteina"] == {"g_kg": [1.6, 2.0], "g": [112, 140]}
    assert o["hidratos"] == {"g_kg": [6, 10], "g": [420, 700]}
    assert o["grasa"]["pct_min"] == 20


def test_objetivo_con_70_kg_sin_entreno():  # R1 · C-339
    o = objetivo(70, 0)
    assert o["hidratos"]["g"] == [210, 350]
    assert o["proteina"]["g"] == [112, 140]


def test_sin_peso_solo_g_kg_y_sin_veredicto_de_proteina_ni_hidratos():  # R2 · C-347
    o = objetivo(None, 1.5)
    assert o["proteina"] == {"g_kg": [1.6, 2.0], "g": None}
    assert o["hidratos"] == {"g_kg": [6, 10], "g": None}
    d = del_dia([_comido(2000, 150, 380, 2000 * 0.25 / 9)], None, 1.5, es_hoy=False)
    assert d["aviso_peso"] == AVISO_SIN_PESO == "apunta tu peso para verlo en gramos"
    assert d["proteina"]["veredicto"] is None and d["hidratos"]["veredicto"] is None
    assert d["proteina"]["g_kg"] is None and d["proteina"]["objetivo_g"] is None
    assert d["grasa"]["veredicto"] == "bien"  # the fat has its verdict without a weight


# -- R4 · R-276, C-346: the verdict of a past day ------------------------------------------------------------------

def test_veredicto_de_ayer_alto_bajo_bien():  # R4 · C-346
    d = del_dia([_comido(2000, 150, 380, 2000 * 0.25 / 9)], 70, 1.5, es_hoy=False)
    assert d["estado"] == "completo"
    assert (d["proteina"]["veredicto"], d["hidratos"]["veredicto"], d["grasa"]["veredicto"]) == ("alto", "bajo", "bien")
    assert d["grasa"]["pct_kcal"] == 25
    assert d["proteina"]["g"] == 150 and d["hidratos"]["g"] == 380


def test_veredicto_en_rango_y_grasa_baja():  # R4
    d = del_dia([_comido(2000, 120, 500, 2000 * 0.15 / 9)], 70, 1.5, es_hoy=False)
    assert d["proteina"]["veredicto"] == "en_rango" and d["hidratos"]["veredicto"] == "en_rango"
    assert d["grasa"]["veredicto"] == "bajo"


def test_hoy_dice_llevas_sin_veredicto():  # R4 · R-276: during the day, «llevas»
    d = del_dia([_comido(2000, 150, 380, 2000 * 0.25 / 9)], 70, 1.5, es_hoy=True)
    assert d["estado"] == "completo"
    assert all(d[m]["veredicto"] is None for m in ("proteina", "hidratos", "grasa"))
    assert d["proteina"]["g"] == 150 and d["proteina"]["objetivo_g"] == [112, 140]


# -- R5 · R-277, C-341: the 80 % threshold (the criterion that carries the unit) -----------------------------------

def test_menos_del_80_por_ciento_es_aproximado_y_sin_veredicto():  # R5 · C-341 (R-277)
    apuntes = [_comido(1300, 150, 380, 40), _comido(700),  # 2000 kcal eaten, 1300 with macros: 65 %
               _comido(500, 30, 60, 10, estado="planeado")]  # what is planned never counts
    d = del_dia(apuntes, 70, 1.5, es_hoy=False)
    assert d["kcal_comidas"] == 2000 and d["kcal_con_macros"] == 1300
    assert d["pct_kcal_con_macros"] == 65
    assert d["estado"] == "aproximado"
    assert d["aviso"] == "aproximado: macros del 65 % de las kcal"
    assert all(d[m]["veredicto"] is None for m in ("proteina", "hidratos", "grasa"))
    assert d["proteina"]["g"] == 150  # only what is eaten: the planned 30 g do not add


def test_justo_el_80_por_ciento_si_tiene_veredicto():  # R5: below 80 % is «aproximado»; 80 % is not
    d = del_dia([_comido(1600, 150, 380, 1600 * 0.25 / 9), _comido(400)], 70, 1.5, es_hoy=False)
    assert d["pct_kcal_con_macros"] == 80 and d["estado"] == "completo"
    assert d["proteina"]["veredicto"] == "alto"


def test_un_alimento_con_solo_un_macro_no_cuenta_como_con_macros():  # R5: «con sus tres macros»
    d = del_dia([_comido(1000, 80, 200, 30), _comido(1000, 50, None, None)], 70, 1.5, es_hoy=False)
    assert d["pct_kcal_con_macros"] == 50 and d["estado"] == "aproximado"
    assert d["proteina"]["g"] == 80


def test_sin_nada_con_macros_dice_sin_macros():  # R5
    d = del_dia([_comido(1800)], 70, 0, es_hoy=False)
    assert d["estado"] == "sin_macros" and d["aviso"] == "sin macros"
    assert all(d[m]["veredicto"] is None for m in ("proteina", "hidratos", "grasa"))
    nada = del_dia([], 70, 0, es_hoy=True)
    assert nada["estado"] == "sin_macros" and nada["aviso"] == "sin macros"


# -- R3 · R-275, C-340: the hours, Garmin or the plan, the larger ---------------------------------------------------

class Base:
    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.store = DataStore(self.db)
        self.planes = Planes(self.db)

    def plan(self, sesiones: list[dict]) -> None:
        self.planes.crear({"nombre": "Plan inventado", "apartados": [{"titulo": "Semanas", "parte": "previsto"}],
                           "carreras": [{"apartado": 0, "nombre": "Carrera inventada", "fecha": "2026-12-06",
                                         "objetivo": "terminar", "estado": "pendiente", "principal": True}],
                           "semanas": [], "nutricion": [], "incidencias": [], "notas": [], "sesiones": sesiones},
                          AHORA)

    def actividad(self, ident: str, dia: date, minutos: float) -> None:
        self.store.save_activities([Activity(activity_id=ident, activity_date=dia, activity_type="road_biking",
                                             name="Bici inventada", duration_seconds=minutos * 60)])

    def comer(self, dia: date, kcal: float, p: float | None, h: float | None, g: float | None) -> None:
        self.registro.guardar([Alimento(nombre="comida inventada", dia=dia, cantidad=1, unidad="ración", kcal=kcal,
                                        proteina_g=p, hidratos_g=h, grasa_g=g)],
                              hoy=HOY, texto="comida inventada", sin_cambios=True)


def _sesion(fecha: date, bloques: list[str]) -> dict:
    return {"apartado": 0, "fecha": fecha.isoformat(), "titulo": "Rodaje inventado", "bloques": bloques,
            "clave": False}


def test_dos_horas_en_el_plan_sin_hacer_cuentan_desde_la_manana(tmp_path):  # R3 · C-340
    b = Base(tmp_path)
    b.plan([_sesion(HOY, ["2h suave"])])
    assert horas_de_entreno(b.store, b.planes, HOY) == 2
    b.pesos.apuntar(HOY, 70, hoy=HOY)
    hoy = vista(registro=b.registro, pesos=b.pesos, store=b.store, planes=b.planes, hoy=HOY)["hoy"]
    assert hoy["horas"] == 2 and hoy["hidratos"]["objetivo_g"] == [420, 700]


def test_la_mayor_de_garmin_y_el_plan(tmp_path):  # R3
    b = Base(tmp_path)
    b.plan([_sesion(HOY, ["2h suave"])])
    b.actividad("b1", HOY, 90)
    b.actividad("b2", HOY, 60)  # 2 h 30 done: more than the 2 h of the plan
    assert horas_de_entreno(b.store, b.planes, HOY) == 2.5
    b.actividad("b3", AYER, 30)
    assert horas_de_entreno(b.store, b.planes, AYER) == 0.5


def test_las_sesiones_del_dia_se_suman_y_una_ilegible_cuenta_una_hora(tmp_path):  # R3 · C-270
    b = Base(tmp_path)
    b.plan([_sesion(HOY, ["2h suave"]), _sesion(HOY, ["algo que no se puede leer"]), _sesion(AYER, ["2h suave"])])
    assert horas_de_entreno(b.store, b.planes, HOY) == 3


def test_sin_plan_ni_garmin_cero_horas(tmp_path):  # R3
    b = Base(tmp_path)
    assert horas_de_entreno(b.store, b.planes, HOY) == 0
    assert horas_de_entreno(None, None, HOY) == 0


def test_el_peso_es_el_ultimo_hasta_ese_dia(tmp_path):  # R1: «con su último peso apuntado hasta ese día»
    b = Base(tmp_path)
    b.pesos.apuntar(HOY - timedelta(days=5), 70, hoy=HOY)
    b.pesos.apuntar(HOY, 80, hoy=HOY)
    v = vista(registro=b.registro, pesos=b.pesos, store=b.store, planes=b.planes, hoy=HOY)
    assert v["hoy"]["kg"] == 80 and v["ayer"]["kg"] == 70
    assert v["ayer"]["proteina"]["objetivo_g"] == [112, 140]


# -- R6 · R-278, C-344: the 8 weeks --------------------------------------------------------------------------------

def test_ocho_semanas_tres_con_macros(tmp_path):  # R6 · C-344
    b = Base(tmp_path)
    b.pesos.apuntar(HOY - timedelta(days=60), 70, hoy=HOY)
    lunes = HOY - timedelta(days=HOY.weekday())  # 2026-10-19
    # two weeks back, 6 days with macros, protein 1.7 g/kg (119 g) in 5 of them, 2.5 g/kg one day; fat 25 %
    hace_dos = lunes - timedelta(days=14)
    for i in range(6):
        b.comer(hace_dos + timedelta(days=i), 2000, 119 if i < 5 else 175, 300, 2000 * 0.25 / 9)
    # last week: 3 days with macros, one of them «aproximado» (no verdict, does not count)
    hace_una = lunes - timedelta(days=7)
    for i in range(2):
        b.comer(hace_una + timedelta(days=i), 2000, 119, 300, 2000 * 0.25 / 9)
    b.comer(hace_una + timedelta(days=2), 1000, 60, 150, 20)
    b.comer(hace_una + timedelta(days=2), 1000, None, None, None)
    # this week: Monday and Tuesday with macros, and today (Wednesday) too: today never counts
    for i in range(3):
        b.comer(lunes + timedelta(days=i), 2000, 119, 300, 2000 * 0.25 / 9)

    s = semanas(registro=b.registro, pesos=b.pesos, store=b.store, planes=b.planes, hoy=HOY)
    assert [x["lunes"] for x in s] == [(lunes - timedelta(weeks=7 - i)).isoformat() for i in range(8)]
    assert [x["sin_macros"] for x in s] == [True] * 5 + [False] * 3
    assert all(x["proteina"] is None and x["hidratos"] is None and x["grasa"] is None for x in s[:5])
    dos, una, esta = s[5], s[6], s[7]
    assert dos["proteina"] == {"media_g_kg": 1.8, "en_rango": 5, "dias": 6}  # (5 × 1.7 + 2.5) / 6
    assert dos["hidratos"] == {"media_g_kg": 4.3, "en_rango": 6, "dias": 6}  # 300 g / 70 kg, no training: 3-5
    assert dos["grasa"] == {"media_pct": 25, "en_rango": 6, "dias": 6}
    assert una["proteina"]["dias"] == 2 and una["proteina"]["en_rango"] == 2  # the «aproximado» day is out
    assert esta["proteina"]["dias"] == 2  # Monday and Tuesday; today is out


def test_semanas_sin_peso_solo_grasa(tmp_path):  # R6 with R2: no g/kg without a weight
    b = Base(tmp_path)
    b.comer(AYER, 2000, 119, 300, 2000 * 0.25 / 9)
    esta = semanas(registro=b.registro, pesos=b.pesos, store=b.store, planes=b.planes, hoy=HOY)[-1]
    assert esta["sin_macros"] is False
    assert esta["proteina"] is None and esta["hidratos"] is None
    assert esta["grasa"] == {"media_pct": 25, "en_rango": 1, "dias": 1}
