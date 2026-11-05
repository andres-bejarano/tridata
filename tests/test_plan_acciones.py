"""Unit 041 · R5 (R-140, C-182, G-55): the cards that change the plan (`plan/acciones.py`), a family of the catalogue
of the 021: change, add or remove a planned session with its blocks, change the target of a race of the plan, add a
note. Nothing changes until Andrés confirms; a card whose session was changed by hand in between is redone (G-41).

Unit level over the catalogue with a seeded temporary SQLite (`test_plan.plan_de_prueba`, invented) and, at the end,
the whole way through the web: the coach proposes it in a conversation (Claude doubled), the plan stays the same
until the card is confirmed with /api/asistente/confirmar, and then Fitness › Plan shows it.
"""
from __future__ import annotations

import re
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


# -- unit 042 · R1-R6 (R-157..R-161, C-199..C-203): the card «Voy a mandar a tu Garmin» ---------------------------

LINEAS_SERIES = ["Calentamiento: 20' a 5:25-5:35 /km", "3 × 2000 m a 4:10-4:20 /km, con 3' de recuperación",
                 "Vuelta a la calma: 15' sin ritmo"]


@pytest.fixture
def garmin(monkeypatch):
    """Garmin doubled where the app connects to it (`plan.reloj.cliente_garmin`): never the real one."""
    from test_plan_reloj import GarminDoble

    from tridata.plan import reloj

    doble = GarminDoble()
    monkeypatch.setattr(reloj, "cliente_garmin", lambda entorno=None: doble)
    return doble


def _id_del(planes: Planes, plan_id: int, dia: str) -> int:
    (sesion,) = [s for s in planes.sesiones_reloj(plan_id) if s["fecha"] == dia]
    return int(sesion["id"])


def _estado_reloj(planes: Planes, plan_id: int, dia: str) -> str | None:
    return next(s["estado"] for s in planes.sesiones_reloj(plan_id) if s["fecha"] == dia)


def test_la_tarjeta_de_garmin_esta_en_el_catalogo():  # R1
    assert "mandar_a_garmin" in catalogo()
    assert "- mandar_a_garmin:" in texto_acciones()


def test_la_tarjeta_dice_bloques_margen_y_dia_y_no_manda_nada(b, garmin):  # R1, R5 · C-199, C-203
    t = b.uno(accion="mandar_a_garmin", dia=MIERCOLES)
    assert t["tipo"] == "propuesta", t
    assert t["texto"] == "Voy a mandar a tu Garmin: Series de prueba 3×2000 (mié 28/10)"
    assert t["lineas"] == LINEAS_SERIES
    assert t["dia"] == "2026-10-28"
    assert "Garmin Connect" in t["donde"]
    assert _estado_reloj(b.planes, b.plan_id, "2026-10-28") is None
    descartar(t, b.ctx)
    assert garmin.llamadas == []  # C-203: not confirmed, nothing goes to Garmin
    assert _estado_reloj(b.planes, b.plan_id, "2026-10-28") is None


def test_al_confirmar_queda_en_su_dia_y_el_plan_lo_marca(b, garmin):  # R2 · C-200
    t = b.uno(accion="mandar_a_garmin", dia=MIERCOLES)
    hecho = confirmar(t, b.ctx)
    assert "28/10" in hecho["hecho"] and "Garmin" in hecho["hecho"]
    assert [e["workoutName"] for e in garmin.del_dia(MIERCOLES)] == ["Series de prueba 3×2000"]
    (sesion,) = [s for s in b.planes.sesiones_reloj(b.plan_id) if s["fecha"] == "2026-10-28"]
    assert sesion["estado"] == "al_dia" and sesion["enviada"]
    guardada = b.planes.sesion(sesion["id"])
    assert guardada["garmin_workout_id"] in garmin.entrenos
    assert garmin.programados[guardada["garmin_schedule_id"]] == (guardada["garmin_workout_id"], MIERCOLES)


def test_cambiada_y_mandada_otra_vez_queda_una_sola(b, garmin):  # R4 · C-202
    confirmar(b.uno(accion="mandar_a_garmin", dia=MIERCOLES), b.ctx)
    confirmar(b.uno(accion="plan_cambiar_sesion", dia=MIERCOLES,
                    texto="Series 3×10' | 20' a 5:30 | 3×10' a 4:00 (2' rec) | 15' suave"), b.ctx)
    assert _estado_reloj(b.planes, b.plan_id, "2026-10-28") == "cambiada"  # the plan says it changed since
    t = b.uno(accion="mandar_a_garmin", dia=MIERCOLES)
    assert any("sustitu" in a for a in t["avisos"])  # the card says it replaces the one sent
    confirmar(t, b.ctx)
    assert [e["workoutName"] for e in garmin.del_dia(MIERCOLES)] == ["Series 3×10'"]
    assert len(garmin.entrenos) == 1
    assert _estado_reloj(b.planes, b.plan_id, "2026-10-28") == "al_dia"


