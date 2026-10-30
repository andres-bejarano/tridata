"""Several planned trainings on one day (unit 047, R1-R7 of `consultar-margen`).

The column `objetivo_dia.previsto` keeps a LIST; an old row with one object reads as a list of one, with no
migration. Outwards `previstos` is added and `previsto` (the first or None), `previsto_kcal` (the sum) and
`previsto_nombre` (the names together) keep their meaning. `POST /api/dia/objetivo` adds, changes and removes by
position, and still takes the old `previsto` (an object: only that one; null: none). The assistant's card adds, and
only replaces the one of the same activity. Unit level over `ObjetivosDia` and `gasto`, integration with
Starlette's TestClient, the assistant with hand-written `CambioInterpretado` (Claude is never called).

Today is Wednesday 2026-10-21. Every value is invented: a swim of 45 min and 360 kcal two days ago, a ride of 60 min
and 540 kcal three days ago, a run of 30 min and 330 kcal four days ago, an old run of twenty days ago. Settings:
basal 1,600 kcal, 0.04 kcal/step, 10,000 steps: 2,000 kcal without Garmin data that day.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.ia import CambioInterpretado
from tridata.models import Activity
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Cierres, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.nutricion.asistente import Contexto, Peticion, confirmar, proponer
from tridata.nutricion.gasto import estimar_gasto, gasto_hecho
from tridata.nutricion.margen import ConfigGasto
from tridata.nutricion.recetas import Recetario
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 10, 21)
MANANA = HOY + timedelta(days=1)
CONFIG = ConfigGasto(basal_kcal=1600, kcal_por_paso=0.04, pasos=10000)

NAT = {"actividad_id": "p-nat", "nombre": "Natación de prueba", "tipo": "lap_swimming", "minutos": 45, "kcal": 360}
BICI = {"actividad_id": "p-bici", "nombre": "Bici de prueba", "tipo": "cycling", "minutos": 60, "kcal": 540}
CARRERA = {"actividad_id": "p-carrera", "nombre": "Carrera de prueba", "tipo": "running", "minutos": 30, "kcal": 330}


def _actividad(ident, hace, tipo, nombre, minutos, kcal):
    return Activity(activity_id=ident, activity_date=HOY - timedelta(days=hace), activity_type=tipo, name=nombre,
                    duration_seconds=minutos * 60, calories=kcal)


ACTIVIDADES = [
    _actividad("p-nat", 2, "lap_swimming", "Natación de prueba", 45, 360),
    _actividad("p-bici", 3, "cycling", "Bici de prueba", 60, 540),
    _actividad("p-carrera", 4, "running", "Carrera de prueba", 30, 330),
    _actividad("p-vieja", 20, "running", "Carrera vieja de prueba", 30, 330),
]


# -- ObjetivosDia: a list in the column, an old object read as a list of one (R4) ------------------------------------

def test_guarda_la_lista_y_el_primero_sigue_en_previsto(tmp_path):  # R1, R4
    objetivos = ObjetivosDia(tmp_path / "data.sqlite3")
    guardado = objetivos.cambiar(HOY, previstos=[NAT, BICI, CARRERA])
    assert [p["actividad_id"] for p in guardado["previstos"]] == ["p-nat", "p-bici", "p-carrera"]
    assert guardado["previsto"]["actividad_id"] == "p-nat"
    leido = objetivos.del_dia(HOY)
    assert leido["previstos"] == guardado["previstos"] and leido["previsto"] == guardado["previsto"]


def test_previsto_antiguo_deja_solo_ese_y_null_ninguno(tmp_path):  # R4
    objetivos = ObjetivosDia(tmp_path / "data.sqlite3")
    objetivos.cambiar(HOY, previstos=[NAT, BICI])
    solo = objetivos.cambiar(HOY, previsto=CARRERA)
    assert [p["actividad_id"] for p in solo["previstos"]] == ["p-carrera"]
    assert solo["previsto"]["actividad_id"] == "p-carrera"
    ninguno = objetivos.cambiar(HOY, previsto=None)
    assert ninguno["previstos"] == [] and ninguno["previsto"] is None
    vacio = objetivos.cambiar(HOY, previstos=[BICI])
    assert len(vacio["previstos"]) == 1
    assert objetivos.cambiar(HOY, previstos=[])["previstos"] == []


def test_una_fila_antigua_con_un_objeto_se_lee_como_lista_de_uno(tmp_path):  # R4
    db = tmp_path / "data.sqlite3"
    objetivos = ObjetivosDia(db)
    with sqlite3.connect(db) as conn:  # as unit 008 wrote it: ONE object in the column
        conn.execute("INSERT INTO objetivo_dia (dia, previsto, creado) VALUES (?, ?, ?)",
                     (HOY.isoformat(), json.dumps(NAT), "2026-10-20T08:00:00"))
    leido = objetivos.del_dia(HOY)
    assert leido["previstos"] == [NAT]
    assert leido["previsto"] == NAT
    otro = objetivos.cambiar(MANANA, porcentaje=-10)
    assert otro["previstos"] == [] and otro["previsto"] is None


def test_mas_de_diez_no_cambia_nada(tmp_path):  # R6
    objetivos = ObjetivosDia(tmp_path / "data.sqlite3")
    objetivos.cambiar(HOY, previstos=[NAT])
    once = [{**BICI, "minutos": 10 + i} for i in range(11)]
    with pytest.raises(ValueError, match="10"):
        objetivos.cambiar(HOY, previstos=once)
    assert objetivos.del_dia(HOY)["previstos"] == [NAT]
    with pytest.raises(ValueError):
        objetivos.cambiar(HOY, previstos=[NAT, {**BICI, "minutos": 0}])
    with pytest.raises(ValueError):
        objetivos.cambiar(HOY, previstos=NAT)  # not a list
    assert objetivos.del_dia(HOY)["previstos"] == [NAT]


# -- gasto: every one adds; the old shape stays (R1, R7) ---------------------------------------------------------------

def test_el_gasto_suma_todos(tmp_path):  # R1
    estimado = estimar_gasto(CONFIG, None, [], [NAT, BICI, CARRERA])
    assert estimado["kcal"] == 2000 + 360 + 540 + 330
    d = estimado["desglose"]
    assert [p["nombre"] for p in d["previstos"]] == ["Natación de prueba", "Bici de prueba", "Carrera de prueba"]
    assert d["previsto"] == NAT
    assert d["previsto_kcal"] == 1230
    h = gasto_hecho(estimado)
    assert h["previsto_kcal"] == 1230
    assert h["previsto_nombre"] == "Natación de prueba, Bici de prueba y Carrera de prueba"
    assert h["kcal"] == 1600  # what is done does not count anything planned


def test_un_objeto_y_una_lista_de_uno_dan_lo_mismo():  # R7
    assert estimar_gasto(CONFIG, None, [], NAT) == estimar_gasto(CONFIG, None, [], [NAT])
    uno = estimar_gasto(CONFIG, {"steps": 6000}, [], NAT)
    assert uno["kcal"] == 2360 and uno["desglose"]["previsto_kcal"] == 360
    assert uno["desglose"]["previstos"] == [NAT] and uno["desglose"]["previsto"] == NAT
    assert gasto_hecho(uno)["previsto_nombre"] == "Natación de prueba"
    nada = estimar_gasto(CONFIG, None, [], [])
    assert nada == estimar_gasto(CONFIG, None, [], None)
    assert nada["desglose"]["previstos"] == [] and nada["desglose"]["previsto"] is None
    assert nada["desglose"]["previsto_kcal"] == 0


# -- the web: add, change, remove by position (R1-R4, R6, R7) ---------------------------------------------------------

class Web:
    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000,
                              "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.store = DataStore(self.db)
        self.store.save_activities(ACTIVIDADES)
        self.cierres = Cierres(self.db, ajustes=self.ajustes, pesos=self.pesos, objetivos=self.objetivos,
                               store=self.store, registro=self.registro)
        self.cliente = TestClient(crear_app(self.registro, MagicMock(), self.ajustes, hoy=lambda: HOY,
                                            pesos=self.pesos, objetivos=self.objetivos, store=self.store,
                                            cierres=self.cierres))

    def objetivo(self, cuerpo):
        return self.cliente.post("/api/dia/objetivo", json=cuerpo)

    def ok(self, cuerpo):
        r = self.objetivo(cuerpo)
        assert r.status_code == 200, r.text
        return r.json()

    def dia(self, fecha=HOY):
        r = self.cliente.get(f"/api/dia?fecha={fecha.isoformat()}")
        assert r.status_code == 200, r.text
        return r.json()

    def previstos(self, fecha=HOY):
        fila = self.objetivos.del_dia(fecha)
        return None if fila is None else [(p["actividad_id"], p["minutos"], p["kcal"]) for p in fila["previstos"]]


@pytest.fixture
def w(tmp_path):
    return Web(tmp_path)


def _tres(w, fecha=None):
    extra = {} if fecha is None else {"fecha": fecha.isoformat()}
    for ident, minutos in (("p-nat", 45), ("p-bici", 60), ("p-carrera", 30)):
        w.ok({"previsto_anadir": {"actividad_id": ident, "minutos": minutos}, **extra})


def test_tres_previstos_suman_y_se_nombran_uno_a_uno(w):  # R1 (portante)
    assert w.dia()["gasto"]["kcal"] == 2000
    _tres(w)
    dia = w.dia()
    assert dia["gasto"]["kcal"] == 2000 + 360 + 540 + 330
    d = dia["gasto"]["desglose"]
    assert [(p["nombre"], p["minutos"], p["kcal"]) for p in d["previstos"]] == [
        ("Natación de prueba", 45, 360), ("Bici de prueba", 60, 540), ("Carrera de prueba", 30, 330)]
    assert d["previsto"]["nombre"] == "Natación de prueba" and d["previsto_kcal"] == 1230
    explicacion = dia["gasto"]["explicacion"]
    for parte in ("360 kcal de entreno previsto (Natación de prueba, 45 min)",
                  "540 kcal de entreno previsto (Bici de prueba, 60 min)",
                  "330 kcal de entreno previsto (Carrera de prueba, 30 min)"):
        assert parte in explicacion
    assert dia["barra"]["real"]["hecho"]["previsto_kcal"] == 1230
    assert dia["barra"]["real"]["hecho"]["previsto_nombre"] == "Natación de prueba, Bici de prueba y Carrera de prueba"
    assert [p["actividad_id"] for p in dia["objetivo_dia"]["previstos"]] == ["p-nat", "p-bici", "p-carrera"]


def test_un_dia_futuro_tambien(w):  # R1
    _tres(w, MANANA)
    assert w.dia(MANANA)["gasto"]["kcal"] == 3230
    assert w.objetivos.del_dia(HOY) is None


def test_cambiar_uno_solo_toca_ese(w):  # R2
    _tres(w)
    respuesta = w.ok({"previsto_cambiar": {"posicion": 1, "minutos": 90}})
    assert respuesta["dia"]["gasto"]["kcal"] == 3230 + 270
    assert w.previstos() == [("p-nat", 45, 360), ("p-bici", 90, 810), ("p-carrera", 30, 330)]
    # the activity too: the run becomes 30 min like the swim (kcal recomputed from the swim)
    w.ok({"previsto_cambiar": {"posicion": 2, "minutos": 30, "actividad_id": "p-nat"}})
    assert w.previstos() == [("p-nat", 45, 360), ("p-bici", 90, 810), ("p-nat", 30, 240)]
    assert w.objetivos.del_dia(HOY)["previstos"][2]["nombre"] == "Natación de prueba"
    assert w.dia()["gasto"]["kcal"] == 2000 + 360 + 810 + 240


def test_quitar_uno_solo_quita_ese(w):  # R3
    _tres(w)
    respuesta = w.ok({"previsto_quitar": 0})
    assert respuesta["dia"]["gasto"]["kcal"] == 2000 + 540 + 330
    assert w.previstos() == [("p-bici", 60, 540), ("p-carrera", 30, 330)]
    w.ok({"previsto_quitar": 1})
    assert w.previstos() == [("p-bici", 60, 540)]
    w.ok({"previsto_quitar": 0})
    assert w.previstos() == []
    assert w.dia()["gasto"]["kcal"] == 2000


def test_la_forma_antigua_de_la_api_sigue_valiendo(w):  # R4
    _tres(w)
    w.ok({"previsto": {"actividad_id": "p-bici", "minutos": 30}})  # an object: only that one
    assert w.previstos() == [("p-bici", 30, 270)]
    w.ok({"previsto_anadir": {"actividad_id": "p-nat", "minutos": 45}})
    assert len(w.previstos()) == 2
    w.ok({"previsto": None})  # null: none
    assert w.previstos() == []
    assert w.dia()["gasto"]["kcal"] == 2000


def test_una_fila_antigua_se_suma_y_se_le_anade_otro(w):  # R4
    with sqlite3.connect(w.db) as conn:
        conn.execute("INSERT INTO objetivo_dia (dia, previsto, creado) VALUES (?, ?, ?)",
                     (HOY.isoformat(), json.dumps(NAT), "2026-10-20T08:00:00"))
    dia = w.dia()
    assert dia["gasto"]["kcal"] == 2360
    assert dia["gasto"]["desglose"]["previsto"]["nombre"] == "Natación de prueba"
    assert len(dia["gasto"]["desglose"]["previstos"]) == 1
    w.ok({"previsto_anadir": {"actividad_id": "p-carrera", "minutos": 30}})
    assert w.previstos() == [("p-nat", 45, 360), ("p-carrera", 30, 330)]
    assert w.dia()["gasto"]["kcal"] == 2690


def test_uno_solo_da_los_mismos_numeros_que_antes(tmp_path):  # R7
    antiguo, nuevo = Web(tmp_path / "a"), Web(tmp_path / "b")
    antiguo.ok({"previsto": {"actividad_id": "p-nat", "minutos": 45}})
    nuevo.ok({"previsto_anadir": {"actividad_id": "p-nat", "minutos": 45}})
    a, n = antiguo.dia(), nuevo.dia()
    for clave in ("margenes", "barra", "objetivo", "con_planeado", "cierre"):
        assert a[clave] == n[clave], clave
    assert a["gasto"]["kcal"] == n["gasto"]["kcal"] == 2360
    assert a["gasto"]["explicacion"] == n["gasto"]["explicacion"]
    assert a["gasto"]["explicacion"].count("entreno previsto") == 1


@pytest.mark.parametrize("cuerpo", [
    {"previsto_cambiar": {"posicion": 3, "minutos": 30}},             # there are 3: 0, 1, 2
    {"previsto_cambiar": {"posicion": -1, "minutos": 30}},
    {"previsto_cambiar": {"posicion": "1", "minutos": 30}},
    {"previsto_cambiar": {"posicion": True, "minutos": 30}},
    {"previsto_cambiar": {"posicion": 1, "minutos": 601}},
    {"previsto_cambiar": {"posicion": 1, "minutos": 0}},
    {"previsto_cambiar": {"posicion": 1, "minutos": 30, "actividad_id": "p-vieja"}},
    {"previsto_cambiar": 1},
    {"previsto_quitar": 3},
    {"previsto_quitar": -1},
    {"previsto_quitar": None},
    {"previsto_quitar": "0"},
    {"previsto_anadir": {"actividad_id": "p-nat", "minutos": 0}},
    {"previsto_anadir": {"actividad_id": "p-nat", "minutos": 601}},
    {"previsto_anadir": {"actividad_id": "p-nat"}},
    {"previsto_anadir": {"actividad_id": "p-vieja", "minutos": 30}},  # twenty days ago: not among the last 14
    {"previsto_anadir": {"actividad_id": "nada", "minutos": 30}},
    {"previsto_anadir": None},
    {"previsto_anadir": {"actividad_id": "p-nat", "minutos": 30}, "previsto_quitar": 0},  # one thing at a time
])
def test_errores_no_cambian_nada(w, cuerpo):  # R6
    _tres(w)
    antes = w.objetivos.del_dia(HOY)
    r = w.objetivo(cuerpo)
    assert 400 <= r.status_code < 500, r.text
    assert "No se ha cambiado nada" in r.json()["error"]
    assert w.objetivos.del_dia(HOY) == antes


def test_el_undecimo_no_entra(w):  # R6
    for _ in range(10):
        w.ok({"previsto_anadir": {"actividad_id": "p-bici", "minutos": 20}})
    antes = w.objetivos.del_dia(HOY)
    r = w.objetivo({"previsto_anadir": {"actividad_id": "p-nat", "minutos": 20}})
    assert 400 <= r.status_code < 500
    assert "10" in r.json()["error"]
    assert w.objetivos.del_dia(HOY) == antes and len(antes["previstos"]) == 10


def test_un_dia_pasado_no(w):  # R6 (unit 034, as always)
    r = w.objetivo({"previsto_anadir": {"actividad_id": "p-nat", "minutos": 30},
                    "fecha": (HOY - timedelta(days=1)).isoformat()})
    assert r.status_code == 422
    assert w.objetivos.del_dia(HOY - timedelta(days=1)) is None


# -- the assistant: it adds, and only replaces the one of the same activity (R5) --------------------------------------

def _como_reciente(a: Activity) -> dict:
    return {"activity_id": a.activity_id, "activity_date": a.activity_date.isoformat(), "activity_type": a.activity_type,
            "name": a.name, "duration_seconds": a.duration_seconds, "calories": a.calories}


class Asistente:
    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000,
                              "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.pesos = RegistroPeso(self.db)
        self.recetario = Recetario(self.db)
        recientes = [_como_reciente(a) for a in ACTIVIDADES[:3]]
        self.ctx = Contexto(registro=self.registro, objetivos=self.objetivos, ajustes=self.ajustes, pesos=self.pesos,
                            recetario=self.recetario, hoy=lambda: HOY, recientes=lambda: recientes)

    def proponer(self, objeto, minutos, dia=HOY):
        (t,) = proponer([CambioInterpretado(accion="entreno_previsto", objeto=objeto, cantidad=minutos, dia=dia)],
                        self.ctx, Peticion(texto=f"y {minutos} min de {objeto}", fecha=HOY))
        return t

    def previstos(self, dia=HOY):
        return [(p["actividad_id"], p["minutos"]) for p in self.objetivos.del_dia(dia)["previstos"]]


@pytest.fixture
def a(tmp_path):
    return Asistente(tmp_path)


def test_el_asistente_anade_otro_de_otra_actividad(a):  # R5
    a.objetivos.cambiar(MANANA, previsto=NAT)  # the swim, as the screen of unit 008 saved it
    t = a.proponer("carrera", 30, MANANA)
    assert t["tipo"] == "propuesta" and t["error"] is None
    assert any("se suma" in aviso for aviso in t["avisos"]), t["avisos"]
    assert not any("sustituye" in aviso for aviso in t["avisos"])
    assert "Gasto estimado del 22-10: 2.360 → 2.690 kcal" in t["lineas"]
    confirmar(t, a.ctx)
    assert a.previstos(MANANA) == [("p-nat", 45), ("p-carrera", 30)]


def test_el_asistente_sustituye_solo_la_misma_actividad(a):  # R5
    a.objetivos.cambiar(HOY, previstos=[NAT, {**CARRERA, "minutos": 20, "kcal": 220}, BICI])
    t = a.proponer("carrera", 30)
    assert any("sustituye" in aviso and "20 min de Carrera de prueba" in aviso for aviso in t["avisos"]), t["avisos"]
    confirmar(t, a.ctx)
    assert a.previstos() == [("p-nat", 45), ("p-carrera", 30), ("p-bici", 60)]
    assert sum(p["kcal"] for p in a.objetivos.del_dia(HOY)["previstos"]) == 360 + 330 + 540


def test_el_asistente_sin_nada_pone_el_primero(a):  # R5
    t = a.proponer("bici", 60)
    assert t["avisos"] == []
    confirmar(t, a.ctx)
    assert a.previstos() == [("p-bici", 60)]


def test_el_asistente_si_ya_es_lo_dice(a):  # R5
    a.objetivos.cambiar(HOY, previstos=[NAT, CARRERA])
    t = a.proponer("carrera", 30)
    assert t["tipo"] == "no" and "ya es" in t["texto"] and "30 min de Carrera de prueba" in t["texto"]


def test_el_asistente_no_pasa_de_diez(a):  # R6
    a.objetivos.cambiar(HOY, previstos=[{**BICI, "minutos": 10 + i, "kcal": 90} for i in range(10)])
    t = a.proponer("carrera", 30)
    assert t["tipo"] == "no" or t["error"]
    assert "10" in (t.get("error") or t["texto"])
    assert len(a.objetivos.del_dia(HOY)["previstos"]) == 10


# -- unit 060 · R3: the kcal of a training count without its BMR (Garmin's include the basal of that hour) -------------

def test_un_entreno_hecho_cuenta_sin_su_bmr():  # R3
    actividades = [
        {"activity_id": "h1", "name": "Rodaje de prueba", "activity_type": "cycling", "duration_seconds": 3600,
         "calories": 650.0, "bmr_calories": 80.0},
        {"activity_id": "h2", "name": "Fuerza de prueba", "activity_type": "strength_training",
         "duration_seconds": 1800, "calories": 200.0},  # saved before unit 060: no BMR, as before
    ]
    d = estimar_gasto(CONFIG, None, actividades)["desglose"]
    assert [h["kcal"] for h in d["entreno_hecho"]] == [570.0, 200.0]
    assert d["entreno_hecho_kcal"] == 770.0
    assert estimar_gasto(CONFIG, None, actividades)["kcal"] == 1600 + 400 + 770
    assert d["entreno_sin_basal"] is True  # the mark of the new rule, for the calibration (R4)


def test_las_kcal_previstas_tambien_sin_bmr():  # R3
    from tridata.nutricion.gasto import kcal_previstas
    con = {"duration_seconds": 3600, "calories": 650.0, "bmr_calories": 80.0}
    assert kcal_previstas(con, 30) == 285.0  # (650 − 80) × 30 / 60
    sin = {"duration_seconds": 3600, "calories": 650.0}
    assert kcal_previstas(sin, 30) == 325.0  # without a BMR, as before
