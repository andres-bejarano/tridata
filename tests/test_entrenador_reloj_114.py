"""Unit 114 (P-20261030-2ebd4bdd, P-20261028-0f41c004, P-20261029-22ca59d3, P-20261026-073af351): the coach and the
watch, through the local MCP server as the coach of the PC uses it.

- R1 (R-206, load-bearing): removing with the coach a session sent to the watch removes it from Garmin too; if Garmin
  fails it is removed from the plan anyway and the «Hecho» says it is still in the watch; never sent, no Garmin.
- R2 (R-157..R-161): after adding a running session the watch can follow, the «Hecho» offers to send it.
- R3 (R-151..R-153): a 429 of Garmin with the routes says to wait, without asking for the GPX.
- R4, R5: the context of the plan carries the earlier editions of a race not yet run and the comparativa.
- R6: the instructions of the MCP and `get_contexto` ask to open the laps and the earlier editions.
- R7: `GarminClient.bajar_fit` is gone.

Every datum is invented; Garmin is a double; nothing talks to a paid service.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime

import pytest
from test_plan import plan_de_prueba
from test_plan_reloj import GarminDoble

from tridata.conversaciones.contexto import plan_en_curso
from tridata.conversaciones.desde_pc import montar_equipo
from tridata.garmin_client import GarminClient, GarminRateLimitError
from tridata.mcp_server import INSTRUCTIONS, build_server
from tridata.models import Activity
from tridata.plan import Planes
from tridata.storage import DataStore

HOY = date(2026, 10, 22)
AHORA = datetime(2026, 10, 22, 9, 0)
MIERCOLES = date(2026, 10, 28)
HUELLA = re.compile(r"Huella: ([0-9a-f]{12})")
SERIES = {"apartado": 1, "fecha": "2026-10-28", "titulo": "Series de prueba 3×2000",
          "bloques": ["20' a 5:30", "3×2000 a 4:15 (3' rec)", "15' suave"], "clave": True}
BEHOBIA = {"apartado": 0, "nombre": "Behobia", "fecha": "2026-11-08", "objetivo": "1:25", "estado": "pendiente",
           "principal": False}
NOTAS = [{"apartado": 0, "tipo": "comparativa", "texto": "Comparativa inventada: 2025 llegaba con 10 km menos"},
         {"apartado": 0, "tipo": "nota", "texto": "Nota inventada del plan"}]
OFERTA = "¿Te la mando al reloj?"
FRENA = "Garmin está frenando las peticiones: espera unos minutos y vuelve a pedírmelo"


def _actividad(ident: str, dia: date, nombre: str, metros: float, segundos: float, tipo: str = "running") -> Activity:
    return Activity(activity_id=ident, activity_date=dia, activity_type=tipo, name=nombre,
                    duration_seconds=segundos, distance_meters=metros)


class Mundo:
    """A plan in course with the series of Wednesday and the Behobia, last year's Behobia and others in Garmin's
    history, the team of the PC and Garmin as a double that counts how many times it is connected to."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.store = DataStore(self.db)
        self.store.save_activities([
            _actividad("behobia-25", date(2025, 11, 9), "Behobia-San Sebastián", 20100, 5430),
            _actividad("rodaje-25", date(2025, 11, 9), "Rodaje suave", 8000, 2880),
            _actividad("behobia-marzo", date(2025, 3, 15), "Behobia", 20000, 5600),
            _actividad("10k-25", date(2025, 10, 12), "10K de ejemplo", 10000, 2350),
        ])
        self.planes = Planes(self.db)
        carreras = [*plan_de_prueba()["carreras"], BEHOBIA]
        self.plan_id = self.planes.crear(plan_de_prueba(sesiones=[*plan_de_prueba()["sesiones"], SERIES],
                                                        carreras=carreras, notas=NOTAS), AHORA)
        self.equipo = montar_equipo(self.db, store=self.store, token=tmp_path / "google-token.json",
                                    hoy=lambda: HOY, ahora=lambda: AHORA)
        self.garmin = GarminDoble()
        self.conexiones = 0

        def conectar():
            self.conexiones += 1
            return self.garmin

        self.equipo.asistente.planes.garmin = conectar  # never the real Garmin Connect
        self.server = build_server(self.store, equipo=self.equipo)

    def llamar(self, nombre: str, argumentos: dict | None = None) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos or {}))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)

    def hacer(self, cambio: dict) -> str:
        """What the coach does: prepare, show it to Andrés, and with his «sí» do it with that fingerprint."""
        texto = self.llamar("preparar_cambio", {"cambio": cambio})
        encontrada = HUELLA.search(texto)
        assert encontrada, texto
        return self.llamar("hacer_cambio", {"papel": "entrenador", "cambio": cambio, "huella": encontrada.group(1)})

    def del_miercoles(self) -> list[str]:
        return [s["titulo"] for s in self.planes.leer(self.plan_id)["sesiones"] if s["fecha"] == "2026-10-28"]


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