def test_si_garmin_falla_lo_dice_y_ofrece_el_fichero(b, garmin):  # R3 · C-201
    from tridata.garmin_client import GarminRateLimitError
    from tridata.nutricion.asistente import NoValida

    garmin.falla_en, garmin.error = "subir_entreno", GarminRateLimitError("429")
    t = b.uno(accion="mandar_a_garmin", dia=MIERCOLES)
    with pytest.raises(NoValida) as exc:
        confirmar(t, b.ctx)
    assert "No ha llegado a tu Garmin" in str(exc.value) and "Bajar fichero" in str(exc.value)
    assert _estado_reloj(b.planes, b.plan_id, "2026-10-28") is None  # not marked
    assert [x["estado"] for x in b.ctx.tarjetas.todas()] == []  # nor written down as applied


@pytest.mark.parametrize(("dia", "bloques", "dice"), [
    (date(2026, 10, 14), None, "ya pasó"),
    (date(2026, 10, 29), None, "no hay ninguna sesión"),
    (MIERCOLES, ["20' a 5:30", "texto raro", "15' suave"], "«texto raro»"),
], ids=["pasada", "sin-sesion", "bloque-raro"])
def test_lo_que_no_se_manda_lo_dice(b, garmin, dia, bloques, dice):  # R6
    if bloques is not None:
        import json
        import sqlite3

        with sqlite3.connect(b.db) as conn:
            conn.execute("UPDATE plan_sesion SET bloques = ? WHERE fecha = '2026-10-28'", (json.dumps(bloques),))
    t = b.uno(accion="mandar_a_garmin", dia=dia)
    assert t["tipo"] == "no" and dice in t["texto"], t
    assert garmin.llamadas == []


# -- the same from Fitness › Plan and from a conversation, through the web -------------------------------------------

def _web_con_series(tmp_path):
    from test_conversaciones_web import Web

    web = Web(tmp_path)
    planes = Planes(web.db)
    plan_id = planes.crear(plan_de_prueba(sesiones=[*plan_de_prueba()["sesiones"], SERIES]), AHORA)
    return web, planes, plan_id


def _miercoles(web) -> dict:
    r = web.cliente.get("/api/plan/semana/10")
    assert r.status_code == 200, r.text
    return next(d for d in r.json()["dias"] if d["fecha"] == "2026-10-28")


def test_desde_fitness_plan_la_tarjeta_y_en_tu_garmin(tmp_path, garmin):  # R1, R2, R5 · C-199, C-200, C-203
    web, planes, plan_id = _web_con_series(tmp_path)
    (reloj_dia,) = _miercoles(web)["reloj"]  # one per session of the day, in its order
    assert reloj_dia["estado"] is None and reloj_dia["id"] == _id_del(planes, plan_id, "2026-10-28")
    r = web.cliente.post(f"/api/plan/sesion/{reloj_dia['id']}/garmin")
    assert r.status_code == 200, r.text
    tarjeta = r.json()["tarjeta"]
    assert tarjeta["texto"] == "Voy a mandar a tu Garmin: Series de prueba 3×2000 (mié 28/10)"
    assert tarjeta["lineas"] == LINEAS_SERIES
    assert garmin.llamadas == []  # the card is not the sending (G-55)
    r = web.cliente.post("/api/asistente/confirmar", json={"tarjeta": tarjeta})
    assert r.status_code == 200, r.text
    assert [e["workoutName"] for e in garmin.del_dia(MIERCOLES)] == ["Series de prueba 3×2000"]
    assert _miercoles(web)["reloj"][0]["estado"] == "al_dia"
    assert web.cliente.post("/api/plan/sesion/999999/garmin").status_code == 404


