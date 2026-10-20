"""Changing a food that is already saved (unit 003): RegistroComidas.cambiar and
POST /api/alimento/{id}, over a temporary SQLite with the interpreter mocked (no API).
"""
from datetime import date
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import Alimento, ConfigGasto, RegistroComidas
from tridata.web import crear_app

HOY = date(2026, 3, 4)
CONFIG = ConfigGasto(basal_kcal=1600, kcal_por_paso=0.04)


def _guardar(registro, **campos):
    datos = {"nombre": "yogur", "dia": HOY, "cantidad": 125.0, "unidad": "g", "kcal": 80.0, "estimado": True}
    datos.update(campos)
    return registro.guardar([Alimento(**datos)], hoy=HOY, texto="un yogur", sin_cambios=True)[0]


@pytest.fixture
def registro(tmp_path):
    return RegistroComidas(tmp_path / "data.sqlite3")


@pytest.fixture
def cliente(registro):
    return TestClient(crear_app(registro, MagicMock(), CONFIG, hoy=lambda: HOY))


# -- RegistroComidas.cambiar -----------------------------------------------------------

def test_cambia_cantidad_y_kcal_y_deja_de_ser_estimado(registro):  # R1
    guardado = _guardar(registro)
    cambiado = registro.cambiar(guardado["id"], {"nombre": "yogur griego", "cantidad": 150, "unidad": "g", "kcal": 140})
    assert (cambiado["nombre"], cambiado["cantidad"], cambiado["kcal"]) == ("yogur griego", 150.0, 140.0)
    assert cambiado["estimado"] is False
    assert registro.apuntes_del_dia(HOY) == [cambiado]


def test_mismas_kcal_conserva_estimado(registro):
    guardado = _guardar(registro)
    cambiado = registro.cambiar(guardado["id"], {"nombre": "yogur natural", "cantidad": 125, "unidad": "g", "kcal": 80})
    assert cambiado["estimado"] is True


def test_pendiente_pasa_a_sumar_al_ponerle_cantidad(registro):  # R2
    guardado = _guardar(registro, cantidad=None, unidad=None, kcal=None)
    assert guardado["pendiente_cantidad"] is True
    cambiado = registro.cambiar(guardado["id"], {"nombre": "yogur", "cantidad": 125, "unidad": "g", "kcal": 80})
    assert cambiado["pendiente_cantidad"] is False


def test_cantidad_sin_kcal_no_se_guarda(registro):  # R2
    guardado = _guardar(registro)
    with pytest.raises(ValueError, match="kcal"):
        registro.cambiar(guardado["id"], {"nombre": "yogur", "cantidad": 125, "unidad": "g", "kcal": None})
    assert registro.apuntes_del_dia(HOY)[0]["kcal"] == 80.0


def test_no_toca_estado_dia_ni_los_demas(registro):
    guardado = _guardar(registro)
    otro = _guardar(registro, nombre="manzana", kcal=52.0)
    registro.cambiar(guardado["id"], {"nombre": "yogur", "cantidad": 200, "unidad": "g", "kcal": 128})
    filas = {f["id"]: f for f in registro.apuntes_del_dia(HOY)}
    assert filas[guardado["id"]]["estado"] == "comido" and filas[guardado["id"]]["dia"] == HOY.isoformat()
    assert filas[otro["id"]] == otro


def test_id_que_no_existe(registro):
    with pytest.raises(KeyError):
        registro.cambiar(999, {"nombre": "yogur", "cantidad": 1, "unidad": "g", "kcal": 1})


# -- POST /api/alimento/{id} ------------------------------------------------------------

def test_ruta_devuelve_el_dia_recalculado(registro, cliente):  # R1
    guardado = _guardar(registro)
    antes = cliente.get("/api/dia").json()
    r = cliente.post(f"/api/alimento/{guardado['id']}",
                     json={"nombre": "yogur", "cantidad": 250, "unidad": "g", "kcal": 160})
    assert r.status_code == 200
    dia = r.json()["dia"]
    assert dia["total_kcal"] == antes["total_kcal"] + 80
    assert [m["kcal"] for m in dia["margenes"]] == [m["kcal"] - 80 for m in antes["margenes"]]
    assert dia["alimentos"][0]["estimado"] is False


def test_ruta_rechaza_cantidad_sin_kcal_y_no_cambia_nada(registro, cliente):  # R2, R3
    guardado = _guardar(registro)
    r = cliente.post(f"/api/alimento/{guardado['id']}",
                     json={"nombre": "yogur", "cantidad": 250, "unidad": "g", "kcal": None})
    assert r.status_code == 422 and "kcal" in r.json()["error"]
    assert registro.apuntes_del_dia(HOY) == [guardado]


@pytest.mark.parametrize("cuerpo", [{}, {"nombre": ""}, {"nombre": "yogur", "cantidad": -1, "kcal": 1}])
def test_ruta_rechaza_datos_no_validos(registro, cliente, cuerpo):
    guardado = _guardar(registro)
    assert cliente.post(f"/api/alimento/{guardado['id']}", json=cuerpo).status_code == 400
    assert registro.apuntes_del_dia(HOY) == [guardado]


def test_ruta_404_si_no_existe(cliente):
    r = cliente.post("/api/alimento/999", json={"nombre": "yogur", "cantidad": 1, "unidad": "g", "kcal": 1})
    assert r.status_code == 404
