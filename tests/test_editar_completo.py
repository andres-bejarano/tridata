"""Editing a saved food all the way (unit 004): delete with confirmation, move to another day,
mark a planned food as eaten and add one by hand. RegistroComidas over a temporary SQLite and
the /api/alimento routes through Starlette's TestClient, with the interpreter mocked (no API).
All values are made up.
"""
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.web import crear_app

HOY = date(2026, 3, 4)
MANANA = HOY + timedelta(days=1)
AYER = HOY - timedelta(days=1)


def _guardar(registro, hoy=HOY, **campos):
    datos = {"nombre": "yogur", "dia": HOY, "cantidad": 125.0, "unidad": "g", "kcal": 80.0, "estimado": True}
    datos.update(campos)
    return registro.guardar([Alimento(**datos)], hoy=hoy, texto=datos["nombre"], sin_cambios=True)[0]


def _datos(fila, **cambios):
    """What the change card sends back: the saved fields, plus whatever changes."""
    datos = {c: fila[c] for c in ("nombre", "cantidad", "unidad", "kcal")}
    datos.update(cambios)
    return datos


def _ajustes(tmp_path):
    """Settings in the same temporary SQLite: 1600 + 10000 steps x 0.04 = 2000, since always."""
    ajustes = Ajustes(tmp_path / "data.sqlite3")
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    return ajustes


@pytest.fixture
def registro(tmp_path):
    return RegistroComidas(tmp_path / "data.sqlite3")


@pytest.fixture
def interprete():
    return MagicMock()


@pytest.fixture
def cliente(registro, interprete, tmp_path):
    return TestClient(crear_app(registro, interprete, _ajustes(tmp_path), hoy=lambda: HOY))


@pytest.fixture
def yogur(registro):
    return _guardar(registro)


@pytest.fixture
def manzana(registro):
    return _guardar(registro, nombre="manzana", cantidad=150.0, kcal=78.0)


@pytest.fixture
def pasta_manana(registro):
    return _guardar(registro, nombre="pasta", cantidad=100.0, kcal=350.0, dia=MANANA)


@pytest.fixture
def pasta_planeada_hoy(registro):
    """Planned yesterday for today: still `planeado` today until marked eaten."""
    return _guardar(registro, hoy=AYER, nombre="pasta", cantidad=100.0, kcal=350.0, dia=HOY)


def _margenes(dia):
    return [m["kcal"] for m in dia["margenes"]]


# -- R1: delete only when confirmed ---------------------------------------------------

def test_borrar_quita_solo_ese_alimento(registro, yogur, manzana):
    borrado = registro.borrar(yogur["id"])
    assert borrado == yogur
    assert registro.apuntes_del_dia(HOY) == [manzana]


def test_borrar_uno_que_no_existe(registro, manzana):
    with pytest.raises(KeyError):
        registro.borrar(999)
    assert registro.apuntes_del_dia(HOY) == [manzana]


def test_ruta_borrar_con_confirmacion(cliente, yogur, manzana):
    antes = cliente.get("/api/dia").json()
    assert antes["total_kcal"] == 158
    assert _margenes(antes) == [1642, 1842, 2042]
    r = cliente.post(f"/api/alimento/{yogur['id']}/borrar", json={"confirmado": True})
    assert r.status_code == 200
    assert r.json()["alimento"]["id"] == yogur["id"]
    dia = r.json()["dia"]
    assert dia["fecha"] == HOY.isoformat()
    assert dia["total_kcal"] == 78
    assert _margenes(dia) == [1722, 1922, 2122]
    assert [a["nombre"] for a in dia["alimentos"]] == ["manzana"]


@pytest.mark.parametrize("cuerpo", [None, {}, {"confirmado": False}, {"confirmado": "true"}, {"confirmado": 1}])
def test_ruta_borrar_sin_confirmar_no_borra(registro, cliente, yogur, manzana, cuerpo):
    r = cliente.post(f"/api/alimento/{yogur['id']}/borrar", json=cuerpo)
    assert r.status_code == 400
    assert registro.apuntes_del_dia(HOY) == [yogur, manzana]


def test_ruta_borrar_uno_que_ya_no_esta(registro, cliente, manzana):
    r = cliente.post("/api/alimento/999/borrar", json={"confirmado": True})
    assert r.status_code == 404
    assert "ya no está apuntado" in r.json()["error"]
    assert registro.apuntes_del_dia(HOY) == [manzana]


# -- R2: moving to another day applies the state rule ---------------------------------

def test_mover_a_manana_queda_planeado_con_todo_lo_demas(registro, yogur):
    movido = registro.cambiar(yogur["id"], _datos(yogur, dia=MANANA.isoformat()), hoy=HOY)
    assert movido["dia"] == MANANA.isoformat()
    assert movido["estado"] == "planeado"
    for campo in ("nombre", "cantidad", "unidad", "kcal", "estimado", "supuestos", "pendiente_cantidad"):
        assert movido[campo] == yogur[campo], campo
    assert registro.apuntes_del_dia(HOY) == []
    assert registro.apuntes_del_dia(MANANA) == [movido]