def test_desde_la_web_si_garmin_falla_se_baja_el_fichero(tmp_path, garmin):  # R3 · C-201
    web, planes, plan_id = _web_con_series(tmp_path)
    ident = _id_del(planes, plan_id, "2026-10-28")
    garmin.falla_en = "subir_entreno"
    tarjeta = web.cliente.post(f"/api/plan/sesion/{ident}/garmin").json()["tarjeta"]
    r = web.cliente.post("/api/asistente/confirmar", json={"tarjeta": tarjeta})
    assert r.status_code == 422 and "Bajar fichero" in r.json()["error"]
    assert _miercoles(web)["reloj"][0]["estado"] is None
    r = web.cliente.get(f"/api/plan/sesion/{ident}.fit")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert ".fit" in r.headers["content-disposition"]
    assert r.content[8:12] == b".FIT"
    from tridata.plan.reloj import fit_local
    assert r.content == fit_local(planes.sesion(ident))  # the same session
    assert web.cliente.get("/api/plan/sesion/999999.fit").status_code == 404


def test_desde_fitness_plan_un_bloque_raro_no_da_tarjeta(tmp_path, garmin):  # R6
    import json
    import sqlite3

    web, planes, plan_id = _web_con_series(tmp_path)
    ident = _id_del(planes, plan_id, "2026-10-28")
    with sqlite3.connect(web.db) as conn:
        conn.execute("UPDATE plan_sesion SET bloques = ? WHERE rowid = ?", (json.dumps(["texto raro"]), ident))
    r = web.cliente.post(f"/api/plan/sesion/{ident}/garmin")
    assert r.status_code == 422 and "«texto raro»" in r.json()["error"]
    assert web.cliente.get(f"/api/plan/sesion/{ident}.fit").status_code == 422
    assert garmin.llamadas == []


def test_el_entrenador_la_propone_y_solo_sale_al_confirmar(tmp_path, garmin):  # R1, R5 · C-199, C-203
    web, _, _ = _web_con_series(tmp_path)
    web.claude.responder("Te la dejo lista.", ("proponer_cambio", {"accion": "mandar_a_garmin", "dia": "2026-10-28"}))
    web.claude.responder(" Confírmala y la tienes en el reloj.")
    fin = web.nueva("mándame las series del miércoles al reloj", papel="entrenador")
    (tarjeta,) = fin["mensaje"]["tarjetas"]
    assert tarjeta["texto"] == "Voy a mandar a tu Garmin: Series de prueba 3×2000 (mié 28/10)"
    assert garmin.llamadas == []  # C-203
    assert web.cliente.post("/api/asistente/confirmar", json={"tarjeta": tarjeta}).status_code == 200
    assert [e["workoutName"] for e in garmin.del_dia(MIERCOLES)] == ["Series de prueba 3×2000"]


# -- unit 044 · R1, R2, R4, R6 (R-145..R-148, R-150 · C-187, C-188, C-190, C-192, C-204): incidents and notes -------

INCIDENCIAS = ("abrir_incidencia", "anadir_visita", "cerrar_incidencia", "nota_del_dia")
TOBILLO = {"accion": "abrir_incidencia", "objeto": "tobillo", "campo": "derecho, lateral", "desde": date(2026, 10, 20),
           "texto": "molestia inventada en el lateral al acabar el rodaje"}
VISITA = {"accion": "anadir_visita", "objeto": "tobillo", "campo": "fisio", "dia": date(2026, 10, 22),
          "texto": "sin lesión, alta para correr"}


def test_las_tarjetas_de_incidencias_y_notas_estan_en_el_catalogo():  # R1, R2, R4, R6
    texto = texto_acciones()
    for ident in INCIDENCIAS:
        assert ident in catalogo(), ident
        linea = next(x for x in texto.splitlines() if x.startswith(f"- {ident}:"))
        assert not any(c.isdigit() for c in linea), linea  # no figure for Claude (G-38)


def test_abrir_una_incidencia_solo_al_confirmar(b):  # R1 · C-187, C-204 — the criterio portante
    t = b.uno(**TOBILLO)
    assert t["tipo"] == "propuesta", t
    assert t["texto"] == "Voy a abrir una incidencia: tobillo derecho, lateral, desde el 20/10"
    assert t["antes"] is None and "molestia inventada en el lateral al acabar el rodaje" in t["despues"]
    assert "Fitness › Plan › Incidencias" in t["donde"]
    assert b.planes.incidencias(b.plan_id) == []  # C-204: proposing writes nothing
    descartar(t, b.ctx)
    assert b.planes.incidencias(b.plan_id) == []  # discarded: nothing either
    hecho = confirmar(t, b.ctx)
    assert "incidencia" in hecho["hecho"]
    (i,) = b.planes.incidencias(b.plan_id)
    assert (i["zona"], i["lado"], i["desde"], i["estado"]) == ("tobillo", "derecho, lateral", "2026-10-20", "abierta")
    assert i["que_paso"] == "molestia inventada en el lateral al acabar el rodaje" and i["visitas"] == []


