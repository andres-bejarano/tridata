"""Unit 048 (P-20261027-ec01fb75): each session of a day, one by one, and the pending things of the plan.

- R3 (the criterio portante): with two sessions of the same title the same day, changing the second changes only
  the second and removing the first removes only the first: the card carries the id of the session
  (`Planes.sesiones_reloj`), not its title. The context of the team gives the id of each session and the Asistente
  can name it by it («#12» or «12» in `objeto`).
- R4: a session removed between proposing and confirming says «Esa sesión ya no está en el plan» and touches no
  other one with the same title; a card from before this unit (by title) is still confirmed if that title is unique
  that day.
- R9: confirming a card by id whose session no longer has the day and title the card showed (the rowid of SQLite is
  reused after deleting the last row) applies nothing: the card is redone with what is there now.
- R6: «Bajar .md» writes under each incident of the app its state, its visits and its lesson; a plan without them
  gives the same .md as before.
- R8: README and `.env.example` say what `TRIDATA_MODELO_ASISTENTE` does today and name `TRIDATA_GOOGLE_CLIENTE`.

Integration over a temporary SQLite with an invented plan; nothing calls Garmin, Google nor Anthropic.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from test_plan import plan_de_prueba
from tridata.conversaciones.contexto import plan_en_curso
from tridata.ia import CambioInterpretado
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.nutricion.asistente import Cambiado, Contexto, Peticion, confirmar, proponer, texto_acciones
from tridata.nutricion.recetas import Recetario
from tridata.plan import Planes
from tridata.plan.markdown import escribir
from tridata.storage import DataStore

HOY = date(2026, 10, 22)  # Thursday of S9
AHORA = datetime(2026, 10, 22, 9, 0)
MIERCOLES = date(2026, 10, 28)  # S10
RODAJE = "Rodaje 40'"
PRIMERO = {"apartado": 1, "fecha": "2026-10-28", "titulo": RODAJE, "bloques": ["40' a 5:40"], "clave": False}
SEGUNDO = {"apartado": 1, "fecha": "2026-10-28", "titulo": RODAJE, "bloques": ["10' a 5:30", "30' a 5:10"],
           "clave": False}
TIRADA = {"apartado": 1, "fecha": "2026-10-30", "titulo": "Tirada 1h", "bloques": ["1h a 5:35"], "clave": False}
NUEVA = "Series 3×1000 | 20' a 5:30 | 3×1000 a 4:05 (2' rec) | 15' suave"
RAIZ = Path(__file__).parents[1]


class Base:
    """The plan of `test_plan` with, on the Wednesday of S10, two sessions with the same title (invented)."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1800, "kcal_por_paso": 0.04, "pasos_por_defecto": 9000, "objetivo_pct": -10},
                        desde=DESDE_SIEMPRE)
        self.planes = Planes(self.db)
        self.plan_id = self.planes.crear(
            plan_de_prueba(sesiones=[*plan_de_prueba()["sesiones"], TIRADA, PRIMERO, SEGUNDO]), AHORA)
        self.ctx = Contexto(registro=RegistroComidas(self.db), objetivos=ObjetivosDia(self.db), ajustes=ajustes,
                            pesos=RegistroPeso(self.db), recetario=Recetario(self.db), hoy=lambda: HOY,
                            planes=self.planes)

    def plan(self) -> dict:
        return self.planes.leer(self.plan_id)

    def uno(self, **campos) -> dict:
        (resultado,) = proponer([CambioInterpretado(**campos)], self.ctx, Peticion(texto="texto de prueba", fecha=HOY))
        return resultado

    def ids(self, dia: str = "2026-10-28") -> list[int]:
        return [int(s["id"]) for s in self.planes.sesiones_reloj(self.plan_id) if s["fecha"] == dia]


@pytest.fixture
def b(tmp_path) -> Base:
    return Base(tmp_path)


def _dia(plan: dict, dia: str = "2026-10-28") -> list[tuple[str, list[str]]]:
    return [(s["titulo"], s["bloques"]) for s in plan["sesiones"] if s["fecha"] == dia]


# -- R3: the card carries the id of the session ------------------------------------------------------------------

