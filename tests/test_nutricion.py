"""Food log and daily margin (unit 002): saving, day total and margin.

All foods and numbers are invented. Each test uses its own temporary SQLite.
"""
import sqlite3
from datetime import date, timedelta

import pytest

from tridata.nutricion import (
    Alimento, ConfigGasto, RegistroComidas, gasto_simple, margenes, resumen_del_dia, total_del_dia,
)
from tridata.storage import DEFAULT_DB_PATH, DataStore

HOY = date(2026, 3, 4)
CONFIG = ConfigGasto(basal_kcal=1600, kcal_por_paso=0.04)


def _registro(tmp_path):
    return RegistroComidas(tmp_path / "data.sqlite3")


def _alimento(nombre, kcal, cantidad=100.0, dia=HOY, **extra):
    return Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad="g", kcal=kcal, **extra)


# -- R4: each food saved as data, total always from what was saved ------------

def test_total_sube_exactamente_lo_confirmado(tmp_path):
    """R4 (criterio portante): avena 110 + plátano 90 confirmados ⇒ el total sube 200."""
    registro = _registro(tmp_path)
    registro.guardar([_alimento("manzana", 50)], hoy=HOY, texto="una manzana", sin_cambios=True)
    antes = total_del_dia(registro.apuntes_del_dia(HOY), HOY)

    registro.guardar(
        [_alimento("avena", 110, cantidad=30), _alimento("plátano", 90)],
        hoy=HOY, texto="30 g de avena y un plátano", sin_cambios=True,
    )
    apuntes = registro.apuntes_del_dia(HOY)

    assert total_del_dia(apuntes, HOY) - antes == 200
    assert [a["nombre"] for a in apuntes] == ["manzana", "avena", "plátano"]


def test_guarda_cada_alimento_como_dato(tmp_path):
    registro = _registro(tmp_path)
    alimento = Alimento(
        nombre="lentejas", dia=HOY, cantidad=300, unidad="g", kcal=345, proteina_g=27,
        hidratos_g=60, grasa_g=1.5, estimado=True, supuestos=["lentejas cocidas"],
    )
    registro.guardar([alimento], hoy=HOY, texto="300 gramos de lentejas", sin_cambios=False)

    (fila,) = registro.apuntes_del_dia(HOY)
    assert fila["nombre"] == "lentejas"
    assert fila["cantidad"] == 300 and fila["unidad"] == "g"
    assert fila["kcal"] == 345
    assert (fila["proteina_g"], fila["hidratos_g"], fila["grasa_g"]) == (27, 60, 1.5)
    assert fila["estimado"] is True
    assert fila["supuestos"] == ["lentejas cocidas"]
    assert fila["estado"] == "comido"
    assert fila["pendiente_cantidad"] is False
    assert fila["dia"] == HOY.isoformat()


def test_macros_opcionales_quedan_vacios(tmp_path):
    registro = _registro(tmp_path)
    registro.guardar([_alimento("pan", 80, cantidad=30)], hoy=HOY, texto="pan", sin_cambios=True)
    (fila,) = registro.apuntes_del_dia(HOY)
    assert fila["proteina_g"] is None and fila["hidratos_g"] is None and fila["grasa_g"] is None


def test_guarda_la_interpretacion_con_si_se_corrigio(tmp_path):
    """La tabla `interpretacion` mide el éxito (9 de cada 10 sin corregir)."""
    registro = _registro(tmp_path)
    registro.guardar([_alimento("pan", 80)], hoy=HOY, texto="pan con tomate", sin_cambios=True)
    registro.guardar([_alimento("queso", 120)], hoy=HOY, texto="queso", sin_cambios=False)

    filas = registro.interpretaciones()
    assert [(f["texto"], f["confirmada_sin_cambios"]) for f in filas] == [
        ("pan con tomate", True), ("queso", False),
    ]
    assert all(f["hora"] for f in filas)


# -- R6: a doubtful item can never be saved -----------------------------------

def test_no_guarda_una_lista_con_un_dudoso(tmp_path):
    registro = _registro(tmp_path)
    lista = [_alimento("jamón", 210, cantidad=200), Alimento(nombre="kfjd", dia=HOY, dudoso=True)]

    with pytest.raises(ValueError, match="dudoso"):
        registro.guardar(lista, hoy=HOY, texto="200 g de jamón y un poco de kfjd", sin_cambios=True)

    assert registro.apuntes_del_dia(HOY) == []
    assert registro.interpretaciones() == []


# -- R7: no quantity -> pending, not added ------------------------------------

def test_sin_cantidad_queda_pendiente_y_no_suma(tmp_path):
    registro = _registro(tmp_path)
    registro.guardar(
        [_alimento("tostada", 150, cantidad=1), Alimento(nombre="leche", dia=HOY, kcal=None)],
        hoy=HOY, texto="una tostada y leche en el café", sin_cambios=True,
    )
    apuntes = registro.apuntes_del_dia(HOY)

    leche = next(a for a in apuntes if a["nombre"] == "leche")
    assert leche["pendiente_cantidad"] is True
    assert total_del_dia(apuntes, HOY) == 150
    resumen = resumen_del_dia(apuntes, HOY, CONFIG)
    assert [p["nombre"] for p in resumen["pendientes"]] == ["leche"]


def test_pendiente_con_kcal_tampoco_suma():
    apuntes = [
        {"dia": HOY.isoformat(), "estado": "comido", "kcal": 40.0, "pendiente_cantidad": True},
        {"dia": HOY.isoformat(), "estado": "comido", "kcal": 100.0, "pendiente_cantidad": False},
    ]
    assert total_del_dia(apuntes, HOY) == 100


# -- R9: another day -> planned in that day -----------------------------------

