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
