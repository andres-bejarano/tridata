"""Units 065 and 066 (REC-28, REC-29 of `preguntar-equipo`): the team on the PC sees what the team of the app sees
and, with Andrés's «sí», makes the change itself, without cards.

The MCP server (`mcp_server.py`) gives `get_contexto` (065, R-186: the same context `conversaciones.contexto.montar`
builds for the Asistente, never transcribing a medical PDF) and, since 066 (S-67, S-68, G-55, R-190..R-197),
`preparar_cambio` (what would be done, read-only, with its fingerprint), `hacer_cambio` (the same `proponer` +
`confirmar` of the app, Google Calendar included), `apuntar_comida` and `apuntar_peso` (the same checks and
repositories as the dictation). Every change made leaves a message in the conversation «Desde el PC»
(`conversaciones/desde_pc.py`); `proponer_cambio` (065) is gone.

Every datum is invented; the medical transcriber is a double that must never be called; Garmin and Google Calendar
are doubles; nothing talks to Anthropic.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from tridata.calendario import ErrorCalendario
from tridata.conversaciones import Conversaciones
from tridata.conversaciones.contexto import montar
from tridata.conversaciones.desde_pc import PAPELES_PC, TITULO, Equipo, montar_equipo
from tridata.mcp_server import build_server
from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas, RegistroPeso
from tridata.nutricion.peso import FUERA_DE_RANGO
from tridata.plan import Planes
from tridata.storage import DataStore
from tridata.web.conversaciones import cambio_no_entendido

HOY = date(2026, 10, 26)  # a Monday
AHORA = datetime(2026, 10, 26, 9, 0)
DOMINGO, SABADO = "2026-11-01", "2026-10-31"
TIRADA = {"apartado": 0, "fecha": DOMINGO, "titulo": "Tirada inventada 1h30", "bloques": ["1h30 a 5:20"],
          "clave": True}
QUITAR_DOMINGO = {"accion": "plan_quitar_sesion", "dia": DOMINGO}
PONER_SABADO = {"accion": "plan_anadir_sesion", "dia": SABADO, "texto": "Tirada inventada 1h30 | 1h30 a 5:20"}
AL_RELOJ = {"accion": "mandar_a_garmin", "dia": DOMINGO}
PAELLA = {"nombre": "paella inventada", "cantidad": 350, "unidad": "g", "kcal": 650}
HUELLA = re.compile(r"Huella: ([0-9a-f]{12})")


def _plan() -> dict:
    return {"nombre": "Plan Inventado 26", "apartados": [{"titulo": "Semanas", "parte": "previsto"}],
            "carreras": [{"apartado": 0, "nombre": "Maratón inventado", "fecha": "2026-12-06", "objetivo": "3:05",
                          "estado": "pendiente", "principal": True}],
            "semanas": [], "sesiones": [TIRADA], "nutricion": [], "incidencias": [], "notas": []}


class GarminDoble:
    """What `plan/reloj.py::mandar` uses of `GarminClient`, in memory (as in `test_plan_reloj.py`)."""

    def __init__(self, error: Exception | None = None) -> None:
        self.llamadas: list[tuple] = []
        self.error = error

    def subir_entreno(self, entreno: dict) -> int:
        self.llamadas.append(("subir_entreno", entreno["workoutName"]))
        if self.error is not None:
            raise self.error
        return 101

    def programar(self, workout_id: int, dia: date) -> int:
        self.llamadas.append(("programar", workout_id, dia))
        return 202

    def quitar_programado(self, schedule_id: int) -> None:
        self.llamadas.append(("quitar_programado", schedule_id))

    def borrar_entreno(self, workout_id: int) -> None:
        self.llamadas.append(("borrar_entreno", workout_id))


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
        self.token = tmp_path / "google-token.json"  # not written: Google Calendar is not connected unless asked
        self.equipo: Equipo = montar_equipo(self.db, store=self.store, medicos_dir=self.carpeta,
                                            transcriptor_medico=self.transcriptor, token=self.token,
                                            hoy=lambda: HOY, ahora=lambda: AHORA)
        self.garmin = GarminDoble()
        self.equipo.asistente.planes.garmin = lambda: self.garmin  # never the real Garmin Connect
        self.server = build_server(self.store, equipo=self.equipo)

    def llamar(self, nombre: str, argumentos: dict | None = None) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos or {}))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)

    def preparar(self, cambio: dict) -> tuple[str, str]:
        texto = self.llamar("preparar_cambio", {"cambio": cambio})
        encontrada = HUELLA.search(texto)
        assert encontrada, texto
        return texto, encontrada.group(1)

    def hacer(self, cambio: dict, papel: str = "entrenador", texto: str = "") -> str:
        _, huella = self.preparar(cambio)
        return self.llamar("hacer_cambio", {"papel": papel, "cambio": cambio, "huella": huella, "texto": texto})

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

    def mensajes_pc(self) -> list[dict]:
        convs = [c for c in self.conversaciones() if c["titulo"] == TITULO]
        return [m for c in convs for m in Conversaciones(self.db).mensajes(c["id"])]

    def tarjetas_aplicadas(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return [a for (a,) in conn.execute("SELECT accion FROM tarjeta_asistente WHERE estado = 'aplicada'")]
        finally:
            conn.close()


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


@pytest.fixture
def google(mundo):
    """Google Calendar connected (a token on disk) with its pass a double that counts the calls."""
    mundo.token.write_text("{}", encoding="utf-8")
    with patch("tridata.web.calendario.sincronizar",
               return_value={"creados": 1, "cambiados": 0, "borrados": 0}) as pasada:
        yield pasada


# -- 065 · R-186, C-230 ----------------------------------------------------------------------------------------------

def test_get_contexto_es_el_que_monta_la_app(mundo):
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


def test_get_contexto_y_las_de_antes_no_escriben_ni_transcriben(mundo):  # R-190
    antes = mundo.volcado()
    mundo.llamar("get_contexto")
    for nombre in ("list_activities", "get_daily_metrics", "get_training_load", "get_weekly_summary", "get_records"):
        mundo.llamar(nombre)
    mundo.llamar("get_activity", {"activity_id": "inv1"})
    assert mundo.volcado() == antes
    mundo.transcriptor.transcribir.assert_not_called()


# -- R1 · R-191, C-235 ----------------------------------------------------------------------------------------------

def test_preparar_dice_lo_que_se_haria_y_no_cambia_nada(mundo):  # R1
    antes = mundo.volcado()
    texto, huella = mundo.preparar(QUITAR_DOMINGO)
    assert "Tirada inventada 1h30" in texto  # the text of the card the app would show
    assert "no está hecho" in texto.lower() and "sí" in texto
    assert mundo.volcado() == antes and mundo.conversaciones() == []
    assert mundo.preparar(QUITAR_DOMINGO)[1] == huella  # the same change, the same fingerprint
    otro, _ = mundo.preparar(AL_RELOJ)
    assert "Garmin" in otro and mundo.volcado() == antes
    mundo.transcriptor.transcribir.assert_not_called()


# -- R2 · R-192, C-236 (criterio portante) ---------------------------------------------------------------------------

def test_hacer_aplica_como_la_app_sin_tarjeta_y_avisa_al_calendario(mundo, google):  # R2
    r1 = mundo.hacer(QUITAR_DOMINGO, texto="Te paso la tirada al sábado.")
    r2 = mundo.hacer(PONER_SABADO)
    assert r1.startswith("Hecho") and r2.startswith("Hecho")
    assert mundo.sesiones() == [(SABADO, "Tirada inventada 1h30")]  # moved from Sunday to Saturday
    assert mundo.tarjetas_aplicadas() == ["plan_quitar_sesion", "plan_anadir_sesion"]  # as confirmed in the app
    assert google.call_count == 2  # Google Calendar updated after each change, as the app wakes it (062)
    assert all(m["tarjetas"] == [] for m in mundo.mensajes_pc())  # no card left to confirm


def test_hacer_corrige_un_peso_ya_apuntado(mundo):  # R2: «corregir un peso», a change of the catalogue
    cambio = {"accion": "peso_dia", "dia": HOY.isoformat(), "valor": 71.2}
    respuesta = mundo.hacer(cambio, papel="nutricionista")
    assert respuesta.startswith("Hecho"), respuesta
    assert RegistroPeso(mundo.db).del_dia(HOY)["kg"] == 71.2


# -- R3 · R-193, C-237 ----------------------------------------------------------------------------------------------

def test_mandar_a_garmin_la_sube_y_la_programa(mundo):  # R3
    respuesta = mundo.hacer(AL_RELOJ)
    assert respuesta.startswith("Hecho") and "Garmin" in respuesta
    assert mundo.garmin.llamadas == [("subir_entreno", "Tirada inventada 1h30"),
                                     ("programar", 101, date(2026, 11, 1))]
    (fila,) = mundo.planes.sesiones_reloj(mundo.plan_id)
    sesion = mundo.planes.sesion(fila["id"])
    assert fila["estado"] == "al_dia" and sesion is not None
    assert sesion["garmin_workout_id"] == 101 and sesion["garmin_schedule_id"] == 202


# -- R4 · R-194, C-238 ----------------------------------------------------------------------------------------------

def test_apuntar_comida_la_deja_en_el_dia_como_el_dictado(mundo):  # R4
    respuesta = mundo.llamar("apuntar_comida", {"papel": "nutricionista", "dia": HOY.isoformat(),
                                                "alimentos": [PAELLA], "texto": "Paella de la foto."})
    assert respuesta.startswith("Hecho") and "paella inventada" in respuesta
    paella = [a for a in RegistroComidas(mundo.db).apuntes_del_dia(HOY) if a["nombre"] == "paella inventada"]
    assert len(paella) == 1
    assert (paella[0]["cantidad"], paella[0]["unidad"], paella[0]["kcal"]) == (350, "g", 650)
    assert paella[0]["seccion"] is None and paella[0]["estado"] == "comido"


@pytest.mark.parametrize("alimentos, motivo", [
    ([PAELLA, {**PAELLA, "nombre": "postre inventado", "kcal": -120}], "negativo"),
    ([{**PAELLA, "kcal": None}], "kcal"),
    ([], "nada"),
])
def test_una_comida_que_no_valida_no_apunta_nada(mundo, alimentos, motivo):  # R4
    antes = mundo.volcado()
    respuesta = mundo.llamar("apuntar_comida", {"papel": "nutricionista", "dia": HOY.isoformat(),
                                                "alimentos": alimentos})
    assert motivo in respuesta and "No se ha apuntado nada" in respuesta
    assert mundo.volcado() == antes


# -- R5 · R-192, C-243 ----------------------------------------------------------------------------------------------

def test_apuntar_peso_como_la_pieza_peso(mundo):  # R5
    respuesta = mundo.llamar("apuntar_peso", {"papel": "nutricionista", "kg": 74, "dia": HOY.isoformat()})
    assert respuesta.startswith("Hecho") and "74" in respuesta
    assert RegistroPeso(mundo.db).del_dia(HOY)["kg"] == 74


@pytest.mark.parametrize("kg, dia, motivo", [(740, HOY.isoformat(), FUERA_DE_RANGO),
                                             (74, "2026-10-27", "aún no ha llegado")])
def test_un_peso_que_no_valida_no_se_apunta(mundo, kg, dia, motivo):  # R5
    antes = mundo.volcado()
    respuesta = mundo.llamar("apuntar_peso", {"papel": "nutricionista", "kg": kg, "dia": dia})
    assert motivo in respuesta and "No se ha apuntado" in respuesta
    assert mundo.volcado() == antes


# -- R6 · R-196, C-241 ----------------------------------------------------------------------------------------------

def test_cada_cambio_hecho_deja_un_mensaje_en_desde_el_pc(mundo):  # R6
    mundo.preparar(QUITAR_DOMINGO)  # prepared and not made: nothing
    assert mundo.conversaciones() == []
    mundo.hacer(QUITAR_DOMINGO, papel="entrenador", texto="Quito el domingo.")
    mundo.llamar("apuntar_peso", {"papel": "nutricionista", "kg": 74, "dia": HOY.isoformat()})
    (conv,) = mundo.conversaciones()
    assert conv["titulo"] == TITULO == "Desde el PC"
    uno, dos = mundo.mensajes_pc()
    assert (uno["quien"], uno["papel"], dos["quien"], dos["papel"]) == ("asistente", "entrenador",
                                                                        "asistente", "nutricionista")
    assert "Quito el domingo." in uno["texto"] and "Hecho" in uno["texto"] and "Tirada inventada" in uno["texto"]
    assert "74" in dos["texto"] and "Hecho" in dos["texto"]
    assert uno["hora"].startswith("2026-10-26T09:00") and dos["hora"].startswith("2026-10-26T09:00")
    assert uno["tarjetas"] == [] and dos["tarjetas"] == []


# -- R7 · R-197, R-189, C-242, C-233 --------------------------------------------------------------------------------

def _sin_hecho(respuesta: str) -> None:
    assert "hecho" not in respuesta.lower().replace("no se ha hecho", "").replace("no está hecho", ""), respuesta


def test_garmin_que_no_llega_no_da_nada_por_hecho(mundo):  # R7
    from tridata.garmin_client import GarminWorkoutError

    mundo.garmin.error = GarminWorkoutError("Garmin Connect did not answer")
    antes = mundo.volcado()
    respuesta = mundo.hacer(AL_RELOJ)
    _sin_hecho(respuesta)
    assert "No ha llegado a tu Garmin" in respuesta
    assert mundo.volcado() == antes and mundo.conversaciones() == []


@pytest.mark.parametrize("cambio, campos", [({"dia": DOMINGO}, ["accion"]),
                                            ({"accion": "plan_quitar_sesion", "dia": "el domingo"}, ["dia"])])
def test_un_cambio_mal_formado_dice_que_corregir_y_no_escribe(mundo, cambio, campos):  # R7
    antes = mundo.volcado()
    assert mundo.llamar("preparar_cambio", {"cambio": cambio}) == cambio_no_entendido(campos)
    respuesta = mundo.llamar("hacer_cambio", {"papel": "entrenador", "cambio": cambio, "huella": "0" * 12})
    assert respuesta == cambio_no_entendido(campos)
    assert mundo.volcado() == antes and mundo.conversaciones() == []


def test_si_la_base_cambio_desde_que_se_preparo_no_se_hace(mundo):  # R7
    _, huella = mundo.preparar(QUITAR_DOMINGO)
    conn = sqlite3.connect(mundo.db)
    with conn:
        conn.execute("UPDATE plan_sesion SET titulo = 'Tirada inventada 2h' WHERE fecha = ?", (DOMINGO,))
    conn.close()
    antes = mundo.volcado()
    respuesta = mundo.llamar("hacer_cambio", {"papel": "entrenador", "cambio": QUITAR_DOMINGO, "huella": huella})
    _sin_hecho(respuesta)
    assert "cambiado" in respuesta.lower() and "Tirada inventada 2h" in respuesta  # what it would do now
    assert mundo.volcado() == antes and mundo.conversaciones() == []


def test_un_cambio_que_no_sale_no_se_hace(mundo):  # R7: a «no» of the catalogue
    antes = mundo.volcado()
    cambio = {"accion": "plan_quitar_sesion", "dia": "2026-11-05"}  # no session that day
    respuesta = mundo.llamar("hacer_cambio", {"papel": "entrenador", "cambio": cambio, "huella": "0" * 12})
    _sin_hecho(respuesta)
    assert mundo.volcado() == antes and mundo.conversaciones() == []


@pytest.mark.parametrize("papel", ["médico", "asistente", ""])
def test_un_papel_desconocido_se_rechaza_con_los_validos(mundo, papel):  # R7
    antes = mundo.volcado()
    _, huella = mundo.preparar(QUITAR_DOMINGO)
    for nombre, argumentos in (("hacer_cambio", {"cambio": QUITAR_DOMINGO, "huella": huella}),
                               ("apuntar_comida", {"dia": HOY.isoformat(), "alimentos": [PAELLA]}),
                               ("apuntar_peso", {"kg": 74, "dia": HOY.isoformat()})):
        respuesta = mundo.llamar(nombre, {"papel": papel, **argumentos})
        assert all(p in respuesta for p in PAPELES_PC) and set(PAPELES_PC) == {"entrenador", "nutricionista", "fisio"}
        assert "No se ha hecho nada" in respuesta
    assert mundo.volcado() == antes


def test_google_que_no_responde_se_dice(mundo, google):  # R7: the plan is changed, the calendar is not
    google.side_effect = ErrorCalendario("Google no responde")
    respuesta = mundo.hacer(QUITAR_DOMINGO)
    assert "Google Calendar no se ha actualizado" in respuesta
    assert "Google no responde" in respuesta and "reintentará" in respuesta
    assert mundo.sesiones() == []  # what the app does too: the plan changed, Google retried on the next pass


# -- R8 · R-190, G-55, C-244 ----------------------------------------------------------------------------------------

def test_solo_se_escribe_con_las_de_r2_a_r5(mundo):  # R8
    tools = {t.name for t in asyncio.run(mundo.server.list_tools())}
    assert "proponer_cambio" not in tools
    assert {"preparar_cambio", "hacer_cambio", "apuntar_comida", "apuntar_peso"} <= tools
