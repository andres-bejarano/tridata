"""Unit 016 (small everyday fixes), the routes and the repository: Starlette TestClient over a temporary
SQLite (no API). Every value is invented.

R3 (support) /api/dia says which day is today · R7 new kcal drop «de tu receta» and «provisional».
R6 lives in `test_actualizar_garmin.py`. Unit 080 (S-5): R1 (the objective of a «nuevo día» to the day of the list,
/api/confirmar) and R2 (/api/interpretar with the day of the recording) went with the box to write or dictate.
"""
from datetime import date, timedelta

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas
from tridata.web import crear_app

HOY = date(2026, 10, 21)  # a Wednesday
MARTES = HOY - timedelta(days=1)


class Entorno:
    def __init__(self, db):
        self.db = db
        self.registro = RegistroComidas(db)
        ajustes = Ajustes(db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000, "objetivo_pct": -10},
                        desde=DESDE_SIEMPRE)
        self.objetivos = ObjetivosDia(db)
        self.cliente = TestClient(crear_app(self.registro, None, ajustes, hoy=lambda: HOY,
                                            objetivos=self.objetivos))


@pytest.fixture
def entorno(tmp_path):
    return Entorno(tmp_path / "data.sqlite3")


# -- R3 (support): every day says which day is today on the server ------------------------------------

def test_el_dia_dice_que_dia_es_hoy(entorno):  # R3
    assert entorno.cliente.get("/api/dia").json()["hoy"] == HOY.isoformat()
    otro = entorno.cliente.get(f"/api/dia?fecha={(HOY - timedelta(days=2)).isoformat()}").json()
    assert otro["fecha"] == (HOY - timedelta(days=2)).isoformat() and otro["hoy"] == HOY.isoformat()


# -- R7: new kcal drop «de tu receta» and «provisional» -----------------------------------------------

ORIGEN = "de tu receta «Lentejas de prueba»"


def _lentejas(registro):
    alimento = Alimento(nombre="Lentejas de prueba", dia=HOY, cantidad=250, unidad="g", kcal=320, origen=ORIGEN,
                        provisional=True)
    return registro.guardar([alimento], hoy=HOY, texto="lentejas de prueba", sin_cambios=True)[0]


def _datos(fila, **cambios):
    return {"nombre": fila["nombre"], "cantidad": fila["cantidad"], "unidad": fila["unidad"], "kcal": fila["kcal"],
            **cambios}


def test_cambiar_las_kcal_quita_origen_y_provisional(entorno):  # R7 · R-8
    fila = _lentejas(entorno.registro)

    cambiado = entorno.registro.cambiar(fila["id"], _datos(fila, kcal=300), hoy=HOY)

    assert cambiado["kcal"] == 300
    assert cambiado["origen"] is None and cambiado["provisional"] is False
    assert entorno.registro.alimento(fila["id"])["origen"] is None


@pytest.mark.parametrize("cambios", [{"nombre": "Lentejas de la abuela de prueba"}, {"cantidad": 300},
                                     {"dia": MARTES.isoformat()}], ids=["nombre", "cantidad", "dia"])
def test_sin_tocar_las_kcal_las_marcas_se_quedan(entorno, cambios):  # R7
    fila = _lentejas(entorno.registro)

    cambiado = entorno.registro.cambiar(fila["id"], _datos(fila, **cambios), hoy=HOY)

    assert cambiado["origen"] == ORIGEN and cambiado["provisional"] is True


def test_la_ruta_de_cambiar_devuelve_la_fila_sin_marcas(entorno):  # R7
    fila = _lentejas(entorno.registro)

    r = entorno.cliente.post(f"/api/alimento/{fila['id']}", json=_datos(fila, kcal=300))

    assert r.status_code == 200, r.text
    (lentejas,) = r.json()["dia"]["alimentos"]
    assert lentejas["origen"] is None and lentejas["provisional"] is False and lentejas["kcal"] == 300