# -- R1 · R-206: removing from the coach removes from the watch -------------------------------------------------------

def test_quitar_desde_el_entrenador_la_quita_tambien_del_reloj(mundo):  # R1 — the criterio portante
    assert mundo.hacer({"accion": "mandar_a_garmin", "dia": "2026-10-28"}).startswith("Hecho")
    assert [e["workoutName"] for e in mundo.garmin.del_dia(MIERCOLES)] == ["Series de prueba 3×2000"]
    hecho = mundo.hacer({"accion": "plan_quitar_sesion", "dia": "2026-10-28"})
    assert hecho.startswith("Hecho"), hecho
    assert mundo.del_miercoles() == []  # out of the plan
    assert mundo.garmin.del_dia(MIERCOLES) == [] and mundo.garmin.entrenos == {}  # and out of Garmin's calendar:
    assert mundo.garmin.programados == {}  # the next sync has nothing to bring back
    assert "sigue en tu reloj" not in hecho


def test_si_garmin_falla_se_quita_igual_y_dice_que_sigue_en_el_reloj(mundo):  # R1
    mundo.hacer({"accion": "mandar_a_garmin", "dia": "2026-10-28"})
    mundo.garmin.falla_en = "quitar_programado"
    hecho = mundo.hacer({"accion": "plan_quitar_sesion", "dia": "2026-10-28"})
    assert hecho.startswith("Hecho"), hecho
    assert mundo.del_miercoles() == []
    assert "«Series de prueba 3×2000» sigue en tu reloj: bórrala allí" in hecho


def test_una_sesion_nunca_mandada_no_llama_a_garmin(mundo):  # R1
    hecho = mundo.hacer({"accion": "plan_quitar_sesion", "dia": "2026-10-28"})
    assert hecho.startswith("Hecho") and mundo.del_miercoles() == []
    assert mundo.conexiones == 0 and mundo.garmin.llamadas == []


# -- R2 · R-157..R-161: after adding, the offer of the watch ----------------------------------------------------------

@pytest.mark.parametrize(("texto", "ofrece"), [
    # «10' suave + 4×(1 km a 4:10 + 2' suave)» as the coach writes it for the watch (`_SESION`: «(2' rec)»)
    ("Series 4×1 km | 10' suave | 4×1 km a 4:10 (2' rec)", True),
    ("Bici 1h30 | 1h30 bici Z2", False),
    ("Rodaje con amigos | lo que salga, charlando", False),
], ids=["carrera", "bici", "no-se-entiende"])
def test_al_anadir_ofrece_mandarla_al_reloj_solo_si_sabe_guiarla(mundo, texto, ofrece):  # R2
    hecho = mundo.hacer({"accion": "plan_anadir_sesion", "dia": "2026-10-29", "texto": texto})
    assert hecho.startswith("Hecho"), hecho
    assert hecho.rstrip().endswith(OFERTA) is ofrece, hecho
    assert (OFERTA in hecho) is ofrece
    assert mundo.conexiones == 0  # offering never talks to Garmin


def test_la_descripcion_de_anadir_dice_que_lo_ofrezca_con_mandar_a_garmin():  # R2
    from tridata.nutricion.asistente import texto_acciones

    linea = next(x for x in texto_acciones().splitlines() if x.startswith("- plan_anadir_sesion:"))
    assert "mandar_a_garmin" in linea and "reloj" in linea


# -- R3 · R-153: a 429 is «wait», not «give me the GPX» ---------------------------------------------------------------

class GarminQueFalla:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def rutas_guardadas(self) -> list[dict]:
        raise self.error

    def ruta(self, course_id: int) -> list[dict]:
        raise self.error

    def gpx_actividad(self, activity_id: str) -> bytes:
        raise self.error


