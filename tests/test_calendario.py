"""Unit 043 (REC-27 of `mandar-sesion-reloj`): the sessions of the plan in Google Calendar (`calendario/`).

R2, R3, R5 · C-207, C-208, C-209, C-211, G-59. Google is ALWAYS doubled (`GoogleDoble`, the same chained interface as
`googleapiclient`: `servicio.events().list(...).execute()`): no test talks to Google. Every value is invented.
"""
from __future__ import annotations

import json
import stat
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httplib2
import pytest
from googleapiclient.errors import HttpError
from test_sync import _StubClient

from tridata.calendario import ALCANCE, DESDE_HECHOS, NOMBRE, Calendario, ErrorCalendario
from tridata.calendario.sincronizar import CLAVE_HECHO, cuerpo_del_evento, cuerpo_del_hecho, sincronizar
from tridata.models import Activity
from tridata.plan import Planes
from tridata.storage import DataStore
from tridata.sync import SyncService
from tridata.web.calendario import Sincronizacion

HOY = date(2026, 10, 26)  # Monday
AHORA = datetime(2026, 10, 26, 9, 0)
MARCADOR = "MARCADOR-TOKEN-INVENTADO-7f3a"
TOKEN = json.dumps({"token": MARCADOR, "refresh_token": MARCADOR + "-r", "client_id": "cliente-inventado",
                    "client_secret": MARCADOR + "-s", "scopes": [ALCANCE]})

SERIES = {"apartado": 0, "fecha": "2026-10-28", "titulo": "Series 4×12' a 4:00",
          "bloques": ["20' suave", "4×12' a 4:00 (2' rec)", "10' vuelta a la calma"], "clave": True}
RODAJE = {"apartado": 0, "fecha": "2026-10-29", "titulo": "Rodaje 40'", "bloques": ["40' a 5:40"], "clave": False}
TIRADA = {"apartado": 0, "fecha": "2026-11-01", "titulo": "Tirada larga 1h30", "bloques": ["1h30 a 5:20"],
          "clave": True}
PASADA = {"apartado": 0, "fecha": "2026-10-20", "titulo": "Rodaje de la semana pasada", "bloques": ["35' suave"],
          "clave": False}


def plan_inventado(sesiones: list[dict], **cambios) -> dict:
    plan = {"nombre": "Road to Inventada 26", "apartados": [{"titulo": "Semanas", "parte": "previsto"}],
            "carreras": [{"apartado": 0, "nombre": "Maratón inventado", "fecha": "2026-12-06", "objetivo": "3:05",
                          "estado": "pendiente", "principal": True}],
            "semanas": [], "sesiones": sesiones, "nutricion": [], "incidencias": [], "notas": []}
    plan.update(cambios)
    return plan


# -- the double of Google ---------------------------------------------------------------------------------------------

class _Llamada:
    def __init__(self, doble: GoogleDoble, f: Any) -> None:
        self._doble, self._f = doble, f

    def execute(self) -> Any:
        if self._doble.fallo is not None:
            raise self._doble.fallo
        return self._f()


class _Recurso:
    def __init__(self, doble: GoogleDoble, nombre: str) -> None:
        self._doble, self._nombre = doble, nombre

    def __getattr__(self, metodo: str) -> Any:
        def llamar(**kwargs: Any) -> _Llamada:
            self._doble.llamadas.append((self._nombre, metodo, kwargs))
            return _Llamada(self._doble, lambda: getattr(self._doble, f"_{self._nombre}_{metodo}")(**kwargs))
        return llamar