def test_sin_decir_desde_cuando_es_desde_el_dia_que_lo_cuenta(b):  # R1
    t = b.uno(**{**TOBILLO, "desde": None})
    assert t["texto"] == "Voy a abrir una incidencia: tobillo derecho, lateral, desde el 22/10"


def test_abrir_otra_vez_la_misma_se_rehace(b):  # R1 · G-41
    t = b.uno(**TOBILLO)
    confirmar(t, b.ctx)
    with pytest.raises(Cambiado):
        confirmar(t, b.ctx)  # the same card twice: it is already open
    assert len(b.planes.incidencias(b.plan_id)) == 1


def test_anadir_una_visita_solo_al_confirmar(b):  # R2 · C-188
    confirmar(b.uno(**TOBILLO), b.ctx)
    t = b.uno(**VISITA)
    assert t["tipo"] == "propuesta", t
    assert t["texto"] == "Voy a añadir a la incidencia: visita al fisio del 22/10 · sin lesión, alta para correr"
    assert "tobillo derecho, lateral" in t["donde"]
    assert t["antes"] == "sin visitas" and "22/10" in t["despues"] and "alta para correr" in t["despues"]
    assert b.planes.incidencias(b.plan_id)[0]["visitas"] == []
    confirmar(t, b.ctx)
    assert b.planes.incidencias(b.plan_id)[0]["visitas"] == [
        {"fecha": "2026-10-22", "quien": "fisio", "que_dijo": "sin lesión, alta para correr"}]


def test_con_una_sola_abierta_no_hace_falta_nombrarla(b):  # R2
    confirmar(b.uno(**TOBILLO), b.ctx)
    t = b.uno(**{**VISITA, "objeto": None})
    assert t["tipo"] == "propuesta" and "tobillo derecho, lateral" in t["donde"]


def test_una_visita_cambiada_entre_medias_se_rehace(b):  # R2 · G-41
    confirmar(b.uno(**TOBILLO), b.ctx)
    t = b.uno(**VISITA)
    (i,) = b.planes.incidencias(b.plan_id)
    b.planes.anadir_visita(i["id"], date(2026, 10, 21), "médico", "reposo relativo inventado", AHORA)  # meanwhile
    with pytest.raises(Cambiado) as exc:
        confirmar(t, b.ctx)
    assert "reposo relativo inventado" in exc.value.tarjeta["antes"]
    assert len(b.planes.incidencias(b.plan_id)[0]["visitas"]) == 1  # nothing applied


def test_cerrar_con_su_leccion_solo_al_confirmar(b):  # R4 · C-190
    confirmar(b.uno(**TOBILLO), b.ctx)
    t = b.uno(accion="cerrar_incidencia", objeto="tobillo", texto="bici nunca tras la tirada larga")
    assert t["tipo"] == "propuesta", t
    assert t["texto"] == "Voy a cerrar la incidencia: tobillo derecho, lateral · lección: bici nunca tras la tirada larga"
    assert t["antes"] == "abierta desde el 20/10"
    assert b.planes.incidencias(b.plan_id)[0]["estado"] == "abierta"
    confirmar(t, b.ctx)
    (i,) = b.planes.incidencias(b.plan_id)
    assert (i["estado"], i["leccion"], i["cerrada"]) == ("cerrada", "bici nunca tras la tirada larga", "2026-10-22")
    assert b.uno(**VISITA)["tipo"] == "no"  # closed: it is not treated as open any more (R-148)


def test_cerrada_a_mano_entre_medias_se_rehace(b):  # R4 · G-41
    confirmar(b.uno(**TOBILLO), b.ctx)
    t = b.uno(accion="cerrar_incidencia", objeto="tobillo", texto="bici nunca tras la tirada larga")
    (i,) = b.planes.incidencias(b.plan_id)
    b.planes.cerrar_incidencia(i["id"], "otra lección inventada", HOY)  # closed by hand in Fitness › Plan
    with pytest.raises(Cambiado):
        confirmar(t, b.ctx)
    assert b.planes.incidencias(b.plan_id)[0]["leccion"] == "otra lección inventada"


