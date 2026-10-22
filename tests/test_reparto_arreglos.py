"""Unit 018: the fixes of unit 017's spread (its «Trabajo descubierto» and its reviewer).

R9 spreading again spreads only what is still pending · R10 «Volver al plan» leaves the maintenance days before a
race as they are · R11 «repartido» only on the days that come from the spread (a column `repartido` in
`objetivo_dia`, no new table). Plans: `consultar-margen` R-20, G-22. Unit level over `balance.semana` and
`ObjetivosDia`, integration with the `Web` helper of unit 017 (TestClient over a temporary SQLite). Every value
is invented. «Today» is Wednesday 2026-10-21; plan −10 %, healthy limit 25 %.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from test_balance_web import HOY, MARTES, Web
from tridata.nutricion import ObjetivosDia
from tridata.nutricion.balance import semana
from tridata.nutricion.margen import objetivo_del_dia

YA = "Lo que llevas por encima ya está repartido en los días que quedan."
JUEVES, VIERNES, SABADO, DOMINGO = (date(2026, 10, d) for d in (22, 23, 24, 25))


def _aplicar(w):
    p = w.post("/api/balance/repartir", {"aplicar": False})["propuesta"]
    return w.post("/api/balance/repartir", {"aplicar": True, "propuesta": p})


@pytest.fixture
def w(tmp_path):
    web = Web(tmp_path)
    web.semana_de_prueba()
    return web


# -- R9: spread again ----------------------------------------------------------------------------------------

def test_repartir_otra_vez_ya_esta_repartido(w):  # R9 · R-20
    _aplicar(w)
    s = w.get("/api/balance/semana")
    assert (s["por_encima_kcal"], s["ya_repartido_kcal"], s["pendiente_kcal"]) == (750, 750, 0)
    assert s["reparto_posible"] is True  # the button is still there, and says why it proposes nothing
    p = w.post("/api/balance/repartir", {"aplicar": False})["propuesta"]
    assert (p["motivo"], p["dias"], p["texto"]) == ("ya_repartido", [], YA)
    antes = w.filas_objetivo()
    w.post("/api/balance/repartir", {"aplicar": True, "propuesta": p}, estado=422)
    assert w.filas_objetivo() == antes


def test_un_olvido_reparte_solo_lo_pendiente(w):  # R9 · R-20, G-22
    _aplicar(w)
    w.comer(MARTES, 150, "Olvido")
    s = w.get("/api/balance/semana")
    assert (s["por_encima_kcal"], s["ya_repartido_kcal"], s["pendiente_kcal"]) == (900, 750, 150)
    p = w.post("/api/balance/repartir", {"aplicar": False})["propuesta"]
    assert [(x["dia"], x["antes_pct"], x["nuevo_pct"], x["kcal"], x["objetivo_kcal"]) for x in p["dias"]] == [
        ("2026-10-21", -22.5, -25, 50, 1500), ("2026-10-22", -22.5, -25, 50, 1500),
        ("2026-10-23", -22.5, -25, 50, 1500)]
    assert p["sin_repartir_kcal"] == 0
    assert "miércoles 21, jueves 22 y viernes 23 a −25 % (1.500 kcal; −50 cada uno)" in p["texto"]
    w.post("/api/balance/repartir", {"aplicar": True, "propuesta": p})
    assert w.filas_objetivo()[:3] == [("2026-10-21", -25), ("2026-10-22", -25), ("2026-10-23", -25)]
    assert w.get("/api/balance/semana")["pendiente_kcal"] == 0


def test_el_miercoles_cerrado_en_su_objetivo_cuenta_lo_compensado(w):  # R9
    _aplicar(w)
    w.comer(HOY, 790, "Cena")  # 760 + 790 = 1,550: just its spread objective
    w.cierres.cerrar_comida(HOY, "boton")
    s = w.get("/api/balance/semana")
    assert (s["por_encima_kcal"], s["ya_repartido_kcal"], s["pendiente_kcal"]) == (750, 750, 0)
    assert w.post("/api/balance/repartir", {"aplicar": False})["propuesta"]["motivo"] == "ya_repartido"


def test_la_primera_vez_igual_que_en_la_017(w):  # R9: nothing spread yet, the proposal of unit 017
    s = w.get("/api/balance/semana")
    assert (s["por_encima_kcal"], s["ya_repartido_kcal"], s["pendiente_kcal"]) == (750, 0, 750)
    p = w.post("/api/balance/repartir", {"aplicar": False})["propuesta"]
    assert p["texto"].startswith("Llevas 750 kcal por encima de tu objetivo esta semana. Para compensarlo: "
                                 "miércoles 21, jueves 22 y viernes 23 a −22,5 % (1.550 kcal; −250 cada uno).")


def _resumen(dia: date, comido: float, estado: str, pct: float, *, repartido: bool = False) -> dict:
    gasto = 2000
    return {"fecha": dia.isoformat(), "total_kcal": comido, "gasto": {"kcal": gasto, "origen": "estimado"},
            "objetivo": {"porcentaje": pct, "kcal": gasto * (100 + pct) / 100, "origen": "dia" if pct != -10 else "plan",
                         "plan_pct": -10, "repartido": repartido},
            "plan": {"objetivo_pct": -10, "motivo": "ajuste"},
            "cierre": {"estado": estado, "balance_kcal": comido - gasto, "balance_pct": 0, "semana": None}}


def test_semana_pura_ya_repartido_y_pendiente():  # R9 at unit level
    lunes = date(2026, 10, 19)
    dias = {lunes: _resumen(lunes, 2200, "comida_cerrada", -10),  # +400 above its 1,800
            MARTES: _resumen(MARTES, 0, "abierto", -15),  # by hand: not spread
            HOY: _resumen(HOY, 0, "abierto", -20, repartido=True),  # 10 % of 2,000 spread
            JUEVES: _resumen(JUEVES, 0, "abierto", -15, repartido=True)}  # 5 % of 2,000 spread
    s = semana(lunes, resumen=dias.get)
    assert (s["por_encima_kcal"], s["ya_repartido_kcal"], s["pendiente_kcal"]) == (400, 300, 100)
    filas = {f["dia"]: f for f in s["filas"]}
    assert filas[HOY.isoformat()]["repartido"] is True and filas[JUEVES.isoformat()]["repartido"] is True
    assert "repartido" not in filas[MARTES.isoformat()]


# -- R10: back to the plan and the maintenance days ------------------------------------------------------------

@pytest.fixture
def duatlon(tmp_path):
    """Duatlón on Thursday 22 with 1 maintenance day (Wednesday 21 and Thursday 22 at 0 %); −5 % by hand today;
    Friday to Sunday at −22,5 % «repartido»."""
    web = Web(tmp_path)
    for c in web.ajustes.vigentes(HOY).carreras:
        web.ajustes.quitar_carrera(c.id)
    web.ajustes.anadir_carrera("Duatlón de prueba", JUEVES.isoformat(), 1)
    web.semana_de_prueba()
    web.objetivos.cambiar(HOY, porcentaje=-5)
    for dia in (VIERNES, SABADO, DOMINGO):
        web.objetivos.cambiar(dia, porcentaje=-22.5, repartido=True)
    return web


def test_volver_al_plan_no_toca_el_mantenimiento(duatlon):  # R10 · R-20
    assert duatlon.get("/api/balance/semana")["volver_al_plan"] is True
    r = duatlon.post("/api/balance/volver-al-plan", {"confirmado": True})
    assert dict(duatlon.filas_objetivo()) == {"2026-10-21": -5, "2026-10-23": None, "2026-10-24": None,
                                               "2026-10-25": None}
    f = {x["dia"]: x for x in r["semana"]["filas"]}
    assert [f[d]["objetivo_pct"] for d in ("2026-10-23", "2026-10-24", "2026-10-25")] == [-10] * 3
    assert f["2026-10-21"]["objetivo_pct"] == -5
    assert r["semana"]["volver_al_plan"] is False  # the only day with its own % is a maintenance one


def test_sin_boton_si_solo_queda_el_de_mantenimiento(tmp_path):  # R10
    web = Web(tmp_path)
    for c in web.ajustes.vigentes(HOY).carreras:
        web.ajustes.quitar_carrera(c.id)
    web.ajustes.anadir_carrera("Duatlón de prueba", JUEVES.isoformat(), 1)
    web.semana_de_prueba()
    web.objetivos.cambiar(HOY, porcentaje=-5)
    assert web.get("/api/balance/semana")["volver_al_plan"] is False


# -- R11: «repartido» only on the days of the spread ----------------------------------------------------------

def test_objetivos_dia_columna_repartido(tmp_path):  # R11
    o = ObjetivosDia(tmp_path / "d.sqlite3")
    assert o.cambiar(HOY, porcentaje=-22.5, repartido=True)["repartido"] is True
    assert o.cambiar(HOY, niveles=[-10, -20])["repartido"] is True  # the % did not change
    assert o.cambiar(HOY, porcentaje=-15)["repartido"] is False
    o.cambiar(JUEVES, porcentaje=-22.5, repartido=True)
    assert o.guardar(JUEVES, {"porcentaje": -20})  # saying it again by hand
    assert o.del_dia(JUEVES)["repartido"] is False
    o.cambiar(VIERNES, porcentaje=-22.5, repartido=True)
    assert o.cambiar(VIERNES, porcentaje=None)["repartido"] is False


def test_la_tabla_de_antes_gana_la_columna_una_vez(tmp_path):  # R11: no new table, old rows unmarked
    db = tmp_path / "d.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE objetivo_dia (dia TEXT PRIMARY KEY, tipo TEXT, porcentaje REAL, niveles TEXT, "
                     "entreno TEXT, pasos INTEGER, creado TEXT NOT NULL, previsto TEXT)")
        conn.execute("INSERT INTO objetivo_dia (dia, porcentaje, creado) VALUES ('2026-10-20', -22.5, 'x')")
    ObjetivosDia(db)
    o = ObjetivosDia(db)
    with sqlite3.connect(db) as conn:
        columnas = [c[1] for c in conn.execute("PRAGMA table_info(objetivo_dia)")]
        tablas = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    assert columnas.count("repartido") == 1 and tablas == ["objetivo_dia"]
    assert o.del_dia(MARTES)["repartido"] is False


def test_objetivo_del_dia_dice_repartido():  # R11 · margen
    assert objetivo_del_dia({"porcentaje": -22.5, "repartido": True}, {"objetivo_pct": -10})["repartido"] is True
    assert objetivo_del_dia({"porcentaje": -15, "repartido": False}, {"objetivo_pct": -10})["repartido"] is False
    assert objetivo_del_dia(None, {"objetivo_pct": -10})["repartido"] is False


def test_la_etiqueta_solo_en_los_dias_del_reparto(w):  # R11
    w.post("/api/dia/objetivo", {"porcentaje": -15})
    f = {x["dia"]: x for x in w.get("/api/balance/semana")["filas"]}
    assert f["2026-10-21"]["objetivo_pct"] == -15 and not f["2026-10-21"].get("repartido")
    assert w.get("/api/dia")["objetivo"]["repartido"] is False

    w.post("/api/dia/objetivo", {"porcentaje": None})
    _aplicar(w)
    f = {x["dia"]: x for x in w.get("/api/balance/semana")["filas"]}
    assert [f[d].get("repartido") for d in ("2026-10-21", "2026-10-22", "2026-10-23")] == [True] * 3
    assert w.get("/api/dia")["objetivo"]["repartido"] is True

    w.post("/api/dia/objetivo", {"porcentaje": -20})  # by hand after the spread: today loses it
    f = {x["dia"]: x for x in w.get("/api/balance/semana")["filas"]}
    assert not f["2026-10-21"].get("repartido") and f["2026-10-22"]["repartido"] is True
