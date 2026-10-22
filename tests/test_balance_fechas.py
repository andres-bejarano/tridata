"""Unit 018 (P-20261022-83532c43): the weight-and-deficit chart between two dates, `GET /api/balance?desde=&hasta=`.

Plans: `cerrar-dia` «Historial y gráfica»; Q-3 of unit 017 (only totals). TestClient over a temporary SQLite (the
`Web` helper of unit 017); Garmin is never called. Every value is invented. «Today» is Wednesday 2026-10-21.
"""
from __future__ import annotations

from datetime import date

import pytest

from test_balance_web import Web


@pytest.fixture
def desde_julio(tmp_path):
    web = Web(tmp_path)
    web.historial(date(2026, 7, 27))
    return web


def test_del_1_de_agosto_al_1_de_septiembre(desde_julio):  # R8 · R7
    s = desde_julio.get("/api/balance?desde=2026-08-01&hasta=2026-09-01")
    assert (s["desde"], s["hasta"], s["agrupado"], s["vacio"], s["primer_dia"]) == (
        "2026-08-01", "2026-09-01", "dia", False, "2026-07-27")
    assert len(s["barras"]) == 32
    assert [b["dia"] for b in s["barras"]][:2] == ["2026-08-01", "2026-08-02"]
    assert not any(b["provisional"] for b in s["barras"])
    assert all("2026-08-01" <= p["dia"] <= "2026-09-01" for p in s["pesos"]) and s["pesos"]
    assert s["medias"]
    r = desde_julio.cliente.get("/api/balance?desde=2026-08-01&hasta=2026-09-01")
    assert "alimentos" not in r.text and "importado" not in r.text and "nombre" not in r.text


def test_un_solo_dia_una_barra(desde_julio):  # R8
    s = desde_julio.get("/api/balance?desde=2026-08-10&hasta=2026-08-10")
    assert (s["desde"], s["hasta"], s["agrupado"], len(s["barras"])) == ("2026-08-10", "2026-08-10", "dia", 1)


def test_antes_del_primer_dia_empieza_en_el_primero(desde_julio):  # R8
    s = desde_julio.get("/api/balance?desde=2026-07-01&hasta=2026-08-15")
    assert (s["desde"], s["hasta"], s["primer_dia"]) == ("2026-07-27", "2026-08-15", "2026-07-27")
    assert len(s["barras"]) == 20


def test_mas_de_91_dias_por_semanas(tmp_path):  # R8: weeks, each with the mean of its whole week, as in «Todo»
    web = Web(tmp_path)
    web.historial(date(2026, 6, 1))
    s = web.get("/api/balance?desde=2026-06-01&hasta=2026-09-30")
    assert (s["agrupado"], len(s["barras"])) == ("semana", 18)
    todo = {b["desde"]: b for b in web.get("/api/balance?rango=todo")["barras"]}
    for b in s["barras"]:
        assert b["balance_kcal"] == todo[b["desde"]]["balance_kcal"] and b["dias"] == todo[b["desde"]]["dias"]
    assert s["barras"][-1]["desde"] == "2026-09-28" and s["barras"][-1]["dias"] == 7


def test_rango_sin_datos(desde_julio):  # R8
    s = desde_julio.get("/api/balance?desde=2026-05-01&hasta=2026-05-31")
    assert s["vacio"] is True and s["barras"] == [] and s["pesos"] == []


def test_hasta_hoy_lleva_hoy_provisional(desde_julio):  # R8: today open, striped, only when it is inside
    s = desde_julio.get("/api/balance?desde=2026-10-01&hasta=2026-10-21")
    assert s["barras"][-1]["dia"] == "2026-10-21" and s["barras"][-1]["provisional"] is True


@pytest.mark.parametrize("ruta", [
    "/api/balance?desde=2026-09-01&hasta=2026-08-01",  # desde > hasta
    "/api/balance?desde=2026-10-01&hasta=2026-10-22",  # after today
    "/api/balance?desde=2026-08-1x&hasta=2026-09-01",  # badly written
    "/api/balance?desde=2026-08-01&hasta=2026-02-30",
    "/api/balance?desde=2026-08-01",  # one missing
    "/api/balance?hasta=2026-09-01",
    "/api/balance?rango=4s&desde=2026-08-01&hasta=2026-09-01",  # both ways at once
    "/api/balance?rango=todo&hasta=2026-09-01",
])
def test_fechas_mal_son_400(desde_julio, ruta):  # R8
    r = desde_julio.cliente.get(ruta)
    assert r.status_code == 400 and "error" in r.json()


def test_los_rangos_siguen_igual(desde_julio):  # R8: ?rango=4s|3m|todo as in unit 017
    cuatro = desde_julio.get("/api/balance?rango=4s")
    assert (cuatro["rango"], cuatro["desde"], cuatro["hasta"], cuatro["agrupado"], len(cuatro["barras"])) == (
        "4s", "2026-09-24", "2026-10-21", "dia", 28)
    assert desde_julio.get("/api/balance?rango=todo")["desde"] == "2026-07-27"
    assert desde_julio.cliente.get("/api/balance").status_code == 400