def test_otro_dia_se_guarda_planeado_y_no_cambia_hoy(tmp_path):
    registro = _registro(tmp_path)
    registro.guardar([_alimento("arroz", 130)], hoy=HOY, texto="arroz", sin_cambios=True)
    manana = HOY + timedelta(days=1)

    registro.guardar([_alimento("pasta", 360, dia=manana)], hoy=HOY, texto="mañana 100 g de pasta",
                     sin_cambios=True)

    assert [a["nombre"] for a in registro.apuntes_del_dia(HOY)] == ["arroz"]
    assert total_del_dia(registro.apuntes_del_dia(HOY), HOY) == 130
    (pasta,) = registro.apuntes_del_dia(manana)
    assert pasta["estado"] == "planeado"
    assert total_del_dia(registro.apuntes_del_dia(manana), manana) == 0


def test_planeado_de_otro_dia_no_entra_en_el_total_de_hoy():
    apuntes = [
        {"dia": HOY.isoformat(), "estado": "comido", "kcal": 300.0, "pendiente_cantidad": False},
        {"dia": HOY.isoformat(), "estado": "planeado", "kcal": 500.0, "pendiente_cantidad": False},
        {"dia": (HOY + timedelta(days=1)).isoformat(), "estado": "planeado", "kcal": 700.0,
         "pendiente_cantidad": False},
    ]
    assert total_del_dia(apuntes, HOY) == 300


# -- R5: simple expenditure and margins ---------------------------------------

def test_gasto_simple_basal_mas_diez_mil_pasos():
    assert gasto_simple(CONFIG) == 2000


def test_margenes_con_los_niveles_por_defecto():
    """R5: basal 1.600, 0,04 kcal/paso, 1.050 comidas ⇒ 750 / 950 / 1.150."""
    assert margenes(2000, 1050) == {-10: 750, 0: 950, 10: 1150}


def test_resumen_del_dia_tabla_total_gasto_y_margen(tmp_path):
    registro = _registro(tmp_path)
    registro.guardar(
        [_alimento("lentejas", 600, cantidad=300), _alimento("pan", 450, cantidad=150)],
        hoy=HOY, texto="lentejas y pan", sin_cambios=True,
    )
    resumen = resumen_del_dia(registro.apuntes_del_dia(HOY), HOY, CONFIG)

    assert resumen["fecha"] == HOY.isoformat()
    assert [a["nombre"] for a in resumen["alimentos"]] == ["lentejas", "pan"]
    assert resumen["total_kcal"] == 1050
    assert resumen["gasto"]["kcal"] == 2000
    assert resumen["gasto"]["basal_kcal"] == 1600
    assert resumen["gasto"]["pasos"] == 10000
    assert resumen["gasto"]["kcal_por_paso"] == 0.04
    assert "1600" in resumen["gasto"]["explicacion"] and "10000" in resumen["gasto"]["explicacion"]
    assert resumen["margenes"] == [
        {"nivel": -10, "kcal": 750}, {"nivel": 0, "kcal": 950}, {"nivel": 10, "kcal": 1150},
    ]
    assert resumen["pendientes"] == []


# -- configuration -------------------------------------------------------------

def test_config_desde_el_entorno():
    config = ConfigGasto.desde_entorno({"TRIDATA_BASAL_KCAL": "1500", "TRIDATA_KCAL_POR_PASO": "0,05"})
    assert config == ConfigGasto(basal_kcal=1500, kcal_por_paso=0.05)


def test_config_sin_valores_dice_que_falta():
    with pytest.raises(ValueError, match="TRIDATA_BASAL_KCAL"):
        ConfigGasto.desde_entorno({"TRIDATA_KCAL_POR_PASO": "0.04"})


# -- same database, Garmin tables untouched ------------------------------------

def test_tablas_propias_en_la_misma_base_sin_tocar_las_de_garmin(tmp_path):
    db = tmp_path / "data.sqlite3"
    DataStore(db)
    registro = RegistroComidas(db)
    registro.guardar([_alimento("kiwi", 40)], hoy=HOY, texto="kiwi", sin_cambios=True)

    with sqlite3.connect(db) as conn:
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"alimento_apuntado", "interpretacion", "daily_stats", "activities"} <= tablas


def test_por_defecto_usa_la_base_de_tridata():
    import inspect
    firma = inspect.signature(RegistroComidas)
    assert firma.parameters["db_path"].default == DEFAULT_DB_PATH


# -- input validation (what the web sends) --------------------------------------

def test_alimento_desde_dict_valida_y_convierte():
    a = Alimento.desde_dict({
        "nombre": " avena ", "dia": "2026-03-04", "cantidad": "33", "unidad": "g", "kcal": 125,
        "estimado": True, "dudoso": False, "supuestos": ["copos de avena"],
    })
    assert a == Alimento(nombre="avena", dia=HOY, cantidad=33.0, unidad="g", kcal=125.0,
                         estimado=True, supuestos=["copos de avena"])


@pytest.mark.parametrize("malo", [
    {"nombre": "", "dia": "2026-03-04"},
    {"nombre": "pan", "dia": "ayer"},
    {"nombre": "pan", "dia": "2026-03-04", "kcal": "mucho"},
    {"nombre": "pan", "dia": "2026-03-04", "cantidad": -5},
])
def test_alimento_desde_dict_rechaza_datos_malos(malo):
    with pytest.raises(ValueError):
        Alimento.desde_dict(malo)


def test_con_cantidad_pero_sin_kcal_no_se_guarda(tmp_path):
    registro = _registro(tmp_path)
    with pytest.raises(ValueError, match="kcal"):
        registro.guardar([_alimento("pan", None)], hoy=HOY, texto="pan", sin_cambios=False)
    assert registro.apuntes_del_dia(HOY) == []