def test_una_nota_del_dia_sale_en_lo_hecho(b):  # R6 · C-192
    from tridata.plan import semanas

    t = b.uno(accion="nota_del_dia", dia=date(2026, 10, 21), texto="FC alta para el ritmo, piernas del domingo")
    assert t["tipo"] == "propuesta", t
    assert t["texto"] == "Voy a apuntar en lo hecho del mié 21/10: FC alta para el ritmo, piernas del domingo"
    assert b.planes.notas_del_dia(b.plan_id) == {}
    confirmar(t, b.ctx)
    vista = semanas.semana(b.plan(), 9, None, HOY, notas=b.planes.notas_del_dia(b.plan_id))
    miercoles = next(d for d in vista["dias"] if d["fecha"] == "2026-10-21")
    assert miercoles["notas"] == ["FC alta para el ritmo, piernas del domingo"]
    otra = b.uno(accion="nota_del_dia", dia=date(2026, 10, 21), texto="dormí poco")
    assert otra["antes"] == "FC alta para el ritmo, piernas del domingo"  # what that day already says


@pytest.mark.parametrize(("campos", "dice"), [
    ({**TOBILLO, "objeto": None}, "dónde"),
    ({**TOBILLO, "texto": None}, "qué pasó"),
    ({**TOBILLO, "desde": date(2026, 10, 30)}, "aún no ha llegado"),
    (VISITA, "ninguna incidencia abierta"),
    ({"accion": "cerrar_incidencia", "objeto": "tobillo", "texto": "lección"}, "ninguna incidencia abierta"),
    ({"accion": "nota_del_dia", "dia": date(2026, 10, 30), "texto": "nota"}, "aún no ha llegado"),
    ({"accion": "nota_del_dia", "dia": date(2026, 10, 21)}, "qué nota"),
], ids=["sin-zona", "sin-que-paso", "desde-futuro", "visita-sin-abierta", "cerrar-sin-abierta", "nota-futura",
        "nota-vacia"])
def test_lo_que_no_se_puede_de_incidencias_lo_dice(b, campos, dice):  # G-40
    t = b.uno(**campos)
    assert t["tipo"] == "no" and dice in t["texto"].lower(), t


@pytest.mark.parametrize(("campos", "dice"), [
    ({**VISITA, "campo": None}, "quién"),
    ({**VISITA, "texto": None}, "qué te dijo"),
    ({**VISITA, "objeto": "rodilla"}, "rodilla"),
    ({"accion": "cerrar_incidencia", "objeto": "tobillo"}, "lección"),
], ids=["sin-quien", "sin-que-dijo", "otra-zona", "sin-leccion"])
def test_lo_que_no_se_puede_con_una_abierta_lo_dice(b, campos, dice):  # G-40
    confirmar(b.uno(**TOBILLO), b.ctx)
    t = b.uno(**campos)
    assert t["tipo"] == "no" and dice in t["texto"].lower(), t


def test_el_fisio_la_propone_y_solo_se_abre_al_confirmar(tmp_path):  # R1 · C-187, C-204 through the web
    from test_conversaciones_web import Web

    web = Web(tmp_path)
    Planes(web.db).crear(plan_de_prueba(), AHORA)
    web.claude.responder("Te la apunto.", ("proponer_cambio", {
        "accion": "abrir_incidencia", "objeto": "tobillo", "campo": "derecho, lateral", "desde": "2026-10-20",
        "texto": "molestia inventada al acabar el rodaje"}))
    web.claude.responder(" Confírmala si te parece.")
    fin = web.nueva("al acabar el rodaje noto el lateral del tobillo", papel="fisio")
    (tarjeta,) = fin["mensaje"]["tarjetas"]
    assert tarjeta["texto"] == "Voy a abrir una incidencia: tobillo derecho, lateral, desde el 20/10"
    assert web.cliente.get("/api/plan/incidencias").json()["incidencias"] == []  # C-204
    r = web.cliente.post("/api/asistente/confirmar", json={"tarjeta": tarjeta})
    assert r.status_code == 200, r.text
    (i,) = web.cliente.get("/api/plan/incidencias").json()["incidencias"]
    assert (i["titulo"], i["estado"]) == ("tobillo derecho, lateral", "abierta")


# -- unit 045 · R5, R6 (R-155, R-156, C-197, C-198, G-55): «Voy a guardar en el plan: estrategia de …» --------------

ESTRATEGIA = ("km 0-4 · llano · 4:15/km · paso 17:00 | km 4-6,5 · subida · por esfuerzo, ~4:40 · paso 28:40 | "
              "km 6,5-20 · bajada y llano · 4:10/km · paso 1:24:55 | gel: km 6,5, coronando")
LINEAS_ESTRATEGIA = ["km 0-4 · llano · 4:15/km · paso 17:00", "km 4-6,5 · subida · por esfuerzo, ~4:40 · paso 28:40",
                     "km 6,5-20 · bajada y llano · 4:10/km · paso 1:24:55", "gel: km 6,5, coronando"]


