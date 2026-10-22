"""Unit 017: the week, the chart series, the list of weeks and the proposal to spread (nutricion/balance.py).

Plans: `cerrar-dia` R-24, R-25 and the surface «Historial y gráfica»; `consultar-margen` R-19, R-20, C-20,
G-22. Pure functions over invented day summaries (the shape `resumen_completo` gives); every value is
invented. «Today» is Wednesday 2026-10-21; plan −10 %, healthy limit 25 %, a race on Sunday 25 with one
maintenance day (Saturday 24 and Sunday 25 at 0 %).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.nutricion.balance import (
    BARRAS_POR_DIA_MAX, dias_que_quedan, fila_del_dia, proponer_reparto, semana, semanas, serie, texto_reparto,
)

HOY = date(2026, 10, 21)  # a Wednesday
LUNES = date(2026, 10, 19)


def d(n: int) -> date:
    return LUNES + timedelta(days=n)


def resumen_de_prueba(dia: date, *, comido: float, gasto: float, estado: str, objetivo_pct: float = -10,
                      origen_gasto: str = "estimado", motivo: str | None = "ajuste", origen_objetivo: str = "plan",
                      semana_014: dict | None = None) -> dict:
    """The few keys of a `resumen_completo` the balance reads, plus food it must never copy."""
    kcal = round(comido - gasto, 1)
    return {
        "fecha": dia.isoformat(),
        "alimentos": [{"nombre": "Lentejas de prueba", "kcal": comido}],
        "total_kcal": comido,
        "gasto": {"kcal": gasto, "origen": origen_gasto},
        "objetivo": {"porcentaje": objetivo_pct, "kcal": round(gasto * (100 + objetivo_pct) / 100, 1),
                     "origen": origen_objetivo},
        "plan": {"objetivo_pct": objetivo_pct, "motivo": motivo},
        "cierre": {"estado": estado, "balance_kcal": kcal, "balance_pct": round(kcal / gasto * 100, 1),
                   "semana": semana_014 or {"desde": "2026-10-19", "hasta": "2026-10-25", "dias": 2,
                                            "balance_medio_kcal": 150.0, "balance_pct": 6.7, "texto": "…"}},
    }


def semana_de_prueba() -> dict[date, dict]:
    """The test week of the contract (R1): Monday closed with Garmin, Tuesday food closed, today open."""
    dias = {
        d(0): resumen_de_prueba(d(0), comido=2650, gasto=2500, estado="cerrado_garmin", origen_gasto="garmin"),
        d(1): resumen_de_prueba(d(1), comido=2150, gasto=2000, estado="comida_cerrada"),
        d(2): resumen_de_prueba(d(2), comido=760, gasto=2000, estado="abierto"),
        d(3): resumen_de_prueba(d(3), comido=0, gasto=2000, estado="abierto"),
        d(4): resumen_de_prueba(d(4), comido=0, gasto=2000, estado="abierto"),
        d(5): resumen_de_prueba(d(5), comido=0, gasto=2000, estado="abierto", objetivo_pct=0,
                                motivo="carrera Triatlón de prueba"),
        d(6): resumen_de_prueba(d(6), comido=0, gasto=2000, estado="abierto", objetivo_pct=0,
                                motivo="carrera Triatlón de prueba"),
    }
    return dias


# -- R1, R2: the week ---------------------------------------------------------------------------------

def test_fila_del_dia_solo_totales():  # R1, R9
    fila = fila_del_dia(semana_de_prueba()[d(0)])
    assert fila == {"dia": "2026-10-19", "estado": "cerrado_garmin", "comido_kcal": 2650, "gasto_kcal": 2500,
                    "gasto_origen": "garmin", "balance_kcal": 150, "balance_pct": 6.0, "objetivo_pct": -10,
                    "objetivo_kcal": 2250, "objetivo_origen": "plan", "motivo": "ajuste", "cuenta": True}
    assert "alimentos" not in fila


def test_semana_suma_solo_los_dias_cerrados():  # R1, R2 · R-24, R-25
    dias = semana_de_prueba()
    s = semana(LUNES, resumen=dias.get)
    assert (s["desde"], s["hasta"]) == ("2026-10-19", "2026-10-25")
    assert [f["dia"] for f in s["filas"]] == [d(i).isoformat() for i in range(7)]
    assert [f["cuenta"] for f in s["filas"]] == [True, True, False, False, False, False, False]
    assert s["acumulado_kcal"] == 300
    assert s["por_encima_kcal"] == 750  # (2650 − 2250) + (2150 − 1800); today does not count
    assert s["semana"] == dias[d(0)]["cierre"]["semana"]  # the mean of unit 014, as it is
    assert "alimentos" not in str(s)


def test_semana_cualquier_dia_da_su_lunes():  # R1
    dias = semana_de_prueba()
    assert semana(d(3), resumen=dias.get)["desde"] == "2026-10-19"


def test_semana_nueva_empieza_de_cero():  # R2 · R-19, G-21
    s = semana(date(2026, 10, 26), resumen=lambda dia: resumen_de_prueba(dia, comido=0, gasto=2000, estado="abierto"))
    assert s["acumulado_kcal"] == 0 and s["por_encima_kcal"] == 0


def test_semana_sin_ajustes_en_un_dia():  # R1
    dias = semana_de_prueba()
    del dias[d(4)]
    s = semana(LUNES, resumen=dias.get)
    assert s["filas"][4] == {"dia": d(4).isoformat(), "estado": "sin_ajustes", "cuenta": False}
    assert s["acumulado_kcal"] == 300


# -- R3, R6: the days that are left -----------------------------------------------------------------

@pytest.fixture
def ajustes(tmp_path):
    a = Ajustes(tmp_path / "data.sqlite3")
    a.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "objetivo_pct": -10, "limite_saludable_pct": 25},
              desde=DESDE_SIEMPRE)
    a.anadir_carrera("Triatlón de prueba", "2026-10-25", 1)
    return a


def test_dias_que_quedan_hoy_abierto(ajustes):  # R3 · R-20
    s = semana(LUNES, resumen=semana_de_prueba().get)
    quedan = dias_que_quedan(HOY, s, ajustes)
    assert [(q["dia"], q["mantenimiento"]) for q in quedan] == [
        ("2026-10-21", None), ("2026-10-22", None), ("2026-10-23", None),
        ("2026-10-24", "Triatlón de prueba"), ("2026-10-25", "Triatlón de prueba")]
    assert quedan[0] == {"dia": "2026-10-21", "gasto_kcal": 2000, "objetivo_pct": -10, "mantenimiento": None}


def test_dias_que_quedan_hoy_cerrado(ajustes):  # R3 · R-20
    dias = semana_de_prueba()
    dias[d(2)] = resumen_de_prueba(d(2), comido=1800, gasto=2000, estado="comida_cerrada")
    quedan = dias_que_quedan(HOY, semana(LUNES, resumen=dias.get), ajustes)
    assert [q["dia"] for q in quedan if not q["mantenimiento"]] == ["2026-10-22", "2026-10-23"]


def test_dias_que_quedan_domingo_cerrado(ajustes):  # R6
    domingo = d(6)
    dias = {d(i): resumen_de_prueba(d(i), comido=2100, gasto=2000, estado="comida_cerrada") for i in range(7)}
    assert dias_que_quedan(domingo, semana(LUNES, resumen=dias.get), ajustes) == []


# -- R3, R5, R6: the proposal ----------------------------------------------------------------------

def _dias(*gastos, mantenimiento=()):
    return [{"dia": d(2 + i).isoformat(), "gasto_kcal": g, "objetivo_pct": -10,
             "mantenimiento": "Triatlón de prueba" if i in mantenimiento else None} for i, g in enumerate(gastos)]


def test_reparto_a_partes_iguales():  # R3 · R-20, C-20
    p = proponer_reparto(750, _dias(2000, 2000, 2000, 2000, 2000, mantenimiento=(3, 4)), 25)
    assert [(x["dia"], x["antes_pct"], x["nuevo_pct"], x["kcal"], x["objetivo_kcal"]) for x in p["dias"]] == [
        ("2026-10-21", -10, -22.5, 250, 1550), ("2026-10-22", -10, -22.5, 250, 1550),
        ("2026-10-23", -10, -22.5, 250, 1550)]
    assert p["sin_cambio"] == [{"dia": "2026-10-24", "motivo": "mantenimiento por el Triatlón de prueba"},
                               {"dia": "2026-10-25", "motivo": "mantenimiento por el Triatlón de prueba"}]
    assert p["sin_repartir_kcal"] == 0 and p["motivo"] is None and p["por_encima_kcal"] == 750
    assert texto_reparto(p) == (
        "Llevas 750 kcal por encima de tu objetivo esta semana. Para compensarlo: miércoles 21, jueves 22 y "
        "viernes 23 a −22,5 % (1.550 kcal; −250 cada uno). Sábado 24 y domingo 25 no cambian: mantenimiento "
        "por el Triatlón de prueba.")


def test_reparto_con_el_limite():  # R5 · G-22
    p = proponer_reparto(750, _dias(2000, 2000, 2000), 20)
    assert [(x["nuevo_pct"], x["kcal"]) for x in p["dias"]] == [(-20, 200), (-20, 200), (-20, 200)]
    assert p["sin_repartir_kcal"] == 150
    assert "quedan 150 kcal sin repartir sin pasar de tu límite" in texto_reparto(p)


def test_reparto_lo_que_no_cabe_va_a_los_demas():  # R5 · G-22
    p = proponer_reparto(640, _dias(2000, 2500, 2000), 20)
    assert [(x["dia"], x["nuevo_pct"], x["kcal"]) for x in p["dias"]] == [
        ("2026-10-21", -20, 200), ("2026-10-22", -19.6, 240), ("2026-10-23", -20, 200)]
    assert p["sin_repartir_kcal"] == 0


def test_reparto_sin_limite_tope_50():  # R5
    p = proponer_reparto(3000, _dias(2000, 2000), None)
    assert [(x["nuevo_pct"], x["kcal"]) for x in p["dias"]] == [(-50, 800), (-50, 800)]
    assert p["sin_repartir_kcal"] == 1400


def test_reparto_redondea_hacia_menos_deficit():  # R5: never past the limit
    p = proponer_reparto(100, _dias(2100, 2100, 2100), 25)
    # 33.33 kcal / 2100 = 1.587 % → −11.5 % (not −11.6)
    assert [x["nuevo_pct"] for x in p["dias"]] == [-11.5, -11.5, -11.5]
    assert all(x["kcal"] <= 33.4 for x in p["dias"])


@pytest.mark.parametrize("por_encima", [0, -120])
def test_reparto_nada_que_repartir(por_encima):  # R6 · R-19
    p = proponer_reparto(por_encima, _dias(2000, 2000), 25)
    assert p["dias"] == [] and p["motivo"] == "nada_que_repartir"
    assert texto_reparto(p) == "Vas en tu objetivo o por debajo esta semana: no hay nada que repartir"


@pytest.mark.parametrize("dias", [[], _dias(2000, 2000, mantenimiento=(0, 1))])
def test_reparto_sin_dias(dias):  # R6
    p = proponer_reparto(750, dias, 25)
    assert p["dias"] == [] and p["motivo"] == "sin_dias"
    assert texto_reparto(p) == "No quedan días esta semana para repartirlo; la semana que viene empieza de cero"


# -- R7, R8: the chart series and the list of weeks ------------------------------------------------

def _historial(desde: date, hasta: date):
    dias = {}
    for i in range((hasta - desde).days + 1):
        dia = desde + timedelta(days=i)
        estado = "abierto" if dia == hasta else "comida_cerrada"
        dias[dia] = resumen_de_prueba(dia, comido=2000 + (i % 5) * 100 - 200, gasto=2000, estado=estado)
    return dias


@pytest.mark.parametrize(("dias", "agrupado", "barras"), [(28, "dia", 28), (80, "dia", 80), (91, "dia", 91),
                                                          (143, "semana", 21)])
def test_serie_por_dia_o_por_semana(dias, agrupado, barras):  # R7
    assert BARRAS_POR_DIA_MAX == 91
    desde = HOY - timedelta(days=dias - 1)
    datos = _historial(date(2026, 6, 1), HOY)
    pesos = [{"dia": (HOY - timedelta(days=i)).isoformat(), "kg": 73 + i / 100} for i in range(0, 150, 3)]
    s = serie(desde, HOY, resumen=datos.get, pesos=pesos, hoy=HOY)
    assert s["agrupado"] == agrupado and len(s["barras"]) == barras
    assert (s["desde"], s["hasta"]) == (desde.isoformat(), HOY.isoformat())
    if agrupado == "dia":
        ultima = s["barras"][-1]
        assert ultima["dia"] == HOY.isoformat() and ultima["provisional"] is True
        assert ultima["balance_kcal"] == datos[HOY]["cierre"]["balance_kcal"]
        assert not any(b["provisional"] for b in s["barras"][:-1])
    else:
        assert s["barras"][0]["desde"] == "2026-06-01" and s["barras"][-1]["desde"] == "2026-10-19"
        assert s["barras"][0]["balance_kcal"] == datos[date(2026, 6, 1)]["cierre"]["semana"]["balance_medio_kcal"]
    assert all(desde.isoformat() <= p["dia"] <= HOY.isoformat() for p in s["pesos"])
    assert [m["dia"] for m in s["medias"]] == sorted(m["dia"] for m in s["medias"])
    assert "alimentos" not in str(s)


def test_serie_medias_de_7_dias():  # R7: the same media_7_dias of the Peso tab
    datos = _historial(HOY - timedelta(days=27), HOY)
    pesos = [{"dia": (HOY - timedelta(days=1)).isoformat(), "kg": 72.6}, {"dia": HOY.isoformat(), "kg": 72.4}]
    s = serie(HOY - timedelta(days=27), HOY, resumen=datos.get, pesos=pesos, hoy=HOY)
    assert s["medias"][-1] == {"dia": HOY.isoformat(), "kg": 72.5, "dias_con_peso": 2}


def test_semanas_de_la_mas_reciente_a_la_mas_antigua():  # R8
    datos = {}
    for i in range(7):  # the week of 12 Oct: 5 closed days of 2,400 at −420
        dia = date(2026, 10, 12) + timedelta(days=i)
        if i < 5:
            datos[dia] = resumen_de_prueba(dia, comido=1980, gasto=2400, estado="comida_cerrada", semana_014={
                "desde": "2026-10-12", "hasta": "2026-10-18", "dias": 5, "balance_medio_kcal": -420.0,
                "balance_pct": -17.5, "texto": "…"})
    datos.update({k: v for k, v in semana_de_prueba().items() if k <= HOY})
    pesos = [{"dia": "2026-10-13", "kg": 72.6}, {"dia": "2026-10-15", "kg": 73.0}]
    lista = semanas(date(2026, 10, 12), HOY, resumen=datos.get, pesos=pesos)
    assert [w["desde"] for w in lista] == ["2026-10-19", "2026-10-12"]
    assert lista[1] == {"desde": "2026-10-12", "hasta": "2026-10-18", "dias": 5, "acumulado_kcal": -2100,
                        "balance_medio_kcal": -420, "balance_pct": -17.5, "peso_medio_kg": 72.8}
    assert lista[0]["peso_medio_kg"] is None and lista[0]["acumulado_kcal"] == 300


# -- registro.primer_dia ---------------------------------------------------------------------------

def test_primer_dia(tmp_path):  # R7, R9
    registro = RegistroComidas(tmp_path / "data.sqlite3")
    assert registro.primer_dia() is None
    for dia in (date(2026, 8, 5), date(2026, 8, 3), HOY):
        registro.guardar([Alimento(nombre="total del día (importado)", dia=dia, cantidad=None, unidad=None,
                                   kcal=2000)], hoy=HOY, texto="", sin_cambios=True)
    assert registro.primer_dia() == date(2026, 8, 3)