def test_mover_lo_planeado_a_hoy_queda_comido(registro, pasta_manana):
    assert pasta_manana["estado"] == "planeado"
    movido = registro.cambiar(pasta_manana["id"], _datos(pasta_manana, dia=HOY.isoformat()), hoy=HOY)
    assert (movido["dia"], movido["estado"]) == (HOY.isoformat(), "comido")
    assert registro.apuntes_del_dia(MANANA) == []


def test_mover_a_ayer_queda_comido(registro, pasta_manana):
    movido = registro.cambiar(pasta_manana["id"], _datos(pasta_manana, dia=AYER.isoformat()), hoy=HOY)
    assert (movido["dia"], movido["estado"]) == (AYER.isoformat(), "comido")
    assert registro.apuntes_del_dia(AYER) == [movido]


def test_guardar_sin_cambiar_el_dia_deja_el_estado(registro, pasta_planeada_hoy):
    assert pasta_planeada_hoy["estado"] == "planeado"
    igual = registro.cambiar(pasta_planeada_hoy["id"], _datos(pasta_planeada_hoy, dia=HOY.isoformat()), hoy=HOY)
    assert (igual["dia"], igual["estado"]) == (HOY.isoformat(), "planeado")
    sin_dia = registro.cambiar(pasta_planeada_hoy["id"], _datos(pasta_planeada_hoy, cantidad=120, kcal=420))
    assert (sin_dia["dia"], sin_dia["estado"]) == (HOY.isoformat(), "planeado")


def test_ruta_mover_recalcula_origen_y_destino(cliente, yogur, manzana):
    r = cliente.post(f"/api/alimento/{yogur['id']}", json=_datos(yogur, dia=MANANA.isoformat()))
    assert r.status_code == 200
    assert r.json()["alimento"]["estado"] == "planeado"
    dia = r.json()["dia"]
    assert dia["fecha"] == HOY.isoformat()  # the day being looked at, where the food was
    assert dia["total_kcal"] == 78
    manana = cliente.get(f"/api/dia?fecha={MANANA.isoformat()}").json()
    assert [(a["nombre"], a["estado"]) for a in manana["alimentos"]] == [("yogur", "planeado")]
    assert manana["total_kcal"] == 0  # a planned food does not count


def test_ruta_mover_lo_planeado_a_hoy_suma(cliente, manzana, pasta_manana):
    r = cliente.post(f"/api/alimento/{pasta_manana['id']}", json=_datos(pasta_manana, dia=HOY.isoformat()))
    assert r.status_code == 200
    assert r.json()["dia"]["fecha"] == MANANA.isoformat()
    assert r.json()["dia"]["alimentos"] == []
    hoy = cliente.get("/api/dia").json()
    assert hoy["total_kcal"] == 78 + 350
    assert [a["estado"] for a in hoy["alimentos"]] == ["comido", "comido"]


def test_ruta_mover_rechaza_un_dia_no_valido(registro, cliente, yogur):
    r = cliente.post(f"/api/alimento/{yogur['id']}", json=_datos(yogur, dia="mañana"))
    assert r.status_code == 400
    assert registro.apuntes_del_dia(HOY) == [yogur]


# -- R3: mark a planned food as eaten ---------------------------------------------------

def test_marcar_comido_lo_planeado_para_hoy(registro, pasta_planeada_hoy):
    comido = registro.marcar_comido(pasta_planeada_hoy["id"], hoy=HOY)
    assert (comido["dia"], comido["estado"]) == (HOY.isoformat(), "comido")
    assert {k: v for k, v in comido.items() if k != "estado"} == {
        k: v for k, v in pasta_planeada_hoy.items() if k != "estado"}


def test_marcar_comido_un_dia_posterior_no_se_puede(registro, pasta_manana):
    with pytest.raises(ValueError):
        registro.marcar_comido(pasta_manana["id"], hoy=HOY)
    assert registro.apuntes_del_dia(MANANA) == [pasta_manana]


def test_marcar_comido_uno_que_no_existe(registro):
    with pytest.raises(KeyError):
        registro.marcar_comido(999, hoy=HOY)


def test_ruta_comido_suma_al_total(cliente, yogur, manzana, pasta_planeada_hoy):
    antes = cliente.get("/api/dia").json()
    assert antes["total_kcal"] == 158
    r = cliente.post(f"/api/alimento/{pasta_planeada_hoy['id']}/comido")
    assert r.status_code == 200
    assert r.json()["alimento"]["estado"] == "comido"
    dia = r.json()["dia"]
    assert dia["fecha"] == HOY.isoformat()
    assert dia["total_kcal"] == 158 + 350
    assert _margenes(dia) == [m - 350 for m in _margenes(antes)]