def test_cambiar_la_segunda_cambia_solo_la_segunda(b):  # R3 — the criterio portante
    primero, segundo = b.ids()
    t = b.uno(accion="plan_cambiar_sesion", dia=MIERCOLES, objeto=f"#{segundo}", texto=NUEVA)
    assert t["tipo"] == "propuesta", t
    assert t["parametros"]["sesion"] == segundo
    assert t["antes"] is not None and "30' a 5:10" in t["antes"]  # the second one, with its blocks
    confirmar(t, b.ctx)
    assert _dia(b.plan()) == [(RODAJE, ["40' a 5:40"]),
                              ("Series 3×1000", ["20' a 5:30", "3×1000 a 4:05 (2' rec)", "15' suave"])]
    assert b.ids() == [primero, segundo]  # the same rows: changed in place


def test_quitar_la_primera_quita_solo_la_primera(b):  # R3 — the criterio portante
    primero, segundo = b.ids()
    t = b.uno(accion="plan_quitar_sesion", dia=MIERCOLES, objeto=str(primero))  # «12» works as «#12»
    assert t["tipo"] == "propuesta", t
    assert t["parametros"]["sesion"] == primero
    assert t["texto"] == f"Voy a quitar del plan: mié 28/10 · {RODAJE}"
    confirmar(t, b.ctx)
    assert _dia(b.plan()) == [(RODAJE, ["10' a 5:30", "30' a 5:10"])]
    assert b.ids() == [segundo]


def test_dos_con_el_mismo_titulo_pide_cual_por_su_id(b):  # R3
    primero, segundo = b.ids()
    t = b.uno(accion="plan_quitar_sesion", dia=MIERCOLES, objeto="rodaje")
    assert t["tipo"] == "no", t
    assert f"#{primero}" in t["texto"] and f"#{segundo}" in t["texto"]
    assert len(_dia(b.plan())) == 2


def test_un_id_de_otro_dia_no_vale(b):  # R3
    (tirada,) = b.ids("2026-10-30")
    t = b.uno(accion="plan_quitar_sesion", dia=MIERCOLES, objeto=f"#{tirada}")
    assert t["tipo"] == "no", t
    assert len(_dia(b.plan(), "2026-10-30")) == 1


def test_una_sesion_sola_se_sigue_eligiendo_como_antes(b):  # R3: nothing changes for one session a day
    (tirada,) = b.ids("2026-10-30")
    t = b.uno(accion="plan_cambiar_sesion", dia=date(2026, 10, 30), texto="Tirada 1h10 | 1h10 a 5:35")
    assert t["tipo"] == "propuesta" and t["parametros"]["sesion"] == tirada
    confirmar(t, b.ctx)
    assert _dia(b.plan(), "2026-10-30") == [("Tirada 1h10", ["1h10 a 5:35"])]


def test_mandar_a_garmin_empareja_por_posicion(b):  # R3: the second of two equal ones goes by its id, not its title
    _, segundo = b.ids()
    t = b.uno(accion="mandar_a_garmin", dia=MIERCOLES, objeto=f"#{segundo}")
    assert t["tipo"] == "propuesta", t
    assert t["parametros"] == {"sesion": segundo}


def test_el_contexto_lleva_el_id_de_cada_sesion(b):  # R3 · R-149
    datos, _ = plan_en_curso(b.planes, None, HOY)
    assert datos is not None
    reloj = {int(s["id"]): s for s in b.planes.sesiones_reloj(b.plan_id)}
    assert all("id" in s for s in datos["sesiones"]), datos["sesiones"]
    for s in datos["sesiones"]:
        assert (reloj[s["id"]]["fecha"], reloj[s["id"]]["titulo"]) == (s["fecha"], s["titulo"])
    assert [s["id"] for s in datos["sesiones"] if s["fecha"] == "2026-10-28"] == b.ids()


def test_las_acciones_dicen_que_se_nombra_por_su_id():  # R3
    lineas = {x.split(":")[0]: x for x in texto_acciones().splitlines() if x.startswith("- ")}
    for accion in ("plan_cambiar_sesion", "plan_quitar_sesion", "mandar_a_garmin"):
        assert "id" in lineas[f"- {accion}"].split(":", 1)[1], accion


# -- R4: removed in between, and the cards of before ---------------------------------------------------------------