def test_la_estrategia_esta_en_el_catalogo_sin_cifras():  # R6 · G-38
    assert "guardar_estrategia" in catalogo()
    linea = next(x for x in texto_acciones().splitlines() if x.startswith("- guardar_estrategia:"))
    assert " | " in linea and not re.search(r"\d", linea)  # flat (bug 036) and no figure for Claude (G-38)


def test_guardar_la_estrategia_solo_al_confirmar(b):  # R6 · C-198, G-55
    t = b.uno(accion="guardar_estrategia", objeto="maraton de ejemplo", texto=ESTRATEGIA)
    assert t["tipo"] == "propuesta", t
    assert t["texto"] == "Voy a guardar en el plan: estrategia de Maratón de ejemplo (3 tramos, 1 gel)"
    assert t["lineas"] == LINEAS_ESTRATEGIA  # the table, one line per stretch
    assert t["antes"] is None
    assert "Maratón de ejemplo" in t["donde"] and "Fitness › Plan" in t["donde"]
    assert b.planes.estrategia(b.plan_id, "Maratón de ejemplo") == []  # proposing writes nothing
    descartar(t, b.ctx)
    assert b.planes.estrategia(b.plan_id, "Maratón de ejemplo") == []
    hecho = confirmar(t, b.ctx)
    assert "Maratón de ejemplo" in hecho["hecho"]
    assert b.planes.estrategia(b.plan_id, "Maratón de ejemplo") == LINEAS_ESTRATEGIA
    # in that race of the plan, and only there (read with `Planes.estrategia`: `leer` gives the races as before)
    assert b.planes.estrategia(b.plan_id, "10K de ejemplo") == []
    carrera = next(c for c in b.plan()["carreras"] if c["nombre"] == "Maratón de ejemplo")
    assert carrera["objetivo"] == "3:05"  # nothing else of the race changes


def test_otra_estrategia_dice_la_de_antes_y_la_sustituye(b):  # R6 · G-41
    confirmar(b.uno(accion="guardar_estrategia", objeto="Maratón de ejemplo", texto=ESTRATEGIA), b.ctx)
    otra = b.uno(accion="guardar_estrategia", objeto="Maratón de ejemplo", texto="km 0-20 · todo · 4:15/km · paso 1:25:00")
    assert otra["tipo"] == "propuesta" and otra["antes"] == " | ".join(LINEAS_ESTRATEGIA)
    igual = b.uno(accion="guardar_estrategia", objeto="Maratón de ejemplo", texto=ESTRATEGIA)
    assert igual["tipo"] == "no" and "ya" in igual["texto"].lower()
    # saved by another card in between: the one Andrés saw is redone, nothing applied
    import sqlite3
    with sqlite3.connect(b.db) as conn:
        conn.execute("UPDATE plan_carrera SET estrategia = ? WHERE nombre = 'Maratón de ejemplo'",
                     ('["km 0-20 · otra"]',))
    with pytest.raises(Cambiado):
        confirmar(otra, b.ctx)
    assert b.planes.estrategia(b.plan_id, "Maratón de ejemplo") == ["km 0-20 · otra"]


@pytest.mark.parametrize(("campos", "dice"), [
    ({"accion": "guardar_estrategia", "objeto": "carrera que no está", "texto": ESTRATEGIA}, "no está en tu plan"),
    ({"accion": "guardar_estrategia", "texto": ESTRATEGIA}, "qué carrera"),
    ({"accion": "guardar_estrategia", "objeto": "Maratón de ejemplo"}, "qué estrategia"),
], ids=["sin-carrera", "sin-nombre", "sin-texto"])
def test_lo_que_no_se_puede_de_la_estrategia_lo_dice(b, campos, dice):  # G-40
    t = b.uno(**campos)
    assert t["tipo"] == "no" and dice in t["texto"].lower(), t


# -- bug 055: the session travels as the watch reads it, with the pace inside its block ------------------------------

def test_la_sesion_se_describe_como_la_lee_el_reloj():  # bug 055 · R3
    texto = texto_acciones()
    for ident in ("plan_cambiar_sesion", "plan_anadir_sesion"):
        linea = next(x for x in texto.splitlines() if x.startswith(f"- {ident}:"))
        assert "dos puntos" in linea and "«rec»" in linea and "nunca en valor" in linea, ident


# -- unit 067 · R8, R10 (R-206, R-208 · C-255, C-257): a session deleted by hand leaves Garmin, and a card of the team
# whose session was changed by hand is redone -----------------------------------------------------------------------

