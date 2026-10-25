"""Unit 043 (REC-27 of `mandar-sesion-reloj`): the sessions of the plan in Google Calendar (`calendario/`).

R2, R3, R5 · C-207, C-208, C-209, C-211, G-59. Google is ALWAYS doubled (`GoogleDoble`, the same chained interface as
`googleapiclient`: `servicio.events().list(...).execute()`): no test talks to Google. Every value is invented.
"""
from __future__ import annotations

import json
import stat
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httplib2
import pytest
from googleapiclient.errors import HttpError

from tridata.calendario import ALCANCE, NOMBRE, Calendario, ErrorCalendario
from tridata.calendario.sincronizar import cuerpo_del_evento, sincronizar
from tridata.plan import Planes

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
        return {"items": [dict(c) for c in self.calendarios]}

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
        self.google = doble or GoogleDoble([{"id": "cal-tridata", "summary": NOMBRE}])
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
    assert [c["summary"] for c in b.google.calendarios] == [NOMBRE]  # reused, not a second one
    assert not [ll for ll in b.google.llamadas if ll[:2] == ("calendars", "insert")]


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
    pasado = b.google._events_insert(cal, evento_de(b.sesion(plan_id, 8), start={"date": "2026-10-19"},
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
    assert sorted(sesiones) == sorted([b.sesion(plan_id, n) for n in (0, 1, 2, 8)])

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


def test_las_sesiones_ya_pasadas_no_se_mandan(b):  # R2: «prevista» is today or a day to come
    b.planes.crear(plan_inventado([PASADA, SERIES]), AHORA)
    b.conectar()
    sincronizar(b.planes, b.calendario, HOY)
    assert [e["start"] for e in b.google.de(b.cal())] == [{"date": "2026-10-28"}]


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