def test_un_429_al_pedir_rutas_dice_que_espere_sin_pedir_el_gpx(mundo):  # R3
    mundo.equipo.asistente.planes.garmin = lambda: GarminQueFalla(GarminRateLimitError("429"))
    for herramienta, argumentos in (("get_rutas", {}), ("get_perfil_ruta", {"garmin": 77}),
                                    ("get_perfil_ruta", {"actividad": "behobia-25"})):
        respuesta = mundo.llamar(herramienta, argumentos)
        assert FRENA in respuesta, (herramienta, respuesta)
        assert "GPX" not in respuesta and "Garmin no responde" not in respuesta, (herramienta, respuesta)


def test_un_429_al_entrar_tambien_dice_que_espere(mundo):  # R3: the login of Garmin is limited too
    def entrar():
        raise GarminRateLimitError("Garmin Connect is limiting the requests (429)")

    mundo.equipo.asistente.planes.garmin = entrar
    assert FRENA in mundo.llamar("get_perfil_ruta", {"garmin": 77})


def test_otro_fallo_sigue_pidiendo_el_gpx(mundo):  # R3
    mundo.equipo.asistente.planes.garmin = lambda: GarminQueFalla(RuntimeError("Garmin Connect did not answer"))
    respuesta = mundo.llamar("get_perfil_ruta", {"garmin": 77})
    assert "Garmin no responde" in respuesta and "GPX" in respuesta and FRENA not in respuesta


# -- R4, R5: the earlier editions of a race and the comparativa in the context --------------------------------------

def _carrera(datos: dict, nombre: str) -> dict:
    return next(c for c in datos["carreras"] if c["nombre"] == nombre)


def test_el_contexto_da_las_ediciones_anteriores_de_una_carrera_por_correr(mundo):  # R4
    datos, _ = plan_en_curso(mundo.planes, mundo.store, HOY)
    behobia = _carrera(datos, "Behobia")
    assert behobia["anteriores"] == [{"id": "behobia-25", "fecha": "2025-11-09", "nombre": "Behobia-San Sebastián",
                                      "km": 20.1, "tiempo": "1:30:30", "ritmo_km": "4:30"}]
    # the 10K already run carries none, though there is one of last year
    assert "anteriores" not in _carrera(datos, "10K de ejemplo")
    assert "anteriores" not in _carrera(datos, "Maratón de ejemplo")  # none in its days of other years


def test_el_coach_ve_las_ediciones_anteriores_y_la_comparativa(mundo):  # R4, R5
    texto = mundo.llamar("get_contexto")
    assert "behobia-25" in texto and "rodaje-25" not in texto and "behobia-marzo" not in texto
    assert "Comparativa inventada: 2025 llegaba con 10 km menos" in texto


def test_la_comparativa_va_aparte_de_las_notas(mundo):  # R5
    datos, _ = plan_en_curso(mundo.planes, mundo.store, HOY)
    assert datos["comparativa"] == ["Comparativa inventada: 2025 llegaba con 10 km menos"]
    assert datos["notas"] == ["Nota inventada del plan"]


def test_sin_store_no_hay_ediciones_anteriores(mundo):  # R4: nothing invented without Garmin's history
    datos, _ = plan_en_curso(mundo.planes, None, HOY)
    assert all("anteriores" not in c for c in datos["carreras"])


# -- R6: open the laps before judging ---------------------------------------------------------------------------------

def test_las_instrucciones_piden_abrir_las_vueltas_y_las_ediciones_anteriores(mundo):  # R6
    tools = {t.name: t for t in asyncio.run(mundo.server.list_tools())}
    for texto in (INSTRUCTIONS, tools["get_contexto"].description):
        plano = " ".join(texto.split())
        assert "get_activity" in plano and "laps" in plano, texto
        assert "anteriores" in plano and "comparativa" in plano, texto


# -- R7: no `bajar_fit` -----------------------------------------------------------------------------------------------

def test_bajar_fit_ya_no_existe():  # R7 (the .fit of Fitness › Plan is `fit_local`: test_plan_acciones)
    assert not hasattr(GarminClient, "bajar_fit")


def test_el_contexto_sigue_siendo_json(mundo):  # nothing else of the context changed shape
    datos, _ = plan_en_curso(mundo.planes, mundo.store, HOY)
    json.dumps(datos, ensure_ascii=False)
    assert [s["titulo"] for s in datos["sesiones"] if s["fecha"] == "2026-10-28"] == ["Series de prueba 3×2000"]
