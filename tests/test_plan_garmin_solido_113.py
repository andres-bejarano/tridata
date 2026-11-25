"""Unit 113 (`llevar-plan`, R-180..R-185): the plan from Garmin asks less, keeps the sport and does not duplicate.

R1 (portante): a workout already paired and read today is not read again the same day. R2: a 429 on a detail waits
and tries once more; twice, the plan stays as it was. R3/R4: the sport of a session is kept and wins over its title.
R5: a session out of the window that Garmin moves into it moves, it does not come twice. R6: an old base opens the
same. Garmin is always a double that counts its calls; every name, date and id is invented.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest
from test_sync import _activity, _leido, _stats, _StubClient

from tridata import sync as sync_mod
from tridata.garmin_client import GarminRateLimitError
from tridata.plan import Planes
from tridata.plan.desde_garmin import YA_NO_ESTA, Programado, cambios
from tridata.storage import DataStore
from tridata.sync import SyncService

HOY = date(2026, 11, 2)
CREADO = datetime(2026, 10, 1, 9, 0)
CARRERA = "2026-12-20"


@pytest.fixture(autouse=True)
def _sin_espera(monkeypatch):
    """The wait after a 429 is a constant of `sync.py`; the tests do not wait."""
    monkeypatch.setattr(sync_mod, "ESPERA_429", 0, raising=False)


def _plan(sesiones: list[dict] | None = None) -> dict:
    return {"nombre": "Plan inventado 113", "apartados": [{"titulo": "Semanas", "parte": "previsto"}],
            "carreras": [{"apartado": 0, "nombre": "Carrera inventada", "fecha": CARRERA, "objetivo": "1:30",
                          "estado": "pendiente", "principal": True}],
            "semanas": [], "nutricion": [], "incidencias": [], "notas": [], "sesiones": sesiones or []}


class _Garmin(_StubClient):
    """The calendar of Garmin Connect that counts the details asked; `limites` 429s before answering a detail."""

    def __init__(self, programados: list[dict], *, limites: int = 0) -> None:
        super().__init__(activities=[_activity(HOY, "act-113")], daily_stats={HOY: _stats(HOY)})
        self.programados = programados
        self.limites = limites
        self.calendario: list[tuple[date, date]] = []
        self.detalles: list[int] = []

    def entrenos_programados(self, desde: date, hasta: date) -> list[dict]:
        self.calendario.append((desde, hasta))
        return [dict(p) for p in self.programados if desde.isoformat() <= p["fecha"] <= hasta.isoformat()]

    def entreno(self, workout_id: int) -> dict:
        self.detalles.append(workout_id)
        if self.limites:
            self.limites -= 1
            raise GarminRateLimitError("Garmin Connect is limiting the requests (429)")
        p = next(p for p in self.programados if p["entreno"] == workout_id)
        return _leido(p["titulo"], 2700, 335, "2026-10-28T10:00:00.0")


def _item(n: int, fecha: str, deporte: str = "running") -> dict:
    return {"programado": 9000 + n, "entreno": 8000 + n, "fecha": fecha, "titulo": f"Entreno inventado {n}",
            "deporte": deporte}


def _pasada(store: DataStore, client: _Garmin, dia: date) -> None:
    client.detalles.clear()
    SyncService(client, store).sync(since=dia, until=dia, revalidate_days=0)


def _filas(db) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT rowid, * FROM plan_sesion ORDER BY rowid").fetchall()


# -- R1 (portante): a second pass the same day asks the calendar and no detail ---------------------------------------

def test_r1_segunda_pasada_del_dia_sin_detalles_nuevo_y_movido_si_y_al_dia_siguiente_todos(tmp_path):
    store = DataStore(tmp_path / "t.db")
    Planes(store.db_path).crear(_plan(), CREADO)
    items = [_item(n, (HOY + timedelta(days=1 + n)).isoformat()) for n in range(20)]
    client = _Garmin(items)

    _pasada(store, client, HOY)  # the first: the 20 are new, all read and added
    assert sorted(client.detalles) == sorted(i["entreno"] for i in items)
    assert len(_filas(store.db_path)) == 20

    _pasada(store, client, HOY)  # the second, the same day: the calendar, no detail
    assert client.calendario[-1] == (HOY - timedelta(days=3), date.fromisoformat(CARRERA))
    assert client.detalles == []
    assert len(_filas(store.db_path)) == 20

    client.programados[5]["fecha"] = (HOY + timedelta(days=30)).isoformat()  # moved in Garmin
    client.programados.append(_item(20, (HOY + timedelta(days=4)).isoformat()))  # new in Garmin
    _pasada(store, client, HOY)
    assert sorted(client.detalles) == sorted([8005, 8020])
    assert len(_filas(store.db_path)) == 21

    _pasada(store, client, HOY + timedelta(days=1))  # the next day the paired ones are read again
    assert sorted(client.detalles) == sorted(i["entreno"] for i in client.programados)


def test_r1_sin_releer_lo_que_cambio_de_dia_en_garmin_llega_al_plan(tmp_path):
    store = DataStore(tmp_path / "t.db")
    Planes(store.db_path).crear(_plan(), CREADO)
    client = _Garmin([_item(1, "2026-11-05")])
    _pasada(store, client, HOY)
    client.programados[0]["fecha"] = "2026-11-07"
    _pasada(store, client, HOY)
    assert [f[4] for f in _filas(store.db_path)] == ["2026-11-07"]


# -- R2: a 429 on a detail ---------------------------------------------------------------------------------------------

def test_r2_un_429_espera_reintenta_y_la_pasada_termina_bien(tmp_path):
    store = DataStore(tmp_path / "t.db")
    Planes(store.db_path).crear(_plan(), CREADO)
    client = _Garmin([_item(1, "2026-11-05"), _item(2, "2026-11-06")], limites=1)
    _pasada(store, client, HOY)
    assert client.detalles == [8001, 8001, 8002]
    assert [f[5] for f in _filas(store.db_path)] == ["Entreno inventado 1", "Entreno inventado 2"]


def test_r2_dos_429_dejan_el_plan_igual_y_lo_demas_termina(tmp_path):
    store = DataStore(tmp_path / "t.db")
    Planes(store.db_path).crear(_plan([{"apartado": 0, "fecha": "2026-11-05", "titulo": "Rodaje 45'",
                                        "bloques": ["45' a 5:35"], "clave": False}]), CREADO)
    antes = _filas(store.db_path)
    client = _Garmin([_item(1, "2026-11-05"), _item(2, "2026-11-06")], limites=2)
    _pasada(store, client, HOY)  # does not raise
    assert client.detalles == [8001, 8001]
    assert _filas(store.db_path) == antes  # byte for byte
    assert store.get_activity("act-113") is not None
    assert HOY in client.get_daily_stats_calls
    _pasada(store, client, HOY)  # the next one tries again
    assert [f[5] for f in _filas(store.db_path)] == ["Entreno inventado 1", "Entreno inventado 2"]


# -- R3, R4: the sport kept wins over the title ------------------------------------------------------------------------

def _ses(ident: int, fecha: str, titulo: str, deporte: str | None, **extra) -> dict:
    return {"id": ident, "fecha": fecha, "titulo": titulo, "bloques": ["1h suave"], "garmin_workout_id": None,
            "garmin_schedule_id": None, "garmin_actualizado": None, "deporte": deporte, **extra}


def _prog(sched: int, workout: int, fecha: str, deporte: str = "carrera",
          actualizado: str = "2026-10-29T18:00:00.0") -> Programado:
    return Programado(programado=sched, entreno=workout, fecha=fecha, deporte=deporte, titulo="Rodaje Garmin",
                      bloques=("50' a 5:30",), actualizado=actualizado)


@pytest.mark.parametrize(("deporte", "sustituye"), [("bici", False), (None, True), ("carrera", True)])
def test_r3_el_deporte_guardado_manda_sobre_el_titulo(deporte, sustituye):
    s = _ses(1, "2026-11-05", "Rodaje suave 1h", deporte)
    c = cambios([s], [_prog(701, 601, "2026-11-05")])
    if sustituye:
        assert list(c.actualizar) == [1] and c.anadir == []
    else:
        assert c.actualizar == {} and len(c.anadir) == 1
        assert c.anadir[0]["deporte"] == "carrera"


def test_r3_sin_deporte_guardado_se_empareja_por_el_titulo_como_hoy():
    s = _ses(1, "2026-11-05", "Bici 1h", None)
    c = cambios([s], [_prog(701, 601, "2026-11-05", "bici")])
    assert list(c.actualizar) == [1]


def test_r4_emparejar_y_anadir_guardan_el_deporte_de_garmin():
    s = _ses(1, "2026-11-05", "Bici 1h", None)
    c = cambios([s], [_prog(701, 601, "2026-11-05", "bici"), _prog(702, 602, "2026-11-06", "natacion")])
    assert c.actualizar[1]["deporte"] == "bici"
    assert c.anadir[0]["deporte"] == "natacion"


def test_r4_se_guarda_en_la_base_y_mandar_al_reloj_guarda_carrera(tmp_path):
    store = DataStore(tmp_path / "t.db")
    planes = Planes(store.db_path)
    plan_id = planes.crear(_plan([{"apartado": 0, "fecha": "2026-11-08", "titulo": "Rodaje suave 1h",
                                   "bloques": ["1h suave"], "clave": False}]), CREADO)
    client = _Garmin([{**_item(1, "2026-11-05"), "deporte": "cycling"}])
    _pasada(store, client, HOY)
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT fecha, deporte FROM plan_sesion ORDER BY fecha").fetchall() == [
            ("2026-11-05", "bici"), ("2026-11-08", None)]
        (ident,) = conn.execute("SELECT rowid FROM plan_sesion WHERE fecha = '2026-11-08'").fetchone()
    planes.marcar_garmin(ident, 501, 601, datetime(2026, 11, 2, 9), "Rodaje suave 1h: 1h suave")
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT deporte FROM plan_sesion WHERE rowid = ?", (ident,)).fetchone() == ("carrera",)
    assert plan_id


# -- R5: an old session moved in Garmin into the window ----------------------------------------------------------------

def test_r5_cambios_mueve_la_sesion_de_fuera_de_la_ventana_y_no_le_avisa():
    vieja = _ses(1, "2026-10-23", "Tirada", "carrera", garmin_workout_id=501, garmin_schedule_id=601,
                 garmin_actualizado="2026-10-20T10:00:00.0", fuera=True)
    olvidada = _ses(2, "2026-10-20", "Rodaje", "carrera", garmin_workout_id=502, garmin_schedule_id=602,
                    garmin_actualizado="2026-10-18T10:00:00.0", fuera=True)
    p = _prog(601, 501, "2026-11-03", actualizado="2026-10-20T10:00:00.0")
    c = cambios([vieja, olvidada], [p])
    assert c.anadir == []
    assert c.actualizar[1]["fecha"] == "2026-11-03"
    assert c.avisos.get(2) is None and YA_NO_ESTA not in c.avisos.values()


def test_r5_la_sesion_mandada_hace_10_dias_y_arrastrada_a_manana_se_mueve(tmp_path):
    store = DataStore(tmp_path / "t.db")
    planes = Planes(store.db_path)
    hace_10 = (HOY - timedelta(days=10)).isoformat()
    manana = (HOY + timedelta(days=1)).isoformat()
    planes.crear(_plan([{"apartado": 0, "fecha": hace_10, "titulo": "Entreno inventado 1", "bloques": ["45' a 5:35"],
                         "clave": False},
                        {"apartado": 0, "fecha": hace_10, "titulo": "Otra vieja", "bloques": ["30' suave"],
                         "clave": False}]), CREADO)
    with sqlite3.connect(store.db_path) as conn:
        uno, otra = (r for (r,) in conn.execute("SELECT rowid FROM plan_sesion ORDER BY orden"))
    planes.marcar_garmin(uno, 8001, 9001, datetime(2026, 10, 20, 9), "Entreno inventado 1: 45' a 5:35")
    planes.marcar_garmin(otra, 8002, 9002, datetime(2026, 10, 20, 9), "Otra vieja: 30' suave")
    client = _Garmin([_item(1, manana)])
    _pasada(store, client, HOY)
    filas = {f[0]: f for f in _filas(store.db_path)}
    assert len(filas) == 2
    reloj = {s["id"]: s for s in planes.sesiones_reloj(planes.en_curso(HOY))}
    assert reloj[uno]["fecha"] == manana and reloj[uno]["aviso"] is None
    assert reloj[otra]["fecha"] == hace_10 and reloj[otra]["aviso"] is None


# -- R6: a base of today opens the same --------------------------------------------------------------------------------

def test_r6_una_base_de_hoy_abre_igual_y_abrirla_dos_veces_no_rehace_nada(tmp_path):
    db = tmp_path / "data.sqlite3"
    planes = Planes(db)
    planes.crear(_plan([{"apartado": 0, "fecha": "2026-11-05", "titulo": "Rodaje", "bloques": ["45' a 5:35"],
                         "clave": True}]), CREADO)
    with sqlite3.connect(db) as conn:  # the schema of d222260: without the two columns of the 113
        for columna in ("deporte", "garmin_revisado"):
            if columna in {f[1] for f in conn.execute("PRAGMA table_info(plan_sesion)")}:
                conn.execute(f"ALTER TABLE plan_sesion DROP COLUMN {columna}")
        conn.execute("UPDATE plan_sesion SET garmin_workout_id = 501, garmin_schedule_id = 601, "
                     "garmin_aviso = 'Ya no está en tu Garmin', garmin_fecha = fecha")
        antes = conn.execute("SELECT rowid, * FROM plan_sesion").fetchall()
        columnas_antes = [f[1] for f in conn.execute("PRAGMA table_info(plan_sesion)")]
    Planes(db)
    with sqlite3.connect(db) as conn:
        columnas = [f[1] for f in conn.execute("PRAGMA table_info(plan_sesion)")]
        assert columnas == [*columnas_antes, "deporte", "garmin_revisado"]
        despues = conn.execute(f"SELECT rowid, {', '.join(columnas_antes)}, deporte, garmin_revisado "  # noqa: S608
                               "FROM plan_sesion").fetchall()
    assert [f[:-2] for f in despues] == antes
    assert [f[-2:] for f in despues] == [(None, None)]
    Planes(db)
    with sqlite3.connect(db) as conn:
        assert [f[1] for f in conn.execute("PRAGMA table_info(plan_sesion)")] == columnas
        assert conn.execute("SELECT rowid, * FROM plan_sesion").fetchall() == despues


def test_r5_un_entreno_de_la_biblioteca_programado_otra_vez_no_se_lleva_la_sesion_vieja():  # límite de R5
    # paired by day long ago with a workout of Garmin's library (not sent from TriData): a new scheduling of that
    # same workout is another session, the old one stays on its day
    vieja = _ses(1, "2026-10-20", "Rodaje", "carrera", garmin_workout_id=501, garmin_schedule_id=601,
                 garmin_actualizado="2026-10-18T10:00:00.0", garmin_enviada=None, fuera=True)
    c = cambios([vieja], [_prog(602, 501, "2026-11-03")])
    assert c.actualizar == {} and len(c.anadir) == 1
    enviada = {**vieja, "garmin_enviada": "2026-10-18T09:00:00"}  # sent from TriData: its own workout, it moves
    assert cambios([enviada], [_prog(602, 501, "2026-11-03")]).actualizar[1]["fecha"] == "2026-11-03"
