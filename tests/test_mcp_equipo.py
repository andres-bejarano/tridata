"""Unit 065 (REC-28 of `preguntar-equipo`, S-66 · R-186 to R-190): the team on the PC sees what the team of the app
sees and leaves its changes as cards.

The MCP server (`mcp_server.py`) gets two tools: `get_contexto`, the same context `conversaciones.contexto.montar`
builds for the Asistente (never transcribing a medical PDF), and `proponer_cambio`, which validates the change as the
app does, prepares the card with `nutricion.asistente.proponer` and leaves it in the conversation «Desde el PC» of the
Asistente (`conversaciones/desde_pc.py`). Andrés confirms it in the app, by the route of always.

Every datum is invented; the medical transcriber is a double that must never be called; nothing talks to Anthropic
nor to Google.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.calendario import Calendario
from tridata.conversaciones import Conversaciones
from tridata.conversaciones.contexto import montar
from tridata.conversaciones.desde_pc import PAPELES_PC, TITULO, Equipo, montar_equipo
from tridata.mcp_server import build_server
from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.plan import Planes
from tridata.storage import DataStore
from tridata.web import crear_app
from tridata.web.calendario import despierta
from tridata.web.conversaciones import cambio_no_entendido

HOY = date(2026, 10, 26)  # a Monday
AHORA = datetime(2026, 10, 26, 9, 0)
DOMINGO, SABADO = "2026-11-01", "2026-10-31"
TIRADA = {"apartado": 0, "fecha": DOMINGO, "titulo": "Tirada inventada 1h30", "bloques": ["1h30 a 5:20"],
          "clave": True}
QUITAR_DOMINGO = {"accion": "plan_quitar_sesion", "dia": DOMINGO}
PONER_SABADO = {"accion": "plan_anadir_sesion", "dia": SABADO, "texto": "Tirada inventada 1h30 | 1h30 a 5:20"}


def _plan() -> dict:
    return {"nombre": "Plan Inventado 26", "apartados": [{"titulo": "Semanas", "parte": "previsto"}],
            "carreras": [{"apartado": 0, "nombre": "Maratón inventado", "fecha": "2026-12-06", "objetivo": "3:05",
                          "estado": "pendiente", "principal": True}],
            "semanas": [], "sesiones": [TIRADA], "nutricion": [], "incidencias": [], "notas": []}


class Mundo:
    """A temporary SQLite with invented foods, weight, settings, trainings, a plan with a Sunday session and a folder
    with a medical PDF whose transcriber is a double that counts its calls."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1650, "kcal_por_paso": 0.04, "pasos_por_defecto": 9000,
                              "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.ajustes.anadir_carrera("10K inventado", "2026-12-13", 1)
        RegistroComidas(self.db).guardar([Alimento(nombre="gachas inventadas", dia=HOY, cantidad=100, unidad="g",
                                                   kcal=377)], hoy=HOY, texto="gachas", sin_cambios=True)
        RegistroPeso(self.db).apuntar(HOY, 70.4, hoy=HOY)
        self.store = DataStore(self.db)
        self.store.save_activities([Activity(
            activity_id="inv1", activity_date=date(2026, 10, 24), activity_type="running", name="Rodaje inventado",
            duration_seconds=2400, distance_meters=7000, avg_hr=144)])
        self.store.save_daily_stats(DailyStats(stat_date=HOY, steps=7777, resting_hr=48))
        self.store.save_sleep(SleepRecord(sleep_date=HOY, total_sleep_seconds=25200, sleep_score=77))
        self.store.save_hrv(HRVRecord(hrv_date=HOY, last_night_avg=61))
        self.planes = Planes(self.db)
        self.plan_id = self.planes.crear(_plan(), AHORA)
        self.carpeta = tmp_path / "medicos"
        self.carpeta.mkdir()
        (self.carpeta / "analitica-inventada.pdf").write_bytes(b"%PDF-1.4 inventado")
        self.transcriptor = MagicMock()
        self.token = tmp_path / "google-token.json"  # never written: Google Calendar is not connected
        self.equipo: Equipo = montar_equipo(self.db, store=self.store, medicos_dir=self.carpeta,
                                            transcriptor_medico=self.transcriptor, token=self.token,
                                            hoy=lambda: HOY, ahora=lambda: AHORA)
        self.server = build_server(self.store, equipo=self.equipo)

    def llamar(self, nombre: str, argumentos: dict | None = None) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos or {}))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)

    def volcado(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return list(conn.iterdump())
        finally:
            conn.close()

    def sesiones(self) -> list[tuple[str, str]]:
        return [(s["fecha"], s["titulo"]) for s in self.planes.leer(self.plan_id)["sesiones"]]

    def conversaciones(self) -> list[dict]:
        return Conversaciones(self.db).lista()


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


# -- R1 · R-186, C-230 (criterio portante) ---------------------------------------------------------------------------

def test_get_contexto_es_el_que_monta_la_app(mundo):  # R1
    texto = mundo.llamar("get_contexto")
    medicos = mundo.equipo.medicos
    esperado = montar(registro=RegistroComidas(mundo.db), pesos=RegistroPeso(mundo.db), ajustes=mundo.ajustes,
                      store=mundo.store, transcripciones=medicos.transcripciones(),
                      aviso_medico=medicos.estado()["aviso"], hoy=HOY, planes=mundo.planes,
                      calendario_conectado=False)
    assert texto == "\n\n".join(esperado.bloques)
    datos = json.loads(esperado.bloques[-1])
    assert datos["hoy"] == HOY.isoformat()
    for dato in ("gachas inventadas", "377", "70.4", "Plan Inventado 26", "Tirada inventada 1h30",
                 "Maratón inventado", "10K inventado", "Rodaje inventado", "7777"):
        assert dato in texto, dato


# -- R2 · R-187, C-231, C-234 ----------------------------------------------------------------------------------------

def test_proponer_deja_tarjeta_en_desde_el_pc_y_no_cambia_nada(mundo):  # R2
    antes = mundo.sesiones()
    r1 = mundo.llamar("proponer_cambio", {"papel": "entrenador", "texto": "Te paso la tirada al sábado.",
                                          "cambio": QUITAR_DOMINGO})
    assert "pendiente" in r1.lower() or "no está hecho" in r1
    assert "Desde el PC" in r1
    (conv,) = mundo.conversaciones()
    assert conv["titulo"] == TITULO == "Desde el PC"
    (mensaje,) = Conversaciones(mundo.db).mensajes(conv["id"])
    assert mensaje["quien"] == "asistente" and mensaje["papel"] == "entrenador"
    assert mensaje["texto"] == "Te paso la tirada al sábado."
    (tarjeta,) = mensaje["tarjetas"]
    assert tarjeta["tipo"] == "propuesta" and tarjeta["accion"] == "plan_quitar_sesion"
    assert mundo.sesiones() == antes  # the plan the same

    mundo.llamar("proponer_cambio", {"papel": "entrenador", "texto": "Y la pongo el sábado.",
                                     "cambio": PONER_SABADO})
    (otra,) = mundo.conversaciones()  # the same conversation, reused
    assert otra["id"] == conv["id"]
    assert [m["texto"] for m in Conversaciones(mundo.db).mensajes(conv["id"])] == [
        "Te paso la tirada al sábado.", "Y la pongo el sábado."]
    assert mundo.sesiones() == antes


# -- R3 · R-188, C-232 ----------------------------------------------------------------------------------------------

def test_confirmar_en_la_app_la_tarjeta_del_pc_la_aplica_y_despierta_el_calendario(mundo):  # R3
    mundo.llamar("proponer_cambio", {"papel": "entrenador", "texto": "Quito el domingo.", "cambio": QUITAR_DOMINGO})
    mundo.llamar("proponer_cambio", {"papel": "entrenador", "texto": "Pongo el sábado.", "cambio": PONER_SABADO})
    (conv,) = mundo.conversaciones()
    tarjetas = [t for m in Conversaciones(mundo.db).mensajes(conv["id"]) for t in m["tarjetas"]]
    calendario = Calendario(mundo.db, mundo.token, None)  # not connected: nothing talks to Google
    app = crear_app(RegistroComidas(mundo.db), MagicMock(), mundo.ajustes, hoy=lambda: HOY,
                    objetivos=ObjetivosDia(mundo.db), store=mundo.store, actualizar_garmin=MagicMock(),
                    ahora=lambda: AHORA, ordenador_plan=MagicMock(), conversador=MagicMock(), calendario=calendario)
    cliente = TestClient(app)
    for tarjeta in tarjetas:
        r = cliente.post("/api/asistente/confirmar", json={"tarjeta": tarjeta})
        assert r.status_code == 200, r.text
    assert mundo.sesiones() == [(SABADO, "Tirada inventada 1h30")]  # moved from Sunday to Saturday
    assert despierta("POST", "/api/asistente/confirmar")  # the 2xx of that request wakes the calendar (062)


# -- R4, R6 · R-189, C-233 ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("argumentos, campos", [
    ({"papel": "entrenador", "texto": "x", "cambio": {"dia": DOMINGO}}, ["accion"]),
    ({"papel": "fisio", "texto": "x", "cambio": {"accion": "plan_quitar_sesion", "dia": "el domingo"}}, ["dia"]),
])
def test_un_cambio_mal_formado_dice_que_corregir_sin_tarjeta(mundo, argumentos, campos):  # R4
    antes = mundo.volcado()
    respuesta = mundo.llamar("proponer_cambio", argumentos)
    assert respuesta == cambio_no_entendido(campos)
    assert mundo.conversaciones() == [] and mundo.volcado() == antes