class GoogleDoble:
    """A Google Calendar in memory: its calendars and the events of each one, and every call made."""

    def __init__(self, calendarios: list[dict] | None = None) -> None:
        self.calendarios: list[dict] = list(calendarios or [])
        self.eventos: dict[str, dict[str, dict]] = {c["id"]: {} for c in self.calendarios}
        self.llamadas: list[tuple[str, str, dict]] = []
        self.fallo: Exception | None = None
        self._n = 0

    def _nuevo_id(self, prefijo: str) -> str:
        self._n += 1
        return f"{prefijo}-{self._n}"

    def calendarList(self) -> _Recurso:  # noqa: N802 - the name of the Google API
        return _Recurso(self, "calendarList")

    def calendars(self) -> _Recurso:
        return _Recurso(self, "calendars")

    def events(self) -> _Recurso:
        return _Recurso(self, "events")

    def _calendarList_list(self, **_: Any) -> dict:
        # what Google answers: calendarList.list does not accept the scope `calendar.app.created`
        raise HttpError(httplib2.Response({"status": 403}), b"{}")

    def _calendars_get(self, calendarId: str) -> dict:  # noqa: N803
        calendario = next((c for c in self.calendarios if c["id"] == calendarId), None)
        if calendario is None:
            raise HttpError(httplib2.Response({"status": 404}), b"{}")
        return dict(calendario)

    def _calendars_insert(self, body: dict) -> dict:
        nuevo = {"id": self._nuevo_id("cal"), **body}
        self.calendarios.append(nuevo)
        self.eventos[nuevo["id"]] = {}
        return dict(nuevo)

    def _events_list(self, calendarId: str, pageToken: str | None = None, **_: Any) -> dict:  # noqa: N803
        items = [json.loads(json.dumps(e)) for e in self.eventos[calendarId].values()]
        # two pages, so the pagination of `Calendario.eventos` is exercised
        if pageToken is None and len(items) > 1:
            return {"items": items[:1], "nextPageToken": "pagina-2"}
        return {"items": items[1:] if pageToken else items}

    def _events_insert(self, calendarId: str, body: dict) -> dict:  # noqa: N803
        nuevo = {**json.loads(json.dumps(body)), "id": self._nuevo_id("ev")}
        self.eventos[calendarId][nuevo["id"]] = nuevo
        return dict(nuevo)

    def _events_patch(self, calendarId: str, eventId: str, body: dict) -> dict:  # noqa: N803
        self.eventos[calendarId][eventId].update(json.loads(json.dumps(body)))
        return dict(self.eventos[calendarId][eventId])

    def _events_delete(self, calendarId: str, eventId: str) -> None:  # noqa: N803
        del self.eventos[calendarId][eventId]

    # -- helpers of the tests

    def de(self, calendario: str) -> list[dict]:
        return list(self.eventos[calendario].values())

    def escrituras(self) -> list[tuple[str, str, dict]]:
        return [ll for ll in self.llamadas if ll[1] in ("insert", "patch", "delete", "update")]


def evento_de(sesion: str, **campos: Any) -> dict:
    return {"summary": "viejo", "description": "viejo", "start": {"date": "2026-10-28"},
            "end": {"date": "2026-10-29"}, "extendedProperties": {"private": {"tridata_sesion": sesion}}, **campos}


class Base:
    def __init__(self, tmp_path: Path, doble: GoogleDoble | None = None) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.token = tmp_path / "google-token.json"
        self.cliente = tmp_path / "cliente-inventado.json"
        self.cliente.write_text("{}", encoding="utf-8")
        self.planes = Planes(self.db)
        self.google = doble or GoogleDoble([{"id": "cal-otro", "summary": "Trabajo inventado"}])
        self.flujos: list[str] = []
        self.calendario = Calendario(self.db, self.token, str(self.cliente), flujo=self._flujo,
                                     servicio=lambda token: self.google)

    def _flujo(self, cliente: str) -> str:
        self.flujos.append(cliente)
        return TOKEN

    def conectar(self) -> None:
        self.calendario.conectar()

    def sesion(self, plan_id: int, orden: int) -> str:
        return f"{plan_id}-{orden}"

    def cal(self) -> str:
        return next(c["id"] for c in self.google.calendarios if c["summary"] == NOMBRE)


@pytest.fixture
def b(tmp_path) -> Base:
    return Base(tmp_path)


# -- R1: connecting -------------------------------------------------------------------------------------------------

def test_conectar_guarda_el_token_solo_en_el_pc_con_permisos_600_y_reutiliza_tridata(b):  # R1 · C-206
    assert not b.calendario.conectado
    b.conectar()
    assert b.flujos == [str(b.cliente)]
    assert b.calendario.conectado
    assert json.loads(b.token.read_text(encoding="utf-8"))["token"] == MARCADOR
    assert stat.S_IMODE(b.token.stat().st_mode) == 0o600
    creado = b.cal()
    b.conectar()  # connecting again reuses the «TriData» it made
    assert [c["summary"] for c in b.google.calendarios].count(NOMBRE) == 1 and b.cal() == creado
    assert len([ll for ll in b.google.llamadas if ll[:2] == ("calendars", "insert")]) == 1
    assert not [ll for ll in b.google.llamadas if ll[0] == "calendarList"]  # not allowed by the scope


def test_si_andres_borro_tridata_se_crea_otro(b):  # R1
    b.conectar()
    b.google.calendarios = [c for c in b.google.calendarios if c["summary"] != NOMBRE]
    b.planes.crear(plan_inventado([SERIES]), AHORA)
    sincronizar(b.planes, b.calendario, HOY)
    assert [c["summary"] for c in b.google.calendarios].count(NOMBRE) == 1
    assert [e["summary"] for e in b.google.de(b.cal())] == ["🏃 Series 4×12' a 4:00"]