def _mandada(web, planes: Planes, plan_id: int) -> int:
    ident = _id_del(planes, plan_id, "2026-10-28")
    tarjeta = web.cliente.post(f"/api/plan/sesion/{ident}/garmin").json()["tarjeta"]
    assert web.cliente.post("/api/asistente/confirmar", json={"tarjeta": tarjeta}).status_code == 200
    return ident


def _pasada_de_garmin(planes: Planes, plan_id: int, garmin) -> None:
    """The next sync from Garmin (061) with what the double has scheduled."""
    from tridata.plan.desde_garmin import Programado, cambios

    programados = [Programado(programado=s, entreno=w, fecha=dia.isoformat(), deporte="carrera",
                              titulo=garmin.entrenos[w]["workoutName"], bloques=("20' a 5:30",), actualizado="x")
                   for s, (w, dia) in garmin.programados.items() if w in garmin.entrenos]
    planes.aplicar_garmin(plan_id, cambios(planes.sesiones_garmin(plan_id, date(2026, 10, 21), date(2026, 11, 30)),
                                           programados))


def test_borrar_una_sesion_mandada_la_quita_de_garmin_y_no_vuelve(tmp_path, garmin):  # R8 · R-206, C-255
    web, planes, plan_id = _web_con_series(tmp_path)
    ident = _mandada(web, planes, plan_id)
    assert garmin.del_dia(MIERCOLES)  # in Garmin
    r = web.cliente.post("/api/plan/sesion/borrar", json={"sesion": ident})
    assert r.status_code == 200, r.text
    assert "aviso" not in r.json()
    assert garmin.del_dia(MIERCOLES) == [] and garmin.entrenos == {}  # Garmin was told to remove it
    assert [ll[0] for ll in garmin.llamadas[-2:]] == ["quitar_programado", "borrar_entreno"]
    assert _sesiones(planes.leer(plan_id), "2026-10-28") == []
    _pasada_de_garmin(planes, plan_id, garmin)
    assert _sesiones(planes.leer(plan_id), "2026-10-28") == []  # the sync from Garmin does not bring it back


def test_sin_quitarla_de_garmin_la_pasada_la_volveria_a_meter(tmp_path, garmin):  # R8: why it is removed from Garmin
    web, planes, plan_id = _web_con_series(tmp_path)
    ident = _mandada(web, planes, plan_id)
    with planes._connect() as conn:  # deleted without telling Garmin
        conn.execute("DELETE FROM plan_sesion WHERE rowid = ?", (ident,))
    _pasada_de_garmin(planes, plan_id, garmin)
    assert [t for t, _ in _sesiones(planes.leer(plan_id), "2026-10-28")] == ["Series de prueba 3×2000"]


def test_con_garmin_caido_se_borra_igual_y_avisa(tmp_path, garmin):  # R8 · C-255
    from tridata.garmin_client import GarminWorkoutError

    web, planes, plan_id = _web_con_series(tmp_path)
    ident = _mandada(web, planes, plan_id)
    garmin.falla_en, garmin.error = "quitar_programado", GarminWorkoutError("Garmin Connect did not answer")
    r = web.cliente.post("/api/plan/sesion/borrar", json={"sesion": ident})
    assert r.status_code == 200, r.text
    assert "sigue en tu reloj: bórrala allí" in r.json()["aviso"]
    assert planes.sesion(ident) is None and _sesiones(planes.leer(plan_id), "2026-10-28") == []


def test_borrar_una_semana_quita_de_garmin_sus_sesiones_mandadas(tmp_path, garmin):  # R5 + R8
    web, planes, plan_id = _web_con_series(tmp_path)
    _mandada(web, planes, plan_id)
    r = web.cliente.post("/api/plan/semana/borrar", json={"id": plan_id, "semana": 10})
    assert r.status_code == 200, r.text
    assert r.json()["borradas"] == 1 and garmin.entrenos == {}


def test_la_tarjeta_del_equipo_se_rehace_si_la_cambiaste_a_mano(b):  # R10 · R-208, C-257 (G-41)
    t = b.uno(accion="plan_cambiar_sesion", dia=MIERCOLES, texto=NUEVA)
    assert t["tipo"] == "propuesta", t
    ident = _id_del(b.planes, b.plan_id, "2026-10-28")
    b.planes.cambiar_sesion(ident, "Series de prueba 3×2000", ["50' suave"], HOY)  # by hand, before the «sí»
    with pytest.raises(Cambiado) as exc:
        confirmar(t, b.ctx)
    assert exc.value.tarjeta["tipo"] == "propuesta" and "50' suave" in exc.value.tarjeta["antes"]
    assert _sesiones(b.plan(), "2026-10-28") == [("Series de prueba 3×2000", ["50' suave"])]  # nothing applied blind
    b.planes.mover_sesion(ident, "2026-10-29", HOY)  # moved by hand: the card of that day says it is not there
    t2 = b.uno(accion="plan_cambiar_sesion", dia=MIERCOLES, texto=NUEVA)
    t3 = b.uno(accion="plan_quitar_sesion", dia=date(2026, 10, 29))
    assert t2["tipo"] == "no" and t3["tipo"] == "propuesta"