@pytest.mark.parametrize("papel", ["médico", "asistente", ""])
def test_un_papel_desconocido_se_rechaza_con_los_validos(mundo, papel):  # R6
    antes = mundo.volcado()
    respuesta = mundo.llamar("proponer_cambio", {"papel": papel, "texto": "x", "cambio": QUITAR_DOMINGO})
    assert all(p in respuesta for p in PAPELES_PC) and set(PAPELES_PC) == {"entrenador", "nutricionista", "fisio"}
    assert "ninguna tarjeta" in respuesta
    assert mundo.conversaciones() == [] and mundo.volcado() == antes


# -- R5 · R-190 -----------------------------------------------------------------------------------------------------

def test_get_contexto_y_las_de_antes_no_escriben_ni_transcriben(mundo):  # R5
    antes = mundo.volcado()
    mundo.llamar("get_contexto")
    for nombre in ("list_activities", "get_daily_metrics", "get_training_load", "get_weekly_summary", "get_records"):
        mundo.llamar(nombre)
    mundo.llamar("get_activity", {"activity_id": "inv1"})
    assert mundo.volcado() == antes
    mundo.transcriptor.transcribir.assert_not_called()


def test_proponer_solo_escribe_la_conversacion_y_su_mensaje(mundo):  # R5
    def filas() -> dict[str, list]:
        conn = sqlite3.connect(mundo.db)
        try:
            tablas = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            return {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tablas}  # noqa: S608 - names of sqlite
        finally:
            conn.close()

    antes = filas()
    mundo.llamar("proponer_cambio", {"papel": "nutricionista", "texto": "Te lo dejo aquí.", "cambio": QUITAR_DOMINGO})
    despues = filas()
    cambiadas = {t for t in despues if despues[t] != antes.get(t)}
    assert cambiadas == {"conversacion", "mensaje"} | ({"sqlite_sequence"} & cambiadas)
    mundo.transcriptor.transcribir.assert_not_called()