def test_quitada_entre_medias_no_toca_la_otra(b):  # R4
    primero, segundo = b.ids()
    t = b.uno(accion="plan_cambiar_sesion", dia=MIERCOLES, objeto=f"#{primero}", texto=NUEVA)
    confirmar(b.uno(accion="plan_quitar_sesion", dia=MIERCOLES, objeto=f"#{primero}"), b.ctx)
    with pytest.raises(Cambiado) as exc:
        confirmar(t, b.ctx)
    assert exc.value.tarjeta["tipo"] == "no"
    assert exc.value.tarjeta["texto"] == "Esa sesión ya no está en el plan"
    assert _dia(b.plan()) == [(RODAJE, ["10' a 5:30", "30' a 5:10"])]  # the other one, untouched
    assert b.ids() == [segundo]


def _de_antes(t: dict) -> dict:
    """The same card as it was made before this unit: by title, without the id of the session."""
    return {**t, "parametros": {k: v for k, v in t["parametros"].items() if k != "sesion"}}


def test_una_tarjeta_de_antes_por_titulo_unico_se_confirma(b):  # R4
    t = _de_antes(b.uno(accion="plan_quitar_sesion", dia=date(2026, 10, 30)))
    assert "sesion" not in t["parametros"] and t["parametros"]["titulo"] == "Tirada 1h"
    confirmar(t, b.ctx)
    assert _dia(b.plan(), "2026-10-30") == []
    assert len(_dia(b.plan())) == 2  # nothing else


def test_una_tarjeta_de_antes_con_dos_iguales_no_toca_ninguna(b):  # R4
    primero, _ = b.ids()
    t = _de_antes(b.uno(accion="plan_quitar_sesion", dia=MIERCOLES, objeto=f"#{primero}"))
    with pytest.raises(Cambiado):
        confirmar(t, b.ctx)
    assert len(_dia(b.plan())) == 2


# -- R9: the id reused, or the session changed under it -------------------------------------------------------------

def test_id_reutilizado_no_aplica_y_rehace_la_tarjeta(b):  # R9
    primero, segundo = b.ids()
    t = b.uno(accion="plan_quitar_sesion", dia=MIERCOLES, objeto=f"#{segundo}")
    with sqlite3.connect(b.db) as conn:  # the last row goes and another one takes its rowid
        conn.execute("DELETE FROM plan_sesion WHERE rowid = ?", (segundo,))
        cursor = conn.execute(
            "INSERT INTO plan_sesion (plan_id, orden, apartado, fecha, titulo, bloques, clave) VALUES (?, 99, 1, "
            "'2026-10-31', 'Fartlek de prueba', ?, 0)", (b.plan_id, json.dumps(["30' con cambios"])))
        assert cursor.lastrowid == segundo  # the rowid of SQLite, reused: what this test is about
    with pytest.raises(Cambiado) as exc:
        confirmar(t, b.ctx)
    rehecha = exc.value.tarjeta
    assert rehecha["tipo"] == "propuesta"
    assert rehecha["texto"] == "Voy a quitar del plan: sáb 31/10 · Fartlek de prueba"
    assert _dia(b.plan(), "2026-10-31") == [("Fartlek de prueba", ["30' con cambios"])]  # nothing applied
    assert _dia(b.plan()) == [(RODAJE, ["40' a 5:40"])]
    confirmar(rehecha, b.ctx)  # confirmed again, now it is what it shows
    assert _dia(b.plan(), "2026-10-31") == []
    assert b.ids() == [primero]


def test_cambiada_de_titulo_por_debajo_no_aplica(b):  # R9
    primero, _ = b.ids()
    t = b.uno(accion="plan_quitar_sesion", dia=MIERCOLES, objeto=f"#{primero}")
    with sqlite3.connect(b.db) as conn:
        conn.execute("UPDATE plan_sesion SET titulo = 'Rodaje 45' WHERE rowid = ?", (primero,))
    with pytest.raises(Cambiado) as exc:
        confirmar(t, b.ctx)
    assert exc.value.tarjeta["texto"] == "Voy a quitar del plan: mié 28/10 · Rodaje 45"
    assert len(_dia(b.plan())) == 2  # nothing applied


