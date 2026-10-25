"""Unit 041 · R5 (R-140, C-182, G-55): the cards that change the plan (`plan/acciones.py`), a family of the catalogue
of the 021: change, add or remove a planned session with its blocks, change the target of a race of the plan, add a
note. Nothing changes until Andrés confirms; a card whose session was changed by hand in between is redone (G-41).

Unit level over the catalogue with a seeded temporary SQLite (`test_plan.plan_de_prueba`, invented) and, at the end,
the whole way through the web: the coach proposes it in a conversation (Claude doubled), the plan stays the same
until the card is confirmed with /api/asistente/confirmar, and then Fitness › Plan shows it.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from test_plan import plan_de_prueba
from tridata.ia import CambioInterpretado
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.nutricion.asistente import Cambiado, Contexto, Peticion, catalogo, confirmar, descartar, proponer, \
    texto_acciones
from tridata.nutricion.recetas import Recetario
from tridata.plan import Planes

HOY = date(2026, 10, 22)  # Thursday of S9
AHORA = datetime(2026, 10, 22, 9, 0)
MIERCOLES = date(2026, 10, 28)  # S10
SERIES = {"apartado": 1, "fecha": "2026-10-28", "titulo": "Series de prueba 3×2000",
          "bloques": ["20' a 5:30", "3×2000 a 4:15 (3' rec)", "15' suave"], "clave": True}
NUEVA = "Series 3×1000 | 20' a 5:30 | 3×1000 a 4:05 (2' rec) | 15' suave"
ACCIONES = ("plan_cambiar_sesion", "plan_anadir_sesion", "plan_quitar_sesion", "plan_objetivo_carrera", "plan_nota")


class Base:
    def __init__(self, tmp_path, *, con_plan: bool = True) -> None:
        self.db = tmp_path / "data.sqlite3"
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1800, "kcal_por_paso": 0.04, "pasos_por_defecto": 9000, "objetivo_pct": -10},
                        desde=DESDE_SIEMPRE)
        self.planes = Planes(self.db)
        if con_plan:
            self.plan_id = self.planes.crear(plan_de_prueba(sesiones=[*plan_de_prueba()["sesiones"], SERIES]), AHORA)
        self.ctx = Contexto(registro=RegistroComidas(self.db), objetivos=ObjetivosDia(self.db), ajustes=ajustes,
                            pesos=RegistroPeso(self.db), recetario=Recetario(self.db), hoy=lambda: HOY,
                            planes=self.planes)

    def plan(self) -> dict:
        return self.planes.leer(self.plan_id)

    def uno(self, **campos) -> dict:
        (resultado,) = proponer([CambioInterpretado(**campos)], self.ctx, Peticion(texto="texto de prueba", fecha=HOY))
        return resultado


@pytest.fixture
def b(tmp_path) -> Base:
    return Base(tmp_path)


def _sesiones(plan: dict, dia: str) -> list[tuple[str, list[str]]]:
    return [(s["titulo"], s["bloques"]) for s in plan["sesiones"] if s["fecha"] == dia]


def test_el_catalogo_gana_la_familia_del_plan():  # R5
    assert set(ACCIONES) <= set(catalogo())
    texto = texto_acciones()
    for ident in ACCIONES:
        assert f"- {ident}:" in texto, ident
    linea = next(x for x in texto.splitlines() if x.startswith("- plan_cambiar_sesion:"))
    assert " | " in linea  # how the blocks travel in `texto`: flat (bug 036)


# -- R5 · C-182: change a session -----------------------------------------------------------------------------------

def test_cambiar_una_sesion_no_cambia_nada_hasta_confirmar(b):  # R5 · C-182, G-55
    antes = b.plan()
    t = b.uno(accion="plan_cambiar_sesion", dia=MIERCOLES, texto=NUEVA)
    assert t["tipo"] == "propuesta", t
    assert t["texto"] == "Voy a cambiar en el plan: mié 28/10 · Series 3×1000"
    assert "Series de prueba 3×2000" in t["antes"] and "3×2000 a 4:15 (3' rec)" in t["antes"]
    assert "Series 3×1000" in t["despues"] and "3×1000 a 4:05 (2' rec)" in t["despues"]
    assert t["lineas"] == ["20' a 5:30", "3×1000 a 4:05 (2' rec)", "15' suave"]
    assert b.plan() == antes  # proposing writes nothing
    descartar(t, b.ctx)
    assert b.plan() == antes  # discarded: nothing either
    hecho = confirmar(t, b.ctx)
    assert "28/10" in hecho["hecho"]
    despues = b.plan()
    assert _sesiones(despues, "2026-10-28") == [("Series 3×1000", ["20' a 5:30", "3×1000 a 4:05 (2' rec)", "15' suave"])]
    assert [s for s in despues["sesiones"] if s["fecha"] != "2026-10-28"] == \
        [s for s in antes["sesiones"] if s["fecha"] != "2026-10-28"]  # nothing else
    assert next(s for s in despues["sesiones"] if s["fecha"] == "2026-10-28")["clave"] is True


def test_cambiada_a_mano_entre_medias_se_rehace(b):  # R5 · G-41
    import sqlite3

    t = b.uno(accion="plan_cambiar_sesion", dia=MIERCOLES, texto=NUEVA)
    with sqlite3.connect(b.db) as conn:  # changed by hand (another card, another import) before confirming
        conn.execute("UPDATE plan_sesion SET bloques = ? WHERE fecha = '2026-10-28'", ('["50\' suave"]',))
    with pytest.raises(Cambiado) as exc:
        confirmar(t, b.ctx)
    assert exc.value.tarjeta["tipo"] == "propuesta" and "50' suave" in exc.value.tarjeta["antes"]
    assert _sesiones(b.plan(), "2026-10-28") == [("Series de prueba 3×2000", ["50' suave"])]  # nothing applied


# -- add, remove, the target of a race and a note ---------------------------------------------------------------------

def test_anadir_una_sesion(b):  # R5
    t = b.uno(accion="plan_anadir_sesion", dia=date(2026, 10, 31), texto="Tirada 1h30 | 1h30 a 5:30 | geles min 45")
    assert t["tipo"] == "propuesta" and t["texto"] == "Voy a añadir en el plan: sáb 31/10 · Tirada 1h30"
    assert t["antes"] is None and "1h30 a 5:30" in t["despues"]
    assert _sesiones(b.plan(), "2026-10-31") == []
    confirmar(t, b.ctx)
    assert _sesiones(b.plan(), "2026-10-31") == [("Tirada 1h30", ["1h30 a 5:30", "geles min 45"])]


def test_quitar_una_sesion(b):  # R5
    t = b.uno(accion="plan_quitar_sesion", dia=MIERCOLES)
    assert t["tipo"] == "propuesta" and t["texto"] == "Voy a quitar del plan: mié 28/10 · Series de prueba 3×2000"
    assert t["despues"] is None
    assert len(_sesiones(b.plan(), "2026-10-28")) == 1
    confirmar(t, b.ctx)
    assert _sesiones(b.plan(), "2026-10-28") == []


def test_el_objetivo_de_una_carrera_del_plan(b):  # R5
    t = b.uno(accion="plan_objetivo_carrera", objeto="maraton de ejemplo", texto="3:00")
    assert t["tipo"] == "propuesta"
    assert t["texto"] == "Voy a cambiar en el plan el objetivo de Maratón de ejemplo: 3:05 → 3:00"
    assert (t["antes"], t["despues"]) == ("3:05", "3:00")
    assert b.plan()["carreras"][1]["objetivo"] == "3:05"
    confirmar(t, b.ctx)
    assert [c["objetivo"] for c in b.plan()["carreras"]] == ["mejor marca", "3:00"]


def test_anadir_una_nota(b):  # R5
    t = b.uno(accion="plan_nota", texto="Nota de prueba: si el gemelo molesta, rodajes en llano.")
    assert t["tipo"] == "propuesta" and t["texto"].startswith("Voy a añadir en el plan una nota: Nota de prueba")
    assert b.plan()["notas"] == []
    confirmar(t, b.ctx)
    assert [(n["tipo"], n["texto"]) for n in b.plan()["notas"]] == [
        ("nota", "Nota de prueba: si el gemelo molesta, rodajes en llano.")]


# -- what is not proposed, and why --------------------------------------------------------------------------------

@pytest.mark.parametrize(("campos", "dice"), [
    ({"accion": "plan_cambiar_sesion", "dia": date(2026, 10, 14), "texto": NUEVA}, "ya pasó"),
    ({"accion": "plan_cambiar_sesion", "dia": date(2026, 10, 29), "texto": NUEVA}, "no hay ninguna sesión"),
    ({"accion": "plan_quitar_sesion", "dia": date(2026, 10, 29)}, "no hay ninguna sesión"),
    ({"accion": "plan_cambiar_sesion", "dia": MIERCOLES}, "qué sesión"),
    ({"accion": "plan_anadir_sesion", "texto": NUEVA}, "qué día"),
    ({"accion": "plan_objetivo_carrera", "objeto": "carrera que no está", "texto": "3:00"}, "no está en tu plan"),
    ({"accion": "plan_nota"}, "qué nota"),
], ids=["pasada", "sin-sesion", "quitar-sin-sesion", "sin-texto", "sin-dia", "sin-carrera", "nota-vacia"])
def test_lo_que_no_se_puede_lo_dice(b, campos, dice):  # R5 · G-40
    t = b.uno(**campos)
    assert t["tipo"] == "no" and dice in t["texto"].lower(), t


def test_sin_plan_lo_dice(tmp_path):  # R5
    b = Base(tmp_path, con_plan=False)
    t = b.uno(accion="plan_nota", texto="una nota")
    assert t["tipo"] == "no" and "plan" in t["texto"].lower()


# -- the whole way: a conversation with the coach, the card, Fitness › Plan ---------------------------------------

def test_el_entrenador_propone_y_el_plan_cambia_solo_al_confirmar(tmp_path):  # R5 · C-182, G-55
    from test_conversaciones_web import Web

    web = Web(tmp_path)
    Planes(web.db).crear(plan_de_prueba(sesiones=[*plan_de_prueba()["sesiones"], SERIES]), AHORA)
    web.claude.responder("Te dejo el cambio.", ("proponer_cambio", {"accion": "plan_cambiar_sesion",
                                                                    "dia": "2026-10-28", "texto": NUEVA}))
    web.claude.responder(" Confírmalo si te parece.")
    fin = web.nueva("las series de 2000 me parecen salvajes, pon 3×1000", papel="entrenador")
    (tarjeta,) = fin["mensaje"]["tarjetas"]
    assert tarjeta["texto"] == "Voy a cambiar en el plan: mié 28/10 · Series 3×1000"
    resultado = web.claude.peticiones[1]["messages"][-1]["content"][0]["content"]
    assert "no está hecho hasta que la confirme" in resultado  # Claude is told it is not done

    def miercoles() -> list[dict]:
        r = web.cliente.get("/api/plan/semana/10")
        assert r.status_code == 200, r.text
        return next(d for d in r.json()["dias"] if d["fecha"] == "2026-10-28")["sesiones"]

    assert [s["titulo"] for s in miercoles()] == ["Series de prueba 3×2000"]  # not confirmed: the same plan
    r = web.cliente.post("/api/asistente/confirmar", json={"tarjeta": tarjeta})
    assert r.status_code == 200, r.text
    assert miercoles() == [{"titulo": "Series 3×1000", "bloques": ["20' a 5:30", "3×1000 a 4:05 (2' rec)", "15' suave"],
                            "clave": True}]
