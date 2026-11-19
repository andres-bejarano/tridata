"""Unit 031: GET /api/fitness, GET /api/actividad/{id} and «Pulso y zonas» through /api/ajustes.

Plans `ver-fitness` REC-19: R-105..R-111 · C-146..C-152 · G-47, G-49. R1-R8 of the unit over a seeded SQLite (the
`sembrar` of test_fitness.py). Every value is invented.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient
from test_fitness import AYER, HOY, PULSO_A, PULSO_B, ZONAS_A, sembrar

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas
from tridata.storage import DataStore
from tridata.web import crear_app


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    monkeypatch.setenv("TRIDATA_HR_MAX", "190")
    monkeypatch.setenv("TRIDATA_HR_REST", "60")


class Web:
    def __init__(self, tmp_path) -> None:
        db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                             desde=DESDE_SIEMPRE)
        self.store = DataStore(db)
        sembrar(self.store)
        self.cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), self.ajustes, hoy=lambda: HOY,
                                            objetivos=ObjetivosDia(db), store=self.store,
                                            actualizar_garmin=MagicMock(), ahora=lambda: datetime(2026, 10, 21, 10)))


@pytest.fixture
def web(tmp_path) -> Web:
    return Web(tmp_path)


def test_api_fitness_los_tres_bloques(web):  # R1, R2, R4, R5 · C-146, C-147, C-148, C-150
    r = web.cliente.get("/api/fitness")
    assert r.status_code == 200
    f = r.json()
    assert f["fecha"] == HOY.isoformat()
    # unit 090 (R1): it opens in 6 weeks (42 days) — before, 14 fixed; 14 is now `?dias=14` (test below)
    assert len(f["carga"]["serie"]) == 42 and f["carga"]["hoy"]["fecha"] == HOY.isoformat()
    assert f["carga"]["pulso"] == {"confirmada": False, "texto": "de la configuración: confírmala en Ajustes"}
    u = f["ultima_actividad"]
    assert (u["actividad_id"], u["deporte"], u["fecha"], u["vueltas"], u["clima"]) == (
        "c-ayer", "Carrera", AYER.isoformat(), 10, True)
    rec = f["recuperacion"]
    assert rec["sueno"]["estado"] == "incompleto"
    assert rec["pulso_reposo"]["estado"] == "no_ha_llegado" and rec["pulso_reposo"]["valor"] is None
    assert rec["hrv"]["estado"] == "ok"


def test_api_actividad_vueltas_zona_y_clima(web):  # R3 · C-152
    web.ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    r = web.cliente.get("/api/actividad/c-ayer")
    assert r.status_code == 200
    d = r.json()
    assert [v["zona"] for v in d["vueltas"]] == [2, 2, 2, 2, 3, 3, 3, 4, 4, 5]
    assert d["clima"]["condition"] == "Despejado"
    assert web.cliente.get("/api/actividad/no-existe").status_code == 404


def test_pulso_y_zonas_por_ajustes_y_su_texto_en_fitness(web):  # R6 · C-149
    r = web.cliente.post("/api/ajustes", json={"datos": PULSO_A, "desde": "2026-10-01"})
    assert r.status_code == 200
    vigentes = r.json()["ajustes"]["vigentes"]
    assert vigentes["fc_max"] == {"valor": 180, "desde": "2026-10-01"}
    assert vigentes["zonas_fc"]["valor"] == ZONAS_A
    assert vigentes["fc_origen"]["valor"] == {"tipo": "prueba", "mes": "2025-11"}
    pulso = web.cliente.get("/api/fitness").json()["carga"]["pulso"]
    assert pulso == {"confirmada": True, "texto": "de tu prueba de esfuerzo de 2025-11, desde el 1/10"}


def test_fc_no_valida_no_se_guarda_y_dice_por_que(web):  # R7
    r = web.cliente.post("/api/ajustes", json={"datos": {**PULSO_A, "fc_reposo": 90, "fc_max": 85}, "desde": "2026-10-01"})
    assert r.status_code == 400
    assert r.json()["error"].startswith("No se ha guardado nada:")
    assert web.cliente.get("/api/ajustes").json()["vigentes"]["fc_max"] == {"valor": None, "desde": None}


def test_hoy_y_fitness_dan_la_misma_frescura(web):  # R8
    web.ajustes.cambiar(PULSO_A, desde=DESDE_SIEMPRE)
    web.ajustes.cambiar(PULSO_B, desde="2026-10-15")
    hoy = web.cliente.get("/api/hoy").json()["como_llegas"]["frescura"]["valor"]
    assert hoy == web.cliente.get("/api/fitness").json()["carga"]["hoy"]["tsb"]


def test_la_pagina_trae_fitness(web):  # R1-R4 · the client
    html = web.cliente.get("/").text
    for id_ in ("fitness-carga", "fitness-grafica", "fitness-ultima", "fitness-detalle", "fitness-recuperacion",
                "fitness-pulso"):
        assert f'id="{id_}"' in html
    assert web.cliente.get("/static/fitness.js").status_code == 200
    assert "fitness.js" in web.cliente.get("/static/app.js").text
    assert "graficaCarga" in web.cliente.get("/static/grafica.js").text
    ajustes = web.cliente.get("/ajustes").text
    assert "Pulso y zonas" in ajustes and 'id="pulso"' in ajustes


# -- unit 090 (REC-35): the range of the load and the weeks ----------------------------------------------------

@pytest.mark.parametrize(("dias", "puntos", "semanas"), [(14, 14, 2), (42, 42, 6), (91, 59, 13), (365, 59, 52)])
def test_api_fitness_por_rango(web, dias, puntos, semanas):  # R1, R3 · R-232, R-234
    f = web.cliente.get(f"/api/fitness?dias={dias}").json()
    assert len(f["carga"]["serie"]) == puntos  # 59: the seeded history starts 58 days ago
    assert f["carga"]["serie"][-1]["fecha"] == HOY.isoformat()
    assert len(f["semanas"]) == semanas and f["semanas"][-1]["en_curso"] is True
    assert f["ultima_actividad"]["actividad_id"] == "c-ayer"  # the rest, as before


def test_api_fitness_por_defecto_trae_seis_semanas(web):  # R1, R3
    f = web.cliente.get("/api/fitness").json()
    assert f == web.cliente.get("/api/fitness?dias=42").json()
    assert len(f["semanas"]) == 6
    s = f["semanas"][-2]
    assert set(s["deportes"]) == {"correr", "bici", "nadar", "fuerza", "otros"}
    assert set(s["total"]) == {"horas", "km", "carga", "zonas"}


@pytest.mark.parametrize("dias", ["30", "0", "abc", "-14", "365.0"])
def test_api_fitness_rango_que_no_vale(web, dias):  # R1
    r = web.cliente.get(f"/api/fitness?dias={dias}")
    assert r.status_code == 400
    assert "14, 42, 91 o 365" in r.json()["error"]


def test_api_fitness_sin_garmin_abre_igual(tmp_path):  # R6
    db = tmp_path / "data.sqlite3"
    ajustes = Ajustes(db)
    cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), ajustes, hoy=lambda: HOY, store=None))
    f = cliente.get("/api/fitness?dias=91").json()
    assert f["carga"]["serie"] == [] and len(f["semanas"]) == 13
    assert all(s["total"]["horas"] == 0 for s in f["semanas"])


def test_la_pagina_trae_el_rango_la_formula_y_las_semanas(web):  # R1-R5 · the client
    html = web.cliente.get("/").text
    for id_ in ("fitness-rango", "fitness-como", "fitness-semanas", "fitness-medida", "fitness-grafica-semanas"):
        assert f'id="{id_}"' in html
    for texto in ("14 días", "6 semanas", "3 meses", "1 año", "¿Cómo se calcula?", "TRIMP", "42 días", "7 días"):
        assert texto in html
    assert "graficaSemanas" in web.cliente.get("/static/grafica.js").text


# -- unit 097 (REC-42): a period with dates and its comparison with the one before -----------------------------

DEPORTES_TABLA = ["correr", "bici", "nadar", "fuerza", "otros", "total"]


def test_api_fitness_con_fechas(web):  # R1, R3, R5 · R-263, R-265, C-331, C-333
    r = web.cliente.get("/api/fitness?desde=2026-09-01&hasta=2026-10-15")
    assert r.status_code == 200
    f = r.json()
    assert f["periodo"]["desde"] == "2026-09-01" and f["periodo"]["hasta"] == "2026-10-15"
    serie = f["carga"]["serie"]
    assert serie[0]["fecha"] == "2026-09-01" and serie[-1]["fecha"] == "2026-10-15" and len(serie) == 45
    assert f["carga"]["hoy"]["fecha"] == HOY.isoformat()  # today's figures do not move
    assert [s["lunes"] for s in f["semanas"]][::6] == ["2026-08-31", "2026-10-12"] and len(f["semanas"]) == 7
    c = f["comparacion"]
    assert c["anterior"] == {"desde": "2026-07-18", "hasta": "2026-08-31", "dias": 45}
    assert c["historial_desde"] == "2026-08-24"  # the seeded history starts 58 days before HOY: no %
    assert [fila["deporte"] for fila in c["filas"]] == DEPORTES_TABLA


@pytest.mark.parametrize(("consulta", "texto"), [
    ("desde=2026-10-15&hasta=2026-09-01", "el “desde” tiene que ser antes del “hasta”"),
    ("desde=2026-10-01&hasta=2026-10-22", "el “hasta” no puede ser después de hoy"),
    ("desde=2026-10-01&hasta=2026-10-06", "7 días"),
    ("desde=2024-10-20&hasta=2026-10-21", "2 años"),
    ("desde=2026-13-01&hasta=2026-10-15", "no es una fecha"),
    ("desde=2026-09-01", "«desde» y «hasta»"),
    ("hasta=2026-09-01", "«desde» y «hasta»"),
])
def test_api_fitness_fechas_que_no_valen(web, consulta, texto):  # R6 · R-268, C-337
    r = web.cliente.get(f"/api/fitness?{consulta}")
    assert r.status_code == 400
    assert texto in r.json()["error"]


@pytest.mark.parametrize("desde", ["2026-10-15", "2024-10-21"])  # 7 and 731 days
def test_api_fitness_limites_que_valen(web, desde):  # R6
    assert web.cliente.get(f"/api/fitness?desde={desde}&hasta=2026-10-21").status_code == 200


def test_api_fitness_tabla_con_atajo_y_todos_los_deportes(web):  # R4 · R-266, C-334
    c = web.cliente.get("/api/fitness?dias=42").json()["comparacion"]
    assert c["este"] == {"desde": "2026-09-10", "hasta": HOY.isoformat(), "dias": 42}
    assert c["anterior"] == {"desde": "2026-07-30", "hasta": "2026-09-09", "dias": 42}
    assert [fila["deporte"] for fila in c["filas"]] == DEPORTES_TABLA
    total = c["filas"][-1]
    assert total["este"]["sesiones"] == 21 and total["este"]["horas"] == 20.83  # 20 rides of 1 h + the run of 50 min


def test_api_fitness_fechas_sin_garmin(tmp_path):  # app without store: the table at zero, no warning
    db = tmp_path / "data.sqlite3"
    cliente = TestClient(crear_app(RegistroComidas(db), MagicMock(), Ajustes(db), hoy=lambda: HOY, store=None))
    f = cliente.get("/api/fitness?desde=2026-09-01&hasta=2026-10-15").json()
    assert len(f["semanas"]) == 7 and f["carga"]["serie"] == []
    assert f["comparacion"]["historial_desde"] is None
    assert all(fila["este"]["horas"] == 0 for fila in f["comparacion"]["filas"])


def test_la_pagina_trae_elegir_fechas_y_la_tabla(web):  # R1, R2 · the client
    html = web.cliente.get("/").text
    for id_ in ("fitness-fechas", "fitness-desde", "fitness-hasta", "fitness-aplicar", "fitness-comparar"):
        assert f'id="{id_}"' in html
    assert "Elegir fechas" in html and 'data-dias="fechas"' in html