def test_id_que_ya_no_esta_y_nada_mas(b):  # R9: no session left with that id
    (tirada,) = b.ids("2026-10-30")
    t = b.uno(accion="plan_quitar_sesion", dia=date(2026, 10, 30))
    with sqlite3.connect(b.db) as conn:
        conn.execute("DELETE FROM plan_sesion WHERE rowid = ?", (tirada,))
    with pytest.raises(Cambiado) as exc:
        confirmar(t, b.ctx)
    assert exc.value.tarjeta == {"tipo": "no", "texto": "Esa sesión ya no está en el plan"}
    assert len(_dia(b.plan())) == 2


# -- R6: «Bajar .md» with the state, the visits and the lesson ------------------------------------------------------

DEL_MD = {"apartado": 1, "fecha": "2026-09-23", "texto": "Rampas inventadas en los gemelos (piscina)"}


def test_el_md_lleva_estado_visitas_y_leccion(tmp_path):  # R6 · R-133
    db = tmp_path / "data.sqlite3"
    planes = Planes(db)
    ident = planes.crear(plan_de_prueba(incidencias=[DEL_MD]), AHORA)
    tobillo = planes.abrir_incidencia(ident, "tobillo", "derecho, lateral", date(2026, 10, 20),
                                      "molestia inventada al acabar el rodaje", AHORA)
    planes.anadir_visita(tobillo, date(2026, 10, 21), "fisio", "sin lesión, alta para correr", AHORA)
    gemelo = planes.abrir_incidencia(ident, "gemelo", None, date(2026, 10, 1), "carga inventada", AHORA)
    planes.cerrar_incidencia(gemelo, "estirar después de las series", date(2026, 10, 10))
    texto = escribir(planes.leer(ident), DataStore(db), HOY)
    assert ("- 2026-09-23: Rampas inventadas en los gemelos (piscina)\n"
            "- 2026-10-20: tobillo derecho, lateral: molestia inventada al acabar el rodaje\n"
            "  - Estado: abierta\n"
            "  - Visita 2026-10-21 · fisio: sin lesión, alta para correr\n"
            "- 2026-10-01: gemelo: carga inventada\n"
            "  - Estado: cerrada el 2026-10-10\n"
            "  - Lección: estirar después de las series\n") in texto


def test_sin_incidencias_de_la_app_el_md_es_el_de_siempre(tmp_path):  # R6
    db = tmp_path / "data.sqlite3"
    planes = Planes(db)
    ident = planes.crear(plan_de_prueba(incidencias=[DEL_MD]), AHORA)
    texto = escribir(planes.leer(ident), DataStore(db), HOY)
    assert texto == escribir(planes.leer(ident), None, HOY)  # the incidents of the .md say nothing more
    assert "- 2026-09-23: Rampas inventadas en los gemelos (piscina)\n" in texto
    assert "  - Estado:" not in texto and "  - Lección:" not in texto and "  - Visita" not in texto


def test_bajar_md_desde_la_web_lleva_la_incidencia_viva(tmp_path):  # R6, through GET /api/plan/N.md
    from test_conversaciones_web import Web

    web = Web(tmp_path)
    planes = Planes(web.db)
    ident = planes.crear(plan_de_prueba(incidencias=[DEL_MD]), AHORA)
    tobillo = planes.abrir_incidencia(ident, "tobillo", None, date(2026, 10, 20), "molestia inventada", AHORA)
    planes.anadir_visita(tobillo, date(2026, 10, 21), "fisio", "alta para correr", AHORA)
    r = web.cliente.get(f"/api/plan/{ident}.md")
    assert r.status_code == 200, r.text
    assert ("- 2026-10-20: tobillo: molestia inventada\n  - Estado: abierta\n"
            "  - Visita 2026-10-21 · fisio: alta para correr\n") in r.text


# -- R8: README and .env.example --------------------------------------------------------------------------------------

def test_readme_y_env_example_dicen_lo_que_hay():  # R8
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
    for texto in (readme, ejemplo):
        assert "TRIDATA_MODELO_ASISTENTE" in texto
        assert "Ajustes › El equipo" in texto
        assert "TRIDATA_GOOGLE_CLIENTE" in texto
        assert "google-token.json" in texto
    assert "TRIDATA_GOOGLE_CLIENTE=/path/to/your/private/google/client.json" in ejemplo
    assert "/home/" not in ejemplo and "/home/" not in readme