def test_ruta_comido_en_un_dia_posterior_se_rechaza(registro, cliente, pasta_manana):
    r = cliente.post(f"/api/alimento/{pasta_manana['id']}/comido")
    assert r.status_code == 422
    assert registro.apuntes_del_dia(MANANA) == [pasta_manana]


def test_ruta_comido_404(cliente):
    assert cliente.post("/api/alimento/999/comido").status_code == 404


# -- R4: add a food by hand, without the AI --------------------------------------------

MANZANA = {"nombre": "manzana", "cantidad": 150, "unidad": "g", "kcal": 78}


def test_anadir_hoy_queda_comido_sin_estimado_ni_interpretacion(registro, tmp_path):
    nuevo = registro.anadir(Alimento(nombre="manzana", dia=HOY, cantidad=150, unidad="g", kcal=78,
                                     estimado=True, supuestos=["una mediana"]), hoy=HOY)
    assert (nuevo["dia"], nuevo["estado"], nuevo["kcal"]) == (HOY.isoformat(), "comido", 78.0)
    assert nuevo["estimado"] is False and nuevo["supuestos"] == []
    assert registro.interpretaciones() == []
    with sqlite3.connect(tmp_path / "data.sqlite3") as conn:
        assert conn.execute("SELECT interpretacion_id FROM alimento_apuntado").fetchall() == [(None,)]


def test_anadir_un_dia_posterior_queda_planeado(registro):
    nuevo = registro.anadir(Alimento(nombre="manzana", dia=MANANA, cantidad=150, unidad="g", kcal=78), hoy=HOY)
    assert nuevo["estado"] == "planeado"


def test_anadir_cantidad_sin_kcal_no_se_guarda(registro):
    with pytest.raises(ValueError, match="kcal"):
        registro.anadir(Alimento(nombre="manzana", dia=HOY, cantidad=150, unidad="g"), hoy=HOY)
    assert registro.apuntes_del_dia(HOY) == []


def test_ruta_anadir_recalcula_el_dia_sin_llamar_a_la_ia(registro, cliente, interprete, yogur):
    r = cliente.post("/api/alimento", json={**MANZANA, "dia": HOY.isoformat(), "estimado": True})
    assert r.status_code == 200
    alimento = r.json()["alimento"]
    assert (alimento["nombre"], alimento["estado"], alimento["estimado"]) == ("manzana", "comido", False)
    dia = r.json()["dia"]
    assert dia["fecha"] == HOY.isoformat()
    assert dia["total_kcal"] == 158
    assert _margenes(dia) == [1642, 1842, 2042]
    interprete.interpretar.assert_not_called()
    assert len(registro.interpretaciones()) == 1  # only the yogur's list


def test_ruta_anadir_en_manana_queda_planeado(cliente):
    r = cliente.post("/api/alimento", json={**MANZANA, "dia": MANANA.isoformat()})
    assert r.status_code == 200
    assert r.json()["alimento"]["estado"] == "planeado"
    assert r.json()["dia"]["fecha"] == MANANA.isoformat()
    assert r.json()["dia"]["total_kcal"] == 0


def test_ruta_anadir_sin_cantidad_queda_pendiente_y_no_suma(cliente, yogur):
    r = cliente.post("/api/alimento", json={"nombre": "manzana", "dia": HOY.isoformat()})
    assert r.status_code == 200
    assert r.json()["alimento"]["pendiente_cantidad"] is True
    dia = r.json()["dia"]
    assert dia["total_kcal"] == 80
    assert [p["nombre"] for p in dia["pendientes"]] == ["manzana"]


def test_ruta_anadir_cantidad_sin_kcal_dice_que_falta(registro, cliente):
    r = cliente.post("/api/alimento", json={**MANZANA, "kcal": None, "dia": HOY.isoformat()})
    assert r.status_code == 422 and "kcal" in r.json()["error"]
    assert registro.apuntes_del_dia(HOY) == []


@pytest.mark.parametrize("cuerpo", [
    {**MANZANA, "nombre": "", "dia": HOY.isoformat()},
    {**MANZANA, "dia": "hoy"},
    {**MANZANA},
    {**MANZANA, "cantidad": -1, "dia": HOY.isoformat()},
])
def test_ruta_anadir_datos_no_validos(registro, cliente, cuerpo):
    r = cliente.post("/api/alimento", json=cuerpo)
    assert r.status_code == 400 and r.json()["error"]
    assert registro.apuntes_del_dia(HOY) == []


def test_ruta_anadir_sin_nombre_dice_que_falta(cliente):
    r = cliente.post("/api/alimento", json={**MANZANA, "nombre": "  ", "dia": HOY.isoformat()})
    assert r.status_code == 400 and "nombre" in r.json()["error"]