# -- unit 076 · R2 (R-217, C-267, criterio portante): the team puts the hour Andrés says in the session -----------------

VIERNES = date(2026, 10, 30)
RODAJE_45 = "Rodaje de prueba 45' | 45' a 5:40"


def test_el_equipo_anade_una_sesion_con_su_hora_y_con_el_si_queda_con_ella(b):  # R2 · C-267 (portante)
    t = b.uno(accion="plan_anadir_sesion", dia=VIERNES, texto=RODAJE_45, hora="20:00")
    assert t["tipo"] == "propuesta", t
    assert t["texto"] == "Voy a añadir en el plan: vie 30/10 · 20:00 · Rodaje de prueba 45'"
    assert t["despues"].startswith("20:00 · Rodaje de prueba 45'")
    assert _sesiones(b.plan(), "2026-10-30") == []  # nothing until the «sí»
    confirmar(t, b.ctx)
    (sesion,) = [s for s in b.plan()["sesiones"] if s["fecha"] == "2026-10-30"]
    assert (sesion["titulo"], sesion["bloques"], sesion["hora"]) == ("Rodaje de prueba 45'", ["45' a 5:40"], "20:00")


def test_el_equipo_cambia_una_sesion_y_le_pone_hora_o_conserva_la_que_tenia(b):  # R2 · R-217
    t = b.uno(accion="plan_cambiar_sesion", dia=MIERCOLES, texto=NUEVA, hora="19:30")
    assert t["tipo"] == "propuesta", t
    assert t["texto"] == "Voy a cambiar en el plan: mié 28/10 · 19:30 · Series 3×1000"
    assert not t["antes"].startswith("19:30") and t["despues"].startswith("19:30 · Series 3×1000")
    confirmar(t, b.ctx)
    ident = _id_del(b.planes, b.plan_id, "2026-10-28")
    assert b.planes.sesion(ident)["hora"] == "19:30"

    otra = b.uno(accion="plan_cambiar_sesion", dia=MIERCOLES, texto="Series 4×1000 | 4×1000 a 4:05 (2' rec)")
    assert otra["tipo"] == "propuesta", otra
    assert otra["antes"].startswith("19:30 · ") and otra["despues"].startswith("19:30 · Series 4×1000")
    confirmar(otra, b.ctx)
    assert (b.planes.sesion(ident)["titulo"], b.planes.sesion(ident)["hora"]) == ("Series 4×1000", "19:30")


def test_sin_hora_la_tarjeta_y_la_sesion_son_como_antes(b):  # R1 · R-216
    t = b.uno(accion="plan_anadir_sesion", dia=VIERNES, texto=RODAJE_45)
    assert t["texto"] == "Voy a añadir en el plan: vie 30/10 · Rodaje de prueba 45'"
    assert "hora" not in t["parametros"]
    confirmar(t, b.ctx)
    (sesion,) = [s for s in b.plan()["sesiones"] if s["fecha"] == "2026-10-30"]
    assert "hora" not in sesion


@pytest.mark.parametrize("mala", ["25:00", "a las tantas", "8 pm", "20.00"])
def test_una_hora_que_no_es_hhmm_no_se_propone(b, mala):  # R2: a «no» that says how it goes
    for accion, dia in (("plan_anadir_sesion", VIERNES), ("plan_cambiar_sesion", MIERCOLES)):
        t = b.uno(accion=accion, dia=dia, texto=RODAJE_45, hora=mala)
        assert t["tipo"] == "no", t
        assert "HH:MM" in t["texto"] and "00:00" in t["texto"] and "23:59" in t["texto"], t


def test_la_hora_se_describe_en_las_acciones_de_sesion_sin_cifras():  # R2 · G-38
    texto = texto_acciones()
    for ident in ("plan_cambiar_sesion", "plan_anadir_sesion"):
        linea = next(x for x in texto.splitlines() if x.startswith(f"- {ident}:"))
        assert "hora:" in linea and "HH:MM" in linea, ident