def test_conectar_crea_tridata_si_no_esta_y_otra_vez_no_crea_otro(tmp_path):  # R1 · C-206
    b = Base(tmp_path, GoogleDoble([{"id": "cal-otro", "summary": "Trabajo inventado"}]))
    b.conectar()
    b.conectar()
    assert [c["summary"] for c in b.google.calendarios].count(NOMBRE) == 1
    assert [ll[2]["body"] for ll in b.google.llamadas if ll[:2] == ("calendars", "insert")] == [{"summary": NOMBRE}]


def test_el_permiso_es_solo_el_de_los_calendarios_que_crea_la_app():  # R1
    assert ALCANCE == "https://www.googleapis.com/auth/calendar.app.created"
    assert NOMBRE == "TriData"


# -- R2, R3: the events ---------------------------------------------------------------------------------------------

def test_cada_sesion_prevista_es_un_evento_de_todo_el_dia(b):  # R2 · C-207, G-59
    plan_id = b.planes.crear(plan_inventado([SERIES]), AHORA)
    b.conectar()
    resumen = sincronizar(b.planes, b.calendario, HOY)
    assert resumen == {"creados": 1, "cambiados": 0, "borrados": 0}
    (evento,) = b.google.de(b.cal())
    assert evento["summary"] == "🏃 Series 4×12' a 4:00"
    assert evento["description"] == "20' suave\n4×12' a 4:00 (2' rec)\n10' vuelta a la calma"
    assert evento["start"] == {"date": "2026-10-28"} and evento["end"] == {"date": "2026-10-29"}
    assert evento["extendedProperties"] == {"private": {"tridata_sesion": b.sesion(plan_id, 0)}}


def test_sincronizar_crea_actualiza_conservando_la_hora_y_borra_y_es_idempotente(b):  # R3 · C-208, C-209
    plan_id = b.planes.crear(plan_inventado([SERIES, RODAJE, TIRADA]), AHORA)
    b.conectar()
    cal = b.cal()
    # Andrés moved the series to 19:00 the day before (an old version of it); a session that left the plan; an event
    # of a past day of a session no longer there (history: untouched); one of his own in TriData (untouched)
    movido = b.google._events_insert(cal, evento_de(
        b.sesion(plan_id, 0), start={"dateTime": "2026-10-27T19:00:00+01:00", "timeZone": "Europe/Madrid"},
        end={"dateTime": "2026-10-27T20:15:00+01:00", "timeZone": "Europe/Madrid"}))
    quitado = b.google._events_insert(cal, evento_de(b.sesion(plan_id, 9), start={"date": "2026-10-30"},
                                                     end={"date": "2026-10-31"}))
    # unit 054 (R5): history is an event of a past day of ANOTHER plan (`0-8`: no plan has the id 0); one of the
    # plan in course whose session left is deleted whatever its day (`test_las_sesiones_pasadas_tambien_van`)
    pasado = b.google._events_insert(cal, evento_de("0-8", start={"date": "2026-10-19"},
                                                    end={"date": "2026-10-20"}))
    suyo = b.google._events_insert(cal, {"summary": "Fisio inventado", "start": {"date": "2026-10-30"},
                                         "end": {"date": "2026-10-31"}})

    assert sincronizar(b.planes, b.calendario, HOY) == {"creados": 2, "cambiados": 1, "borrados": 1}

    eventos = {e["id"]: e for e in b.google.de(cal)}
    assert quitado["id"] not in eventos
    assert pasado["id"] in eventos and suyo["id"] in eventos
    series = eventos[movido["id"]]
    assert series["summary"] == "🏃 Series 4×12' a 4:00"
    assert series["description"] == "20' suave\n4×12' a 4:00 (2' rec)\n10' vuelta a la calma"
    # the day changes to the 28th, the 19:00 and the 1h15 stay
    assert series["start"] == {"dateTime": "2026-10-28T19:00:00+01:00", "timeZone": "Europe/Madrid"}
    assert series["end"] == {"dateTime": "2026-10-28T20:15:00+01:00", "timeZone": "Europe/Madrid"}
    sesiones = [e["extendedProperties"]["private"]["tridata_sesion"] for e in eventos.values()
                if "extendedProperties" in e]
    assert sorted(sesiones) == sorted([b.sesion(plan_id, n) for n in (0, 1, 2)] + ["0-8"])

    escrito = len(b.google.escrituras())
    assert sincronizar(b.planes, b.calendario, HOY) == {"creados": 0, "cambiados": 0, "borrados": 0}
    assert len(b.google.escrituras()) == escrito  # a second pass writes nothing


def test_si_la_sesion_cambia_su_evento_cambia_y_sigue_a_las_19(b):  # R3 · C-208 (portante)
    plan_id = b.planes.crear(plan_inventado([SERIES, RODAJE]), AHORA)
    b.conectar()
    sincronizar(b.planes, b.calendario, HOY)
    cal = b.cal()
    (series,) = [e for e in b.google.de(cal) if e["start"].get("date") == "2026-10-28"]
    # Andrés puts it at 19:00 in Google Calendar
    b.google._events_patch(cal, series["id"], {"start": {"dateTime": "2026-10-28T19:00:00+01:00"},
                                               "end": {"dateTime": "2026-10-28T20:00:00+01:00"}})
    # the coach changes it to 3×10' (the same UPDATE the card of the 041 does)
    with b.planes._connect() as conn:
        conn.execute("UPDATE plan_sesion SET titulo = ?, bloques = ? WHERE plan_id = ? AND orden = 0",
                     ("Series 3×10' a 4:00", json.dumps(["20' suave", "3×10' a 4:00 (2' rec)", "10' suave"]),
                      plan_id))

    assert sincronizar(b.planes, b.calendario, HOY) == {"creados": 0, "cambiados": 1, "borrados": 0}
    de_la_sesion = [e for e in b.google.de(cal)
                    if e["extendedProperties"]["private"]["tridata_sesion"] == b.sesion(plan_id, 0)]
    assert len(de_la_sesion) == 1  # never two events of the same session
    (evento,) = de_la_sesion
    assert evento["id"] == series["id"]
    assert evento["summary"] == "🏃 Series 3×10' a 4:00"
    assert evento["description"] == "20' suave\n3×10' a 4:00 (2' rec)\n10' suave"
    assert evento["start"] == {"dateTime": "2026-10-28T19:00:00+01:00"}
    assert evento["end"] == {"dateTime": "2026-10-28T20:00:00+01:00"}


def test_si_se_quita_del_plan_su_evento_se_borra(b):  # R3 · C-209
    plan_id = b.planes.crear(plan_inventado([SERIES, RODAJE]), AHORA)
    b.conectar()
    sincronizar(b.planes, b.calendario, HOY)
    with b.planes._connect() as conn:
        conn.execute("DELETE FROM plan_sesion WHERE plan_id = ? AND orden = 1", (plan_id,))
    assert sincronizar(b.planes, b.calendario, HOY) == {"creados": 0, "cambiados": 0, "borrados": 1}
    assert [e["summary"] for e in b.google.de(b.cal())] == ["🏃 Series 4×12' a 4:00"]


def test_dos_eventos_de_la_misma_sesion_quedan_en_uno(b):  # R3
    plan_id = b.planes.crear(plan_inventado([SERIES]), AHORA)
    b.conectar()
    cal = b.cal()
    for _ in range(2):
        b.google._events_insert(cal, evento_de(b.sesion(plan_id, 0)))
    resumen = sincronizar(b.planes, b.calendario, HOY)
    assert resumen["borrados"] == 1 and resumen["creados"] == 0
    assert len(b.google.de(cal)) == 1




def test_el_evento_guarda_su_id_en_la_base(b):  # R3: the eventId of each session is kept in the SQLite
    plan_id = b.planes.crear(plan_inventado([SERIES]), AHORA)
    b.conectar()
    sincronizar(b.planes, b.calendario, HOY)
    (evento,) = b.google.de(b.cal())
    assert b.calendario.eventos_guardados() == {b.sesion(plan_id, 0): evento["id"]}


def test_solo_el_plan_en_curso(b):  # R2: the sessions of the plan of the current objective
    b.planes.crear(plan_inventado([RODAJE], carreras=[{"apartado": 0, "nombre": "Carrera pasada inventada",
                                                       "fecha": "2026-10-01", "objetivo": "", "estado": "",
                                                       "principal": True}]), AHORA)
    b.planes.crear(plan_inventado([SERIES]), AHORA)
    b.conectar()
    sincronizar(b.planes, b.calendario, HOY)
    assert [e["summary"] for e in b.google.de(b.cal())] == ["🏃 Series 4×12' a 4:00"]


def test_sin_plan_no_hay_eventos(b):  # R2
    b.conectar()
    assert sincronizar(b.planes, b.calendario, HOY) == {"creados": 0, "cambiados": 0, "borrados": 0}


@pytest.mark.parametrize("titulo, icono", [("Rodillo 1h en Z2", "🚴"), ("Bici larga 3h", "🚴"),
                                           ("Natación técnica 2000 m", "🏊"), ("Piscina 40'", "🏊"),
                                           ("Fuerza en el gimnasio", "🏋️"), ("Series 4×1000", "🏃")])
def test_el_tipo_de_sesion_en_el_titulo(titulo, icono):  # R2 · G-59
    cuerpo = cuerpo_del_evento("1-0", {"fecha": "2026-10-28", "titulo": titulo, "bloques": []})
    assert cuerpo["summary"] == f"{icono} {titulo}"


# -- R5: only the session goes to Google ----------------------------------------------------------------------------

def test_al_calendario_solo_va_el_tipo_el_dia_y_los_bloques(b):  # R5 · C-211, G-59
    con_pulso = {**SERIES, "bloques": ["20' suave a 135-145 ppm", "4×12' a 4:00 (FC < 172 lpm)", "10' suave"]}
    b.planes.crear(plan_inventado([con_pulso], nombre="Road to Plan Secreto Inventado",
                                  incidencias=[{"apartado": 0, "fecha": "2026-10-24",
                                                "texto": "Molestia inventada en el gemelo"}],
                                  notas=[{"apartado": 0, "tipo": "nota", "texto": "Nota inventada del plan"}]), AHORA)
    b.conectar()
    sincronizar(b.planes, b.calendario, HOY)
    (insertado,) = [ll[2]["body"] for ll in b.google.llamadas if ll[:2] == ("events", "insert")]
    assert set(insertado) == {"summary", "description", "start", "end", "extendedProperties"}
    assert insertado["description"] == "20' suave\n4×12' a 4:00\n10' suave"
    enviado = json.dumps([ll[2] for ll in b.google.llamadas], ensure_ascii=False)
    for prohibido in ("ppm", "lpm", "135", "172", "FC", "Plan Secreto", "Molestia", "Nota inventada", "Maratón"):
        assert prohibido not in enviado, prohibido


# -- R4: failures -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("fallo, dice", [
    (HttpError(httplib2.Response({"status": 401}), b'{"error": "' + MARCADOR.encode() + b'"}'), "permiso"),
    (OSError("sin red inventada"), "no responde"),
])
def test_si_google_falla_es_un_error_que_se_puede_decir(b, fallo, dice):  # R4 · C-210
    b.planes.crear(plan_inventado([SERIES]), AHORA)
    b.conectar()
    b.google.fallo = fallo
    with pytest.raises(ErrorCalendario) as error:
        sincronizar(b.planes, b.calendario, HOY)
    assert dice in str(error.value)
    assert MARCADOR not in str(error.value) and "inventada" not in str(error.value)
    b.google.fallo = None  # the next pass works again
    assert sincronizar(b.planes, b.calendario, HOY)["creados"] == 1


def test_sin_conectar_no_se_habla_con_google(tmp_path):  # R6
    construido: list[Path] = []
    calendario = Calendario(tmp_path / "data.sqlite3", tmp_path / "google-token.json", None,
                            servicio=lambda token: construido.append(token))
    assert not calendario.conectado
    with pytest.raises(ErrorCalendario):
        sincronizar(Planes(tmp_path / "data.sqlite3"), calendario, HOY)
    assert construido == []


# == unit 054: what was really trained (Garmin) and the past sessions, in «TriData» ===================================
#
# R1-R6, R8 of `054-calendario-entrenos-hechos` (P-20261029-821f09b9, P-20261029-6cf17cd2). The activities live in a
# temporary `DataStore` (the same SQLite), Garmin is the `_StubClient` of `test_sync` and Google the `GoogleDoble`:
# nothing talks to Garmin or to Google. Every value is invented.

HOY_HECHOS = date(2026, 10, 29)  # Thursday
ZONA = "Europe/Madrid"
LUGAR = "Lugar Inventado de Prueba"  # the name of the activity in Garmin often carries the place: never to Google


def hecho(ident: str, dia: str, tipo: str, segundos: float, metros: float | None, hora: str | None = "18:30:00",
          **extra: Any) -> Activity:
    return Activity(activity_id=ident, activity_date=date.fromisoformat(dia), activity_type=tipo,
                    name=f"{LUGAR} {ident}", duration_seconds=segundos, distance_meters=metros, avg_hr=157,
                    max_hr=177, calories=733, training_effect_aerobic=3.7, avg_pace_seconds_per_km=307,
                    start_time_local=f"{dia} {hora}" if hora else None, **extra)


CARRERA = hecho("h-carrera", "2026-10-27", "running", 3120, 10200.0)
NATACION = hecho("h-natacion", "2026-10-27", "lap_swimming", 1800, 1500.0, hora="07:15:00")
FUERZA = hecho("h-fuerza", "2026-10-27", "strength_training", 2700, None, hora="20:00:00")
TEMPO = hecho("h-tempo", "2026-10-28", "running", 3600, 12000.0, hora="19:00:00")
JULIO = hecho("h-julio", "2026-07-31", "running", 2400, 8000.0)


class BaseHechos(Base):
    def __init__(self, tmp_path: Path, doble: GoogleDoble | None = None) -> None:
        super().__init__(tmp_path, doble)
        self.store = DataStore(self.db)

    def pasada(self, hoy: date = HOY_HECHOS) -> dict[str, int]:
        return sincronizar(self.planes, self.calendario, hoy, self.store, zona=ZONA)

    def hechos(self) -> dict[str, dict]:
        """`{activity id: its event}` in «TriData»; an activity with two events fails here."""
        eventos = [e for e in self.google.de(self.cal())
                   if CLAVE_HECHO in ((e.get("extendedProperties") or {}).get("private") or {})]
        ids = [e["extendedProperties"]["private"][CLAVE_HECHO] for e in eventos]
        assert len(ids) == len(set(ids)), "two events of the same activity"
        return {e["extendedProperties"]["private"][CLAVE_HECHO]: e for e in eventos}


@pytest.fixture
def h(tmp_path) -> BaseHechos:
    return BaseHechos(tmp_path)


# -- R1, R2: the event of a training done ----------------------------------------------------------------------------

def test_el_hecho_es_un_evento_a_su_hora_con_deporte_duracion_y_distancia():  # R1, R2 (criterio portante)
    assert DESDE_HECHOS == date(2026, 8, 1)
    assert cuerpo_del_hecho(CARRERA.to_dict(), ZONA) == {
        "summary": "✅ 🏃 Carrera · 52' · 10,2 km", "description": "Hecho según Garmin",
        "start": {"dateTime": "2026-10-27T18:30:00+01:00", "timeZone": ZONA},
        "end": {"dateTime": "2026-10-27T19:22:00+01:00", "timeZone": ZONA},
        "extendedProperties": {"private": {CLAVE_HECHO: "h-carrera"}}}


@pytest.mark.parametrize("actividad, titulo", [
    (NATACION, "✅ 🏊 Natación · 30' · 1500 m"),  # the swim in metres
    (FUERZA, "✅ 🏋️ Fuerza · 45'"),  # without distance
    (hecho("h-bici", "2026-10-25", "road_biking", 5400, 45000.0), "✅ 🚴 Bici · 90' · 45,0 km"),
    (hecho("h-cinta", "2026-10-25", "treadmill_running", 1500, 5000.0), "✅ 🏃 Carrera · 25' · 5,0 km"),
    (hecho("h-otro", "2026-10-25", "hiking", 4500, 9300.0), "✅ 🏃 Entreno · 75' · 9,3 km"),
])
def test_el_titulo_dice_el_deporte_los_minutos_y_la_distancia(actividad, titulo):  # R1
    assert cuerpo_del_hecho(actividad.to_dict(), ZONA)["summary"] == titulo


def test_en_verano_la_hora_lleva_su_desfase():  # R1: its real hour, the offset of that day
    agosto = hecho("h-agosto", "2026-08-04", "running", 2400, 8000.0, hora="07:05:00")
    cuerpo = cuerpo_del_hecho(agosto.to_dict(), ZONA)
    assert cuerpo["start"] == {"dateTime": "2026-08-04T07:05:00+02:00", "timeZone": ZONA}
    assert cuerpo["end"] == {"dateTime": "2026-08-04T07:45:00+02:00", "timeZone": ZONA}


def test_al_calendario_solo_va_deporte_hora_duracion_y_distancia(h):  # R2 · G-59 ampliada
    h.store.save_activities([CARRERA, NATACION, FUERZA])
    h.conectar()
    h.pasada()
    insertados = [ll[2]["body"] for ll in h.google.llamadas if ll[:2] == ("events", "insert")]
    assert len(insertados) == 3
    for cuerpo in insertados:
        assert set(cuerpo) == {"summary", "description", "start", "end", "extendedProperties"}
        assert cuerpo["description"] in ("", "Hecho según Garmin")
    enviado = json.dumps([ll[2] for ll in h.google.llamadas], ensure_ascii=False)
    for prohibido in (LUGAR, "157", "177", "733", "3.7", "307", "ppm", "kcal", "ritmo", "Inventado"):
        assert prohibido not in enviado, prohibido


# -- R3: idempotent, follows Garmin, nothing before the 1st of August -------------------------------------------------

def test_los_hechos_desde_agosto_llegan_una_vez_y_siguen_a_garmin(h):  # R3
    h.store.save_activities([CARRERA, NATACION, FUERZA, JULIO])
    h.conectar()
    assert h.pasada() == {"creados": 3, "cambiados": 0, "borrados": 0}
    assert set(h.hechos()) == {"h-carrera", "h-natacion", "h-fuerza"}  # nothing of July

    escrito = len(h.google.escrituras())
    assert h.pasada() == {"creados": 0, "cambiados": 0, "borrados": 0}  # a second pass writes nothing
    assert len(h.google.escrituras()) == escrito

    # it changes in Garmin (longer and further): its event changes, the same one
    antes = h.hechos()["h-carrera"]["id"]
    h.store.save_activities([hecho("h-carrera", "2026-10-27", "running", 3600, 11000.0)])
    assert h.pasada() == {"creados": 0, "cambiados": 1, "borrados": 0}
    carrera = h.hechos()["h-carrera"]
    assert carrera["id"] == antes and carrera["summary"] == "✅ 🏃 Carrera · 60' · 11,0 km"
    assert carrera["end"]["dateTime"] == "2026-10-27T19:30:00+01:00"

    # it leaves the base: its event is deleted
    with h.store._connect() as conn:
        conn.execute("DELETE FROM activities WHERE activity_id = 'h-fuerza'")
    assert h.pasada() == {"creados": 0, "cambiados": 0, "borrados": 1}
    assert set(h.hechos()) == {"h-carrera", "h-natacion"}


# -- R4: an activity saved without its hour ---------------------------------------------------------------------------

def test_sin_hora_va_todo_el_dia_y_pasa_a_su_hora_cuando_la_rutina_la_trae(h):  # R4
    h.store.save_activities([hecho("h-carrera", "2026-10-27", "running", 3120, 10200.0, hora=None)])
    h.conectar()
    h.pasada()
    (evento,) = h.hechos().values()
    assert evento["start"] == {"date": "2026-10-27"} and evento["end"] == {"date": "2026-10-28"}

    garmin = _StubClient(activities=[CARRERA])
    SyncService(garmin, h.store).rellenar_horas(HOY_HECHOS)
    assert garmin.get_activities_calls == [(DESDE_HECHOS, HOY_HECHOS)]  # one call for every missing hour

    assert h.pasada() == {"creados": 0, "cambiados": 1, "borrados": 0}
    (ahora,) = h.hechos().values()
    assert ahora["id"] == evento["id"]
    assert ahora["start"].get("dateTime") == "2026-10-27T18:30:00+01:00" and not ahora["start"].get("date")
    assert ahora["end"].get("dateTime") == "2026-10-27T19:22:00+01:00" and not ahora["end"].get("date")
    assert h.pasada() == {"creados": 0, "cambiados": 0, "borrados": 0}


# -- R5: every planned session of the plan in course, the past ones too ---------------------------------------------

def test_las_sesiones_pasadas_tambien_van(b):  # R5: the tempo moved to the 28th, synced on the 29th
    viejo = b.planes.crear(plan_inventado([PASADA], carreras=[{"apartado": 0, "nombre": "Carrera pasada inventada",
                                                               "fecha": "2026-10-01", "objetivo": "", "estado": "",
                                                               "principal": True}]), AHORA)
    actual = b.planes.crear(plan_inventado([PASADA, SERIES]), AHORA)
    b.conectar()
    cal = b.cal()
    historia = b.google._events_insert(cal, evento_de(b.sesion(viejo, 0), start={"date": "2026-09-20"},
                                                      end={"date": "2026-09-21"}))
    viejo_futuro = b.google._events_insert(cal, evento_de(b.sesion(viejo, 1), start={"date": "2026-10-30"},
                                                          end={"date": "2026-10-31"}))
    quitada_pasada = b.google._events_insert(cal, evento_de(b.sesion(actual, 7), start={"date": "2026-10-19"},
                                                            end={"date": "2026-10-20"}))

    assert sincronizar(b.planes, b.calendario, HOY_HECHOS) == {"creados": 2, "cambiados": 0, "borrados": 2}
    eventos = {e["id"]: e for e in b.google.de(cal)}
    assert historia["id"] in eventos  # another plan, a past day: history
    assert viejo_futuro["id"] not in eventos and quitada_pasada["id"] not in eventos
    del_plan = sorted(e["start"]["date"] for e in eventos.values()
                      if e["extendedProperties"]["private"]["tridata_sesion"].startswith(f"{actual}-"))
    assert del_plan == ["2026-10-20", "2026-10-28"]
    assert sincronizar(b.planes, b.calendario, HOY_HECHOS) == {"creados": 0, "cambiados": 0, "borrados": 0}


# -- R6: planned and done the same day ---------------------------------------------------------------------------------

def test_el_mismo_dia_van_el_previsto_y_el_hecho_sin_pisarse(h):  # R6
    plan_id = h.planes.crear(plan_inventado([SERIES]), AHORA)  # the 28th
    h.store.save_activities([TEMPO])  # the 28th, at 19:00
    h.conectar()
    assert h.pasada() == {"creados": 2, "cambiados": 0, "borrados": 0}
    eventos = h.google.de(h.cal())
    assert sorted(e["summary"] for e in eventos) == ["✅ 🏃 Carrera · 60' · 12,0 km", "🏃 Series 4×12' a 4:00"]
    assert h.pasada() == {"creados": 0, "cambiados": 0, "borrados": 0}

    # the training leaves: the session stays
    with h.store._connect() as conn:
        conn.execute("DELETE FROM activities")
    assert h.pasada() == {"creados": 0, "cambiados": 0, "borrados": 1}
    assert [e["summary"] for e in h.google.de(h.cal())] == ["🏃 Series 4×12' a 4:00"]

    # the session leaves: the training (back in the base) stays
    h.store.save_activities([TEMPO])
    with h.planes._connect() as conn:
        conn.execute("DELETE FROM plan_sesion WHERE plan_id = ?", (plan_id,))
    assert h.pasada() == {"creados": 1, "cambiados": 0, "borrados": 1}
    assert [e["summary"] for e in h.google.de(h.cal())] == ["✅ 🏃 Carrera · 60' · 12,0 km"]


# -- R8: Google fails half way through the first pass ------------------------------------------------------------------

class GoogleQueFalla(GoogleDoble):
    """Fails with a network error on the n-th insert, once."""

    def __init__(self, en: int) -> None:
        super().__init__([{"id": "cal-otro", "summary": "Trabajo inventado"}])
        self.en, self.inserts = en, 0

    def _events_insert(self, calendarId: str, body: dict) -> dict:  # noqa: N803
        self.inserts += 1
        if self.inserts == self.en:
            raise OSError("red inventada caída")
        return super()._events_insert(calendarId, body)


def test_si_google_falla_a_mitad_lo_creado_se_queda_y_la_siguiente_termina_sin_duplicar(tmp_path):  # R8
    h = BaseHechos(tmp_path, GoogleQueFalla(en=60))
    actividades = [hecho(f"h-{n}", (DESDE_HECHOS + timedelta(days=n % 89)).isoformat(), "running", 2400, 8000.0,
                         hora=f"{6 + n // 89:02d}:00:00") for n in range(130)]
    h.store.save_activities(actividades)
    h.planes.crear(plan_inventado([SERIES, RODAJE]), AHORA)
    h.conectar()
    sinc = Sincronizacion(h.calendario, h.planes, lambda: HOY_HECHOS, lambda: AHORA, store=h.store)

    estado = sinc.vuelta()
    assert estado["error"] and "reintentará" in estado["error"]
    creados = len(h.google.de(h.cal()))
    assert creados == 59  # what was already made stays

    estado = sinc.vuelta()
    assert estado["error"] is None
    assert len(h.hechos()) == 130  # every activity once
    assert len(h.google.de(h.cal())) == 132
    escrito = len(h.google.escrituras())
    assert sinc.vuelta()["error"] is None and len(h.google.escrituras()) == escrito


# -- unit 056: a ride of less than 50 minutes is a commute, not a training --------------------------------------------

def test_una_bici_de_menos_de_50_minutos_no_va_al_calendario(h):  # 056 R1
    h.store.save_activities([hecho("h-bici-corta", "2026-10-27", "cycling", 49 * 60, 9000.0),
                             hecho("h-rodillo-corto", "2026-10-28", "indoor_cycling", 30 * 60, None)])
    h.conectar()
    assert h.pasada() == {"creados": 0, "cambiados": 0, "borrados": 0}
    assert h.hechos() == {}


def test_la_bici_corta_que_ya_estaba_se_borra(h):  # 056 R1: the commutes sent by the 054 leave the calendar
    h.store.save_activities([hecho("h-bici", "2026-10-27", "cycling", 50 * 60, 9000.0)])
    h.conectar()
    assert h.pasada()["creados"] == 1
    h.store.save_activities([hecho("h-bici", "2026-10-27", "cycling", 49 * 60 + 59, 9000.0)])
    assert h.pasada() == {"creados": 0, "cambiados": 0, "borrados": 1}
    assert h.hechos() == {}


def test_la_bici_de_50_minutos_y_lo_corto_de_otros_deportes_siguen(h):  # 056 R2
    h.store.save_activities([hecho("h-bici-50", "2026-10-27", "road_biking", 50 * 60, 20000.0),
                             hecho("h-carrera-corta", "2026-10-27", "running", 30 * 60, 5000.0, hora="07:00:00"),
                             hecho("h-natacion-corta", "2026-10-28", "lap_swimming", 20 * 60, 800.0),
                             hecho("h-fuerza-corta", "2026-10-28", "strength_training", 15 * 60, None, hora="20:00:00")])
    h.conectar()
    assert h.pasada()["creados"] == 4
    assert set(h.hechos()) == {"h-bici-50", "h-carrera-corta", "h-natacion-corta", "h-fuerza-corta"}
