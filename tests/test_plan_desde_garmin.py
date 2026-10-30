"""Unit 061 (`mandar-sesion-reloj`, «Garmin manda»): what Andrés changes in Garmin Connect reaches the plan.

`plan/desde_garmin.py` is pure: it pairs the workouts scheduled in Garmin with the sessions of the plan (by their ids,
then by day and sport, then by title) and says what changes (R1, R2, R3, R5); `Planes.aplicar_garmin` writes it IN
PLACE, so `<plan>-<orden>` (the key of Google Calendar and of the team) does not move. Every value is invented.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest
from test_plan_reloj import _paso_leido, entreno_leido

from tridata.plan import Planes, texto_sesion
from tridata.plan.desde_garmin import YA_NO_ESTA, Programado, cambios, deporte_de_sesion, programado

AHORA = datetime(2026, 10, 30, 8, 0)
HOY = date(2026, 10, 30)


def sesion(ident: int, fecha: str, titulo: str, bloques: list[str] | None = None, *, workout: int | None = None,
           schedule: int | None = None, actualizado: str | None = None) -> dict:
    return {"id": ident, "fecha": fecha, "titulo": titulo, "bloques": bloques or ["40' a 5:40"],
            "garmin_workout_id": workout, "garmin_schedule_id": schedule, "garmin_actualizado": actualizado}


def prog(sched: int, workout: int, fecha: str, titulo: str, bloques: list[str] | None = None, *,
         deporte: str = "carrera", actualizado: str = "2026-10-29T18:00:00.0") -> Programado:
    return Programado(programado=sched, entreno=workout, fecha=fecha, deporte=deporte, titulo=titulo,
                      bloques=tuple(bloques or ["50' a 5:30"]), actualizado=actualizado)


def _todo(p: Programado) -> dict:
    """The columns a session takes from Garmin when Garmin wins."""
    return {"titulo": p.titulo, "bloques": list(p.bloques), "fecha": p.fecha, "garmin_workout_id": p.entreno,
            "garmin_schedule_id": p.programado, "garmin_actualizado": p.actualizado,
            "garmin_texto": texto_sesion(p.titulo, list(p.bloques))}


# -- R1: a session sent by TriData and changed in Garmin --------------------------------------------------------------

def test_la_sesion_mandada_y_cambiada_en_garmin_toma_lo_de_garmin():  # R1 (portante)
    s = sesion(1, "2026-11-01", "Tirada 1h30", ["1h30 a 5:20"], workout=501, schedule=601,
               actualizado="2026-10-25T09:00:00.0")
    p = prog(601, 501, "2026-11-01", "Tirada 1h45", ["15' a 5:45", "1h20 a 5:15", "10' suave"],
             actualizado="2026-10-29T21:10:00.0")
    c = cambios([s], [p])
    assert c.actualizar == {1: _todo(p)}
    assert c.anadir == [] and c.avisos == {1: None}


def test_si_garmin_la_movio_de_dia_la_sesion_va_al_dia_nuevo():  # R1: the day too
    s = sesion(1, "2026-11-01", "Tirada 1h30", ["1h30 a 5:20"], workout=501, schedule=601,
               actualizado="2026-10-25T09:00:00.0")
    p = prog(602, 501, "2026-10-31", "Tirada 1h30", ["1h30 a 5:20"], actualizado="2026-10-29T21:10:00.0")
    assert cambios([s], [p]).actualizar[1]["fecha"] == "2026-10-31"


def test_movida_sin_otra_fecha_de_cambio_tambien_va_al_dia_nuevo():  # R1: dragging it does not touch the workout
    s = sesion(1, "2026-11-01", "Tirada 1h30", ["1h30 a 5:20"], workout=501, schedule=601,
               actualizado="2026-10-25T09:00:00.0")
    p = prog(601, 501, "2026-10-31", "Tirada 1h30", ["1h30 a 5:20"], actualizado="2026-10-25T09:00:00.0")
    assert cambios([s], [p]).actualizar == {1: {"fecha": "2026-10-31"}}


def test_la_misma_fecha_de_cambio_no_toca_nada():  # R1: not converted again on every pass
    s = sesion(1, "2026-11-01", "Tirada 1h30", ["1h30 a 5:20"], workout=501, schedule=601,
               actualizado="2026-10-29T21:10:00.0")
    p = prog(601, 501, "2026-11-01", "Otro texto que no se mira", actualizado="2026-10-29T21:10:00.0")
    c = cambios([s], [p])
    assert c.actualizar == {} and c.anadir == [] and c.avisos == {1: None}


def test_recien_mandada_solo_apunta_su_fecha_de_cambio():  # R1: what TriData just sent is not rewritten
    s = sesion(1, "2026-11-01", "Tirada 1h30", ["1h30 a 5:20"], workout=501, schedule=601)
    p = prog(601, 501, "2026-11-01", "Tirada 1h30", ["1h30 a 5:20"], actualizado="2026-10-30T07:00:00.0")
    assert cambios([s], [p]).actualizar == {1: {"garmin_actualizado": "2026-10-30T07:00:00.0"}}


# -- R2: a workout of Garmin that TriData did not send -----------------------------------------------------------------

def test_uno_de_garmin_sustituye_la_unica_sesion_de_ese_dia_y_deporte():  # R2 (portante)
    s = sesion(1, "2026-11-01", "LR 1h30", ["1h30 a 5:20"])
    otra = sesion(2, "2026-11-01", "Natación técnica", ["30' técnica"])  # another sport the same day: untouched
    p = prog(611, 511, "2026-11-01", "Long run inventado", ["20' a 5:40", "1h10 a 5:10"])
    c = cambios([s, otra], [p])
    assert c.actualizar == {1: _todo(p)}
    assert c.anadir == [] and c.avisos == {1: None, 2: None}


def test_con_varias_del_mismo_deporte_gana_la_del_mismo_titulo():  # R2 · 048
    a = sesion(1, "2026-11-02", "Rodaje mañana", ["40' a 5:40"])
    b = sesion(2, "2026-11-02", "Series tarde", ["8×400 m a 3:50 (200 m rec)"])
    p = prog(612, 512, "2026-11-02", "series  TARDE", ["10×400 m a 3:50 (200 m rec)"])
    c = cambios([a, b], [p])
    assert c.actualizar == {2: _todo(p)}
    assert c.avisos == {1: None, 2: None}


def test_con_varias_y_ningun_titulo_igual_no_se_toca_nada_y_se_avisa():  # R2 · 048
    a = sesion(1, "2026-11-02", "Rodaje mañana", ["40' a 5:40"])
    b = sesion(2, "2026-11-02", "Series tarde", ["8×400 m a 3:50 (200 m rec)"])
    p = prog(612, 512, "2026-11-02", "Cuestas inventadas", ["10×30\" a 3:40 (1' rec)"])
    c = cambios([a, b], [p])
    assert c.actualizar == {} and c.anadir == []
    assert c.avisos[1] and c.avisos[1] == c.avisos[2]
    assert "Cuestas inventadas" in c.avisos[1]


# -- R3: a workout of Garmin on a day of the plan without a session of its sport ---------------------------------------

def test_uno_de_garmin_en_un_dia_sin_sesion_de_su_deporte_se_anade():  # R3
    natacion = sesion(1, "2026-11-03", "Natación técnica", ["30' técnica"])
    p = prog(613, 513, "2026-11-03", "Rodaje inventado", ["45' a 5:35"])
    bici = prog(614, 514, "2026-11-04", "Bici inventada", ["1h"], deporte="bici")
    c = cambios([natacion], [p, bici])
    assert c.actualizar == {}
    assert c.anadir == [_todo(p), _todo(bici)]


def test_una_sesion_ya_emparejada_no_se_empareja_otra_vez():  # R1 + R3: the second one is new
    s = sesion(1, "2026-11-01", "Tirada", ["1h30 a 5:20"], workout=501, schedule=601,
               actualizado="2026-10-29T21:10:00.0")
    mismo = prog(601, 501, "2026-11-01", "Tirada", actualizado="2026-10-29T21:10:00.0")
    otro = prog(615, 515, "2026-11-01", "Trote de la tarde", ["20' suave"])
    c = cambios([s], [otro, mismo])
    assert c.actualizar == {} and c.anadir == [_todo(otro)]


# -- R5: what is not in Garmin any more --------------------------------------------------------------------------------

def test_lo_que_ya_no_esta_en_garmin_no_se_borra_y_se_avisa():  # R5
    mandada = sesion(1, "2026-11-01", "Tirada", ["1h30 a 5:20"], workout=501, schedule=601,
                     actualizado="2026-10-29T21:10:00.0")
    nunca = sesion(2, "2026-11-02", "Rodaje", ["40' a 5:40"])  # never in Garmin: nothing to say
    c = cambios([mandada, nunca], [])
    assert c.actualizar == {} and c.anadir == []
    assert c.avisos == {1: YA_NO_ESTA, 2: None}
    assert YA_NO_ESTA == "Ya no está en tu Garmin"


def test_si_la_sustituye_otra_de_garmin_ya_no_hay_aviso():  # R2 + R5
    vieja = sesion(1, "2026-11-01", "Tirada", ["1h30 a 5:20"], workout=501, schedule=601,
                   actualizado="2026-10-29T21:10:00.0")
    p = prog(616, 516, "2026-11-01", "Tirada nueva", ["1h40 a 5:20"])
    c = cambios([vieja], [p])
    assert c.actualizar == {1: _todo(p)} and c.avisos == {1: None}


# -- the sport of a session and of a workout of Garmin ------------------------------------------------------------------

@pytest.mark.parametrize(("titulo", "deporte"), [
    ("Series 4×1000", "carrera"), ("Rodillo Z2", "bici"), ("Bici larga", "bici"), ("Natación técnica", "natacion"),
    ("Piscina 2000 m", "natacion"), ("Gimnasio: core", "fuerza"), ("Fuerza", "fuerza"), ("LR 1h30", "carrera")])
def test_el_deporte_de_una_sesion_sale_de_su_titulo(titulo, deporte):
    assert deporte_de_sesion(titulo) == deporte


def test_un_programado_lleva_el_texto_su_fecha_de_cambio_y_su_deporte():  # R4
    item = {"programado": 620, "entreno": 520, "fecha": "2026-11-05", "titulo": "Rodaje inventado",
            "deporte": "running"}
    detalle = entreno_leido("Rodaje inventado", _paso_leido(1, "interval", "time", 2700, (330, 340)),
                            actualizado="2026-10-28T10:00:00.0")
    assert programado(item, detalle) == Programado(programado=620, entreno=520, fecha="2026-11-05",
                                                   deporte="carrera", titulo="Rodaje inventado",
                                                   bloques=("45' a 5:35",), actualizado="2026-10-28T10:00:00.0")
    bici = programado({**item, "deporte": "cycling"},
                      entreno_leido("Bici", deporte="cycling", duracion=3600, descripcion=None))
    assert (bici.deporte, bici.bloques) == ("bici", ("1h",))


# -- Planes: written in place -------------------------------------------------------------------------------------------

PLAN = {"nombre": "Road to Inventada 26", "apartados": [{"titulo": "Semanas", "parte": "previsto"}],
        "carreras": [{"apartado": 0, "nombre": "Media inventada", "fecha": "2026-11-22", "objetivo": "1:30",
                      "estado": "pendiente", "principal": True}],
        "semanas": [{"apartado": 0, "n": 1, "desde": "2026-10-26", "hasta": "2026-11-01", "previsto_s": 14400,
                     "clave": "tirada", "hecho_plan_s": None, "hecho_plan_km": None},
                    {"apartado": 0, "n": 2, "desde": "2026-11-02", "hasta": "2026-11-08", "previsto_s": 14400,
                     "clave": "series", "hecho_plan_s": None, "hecho_plan_km": None}],
        "sesiones": [{"apartado": 0, "fecha": "2026-10-20", "titulo": "Rodaje viejo", "bloques": ["30' suave"],
                      "clave": False},
                     {"apartado": 0, "fecha": "2026-11-01", "titulo": "LR 1h30", "bloques": ["1h30 a 5:20"],
                      "clave": True},
                     {"apartado": 0, "fecha": "2026-11-04", "titulo": "Series", "bloques": ["20' suave"],
                      "clave": True}],
        "nutricion": [], "incidencias": [], "notas": []}


def _filas(planes: Planes, plan_id: int) -> list[tuple]:
    with planes._connect() as conn:
        return [tuple(f) for f in conn.execute(
            "SELECT orden, fecha, titulo, bloques, garmin_workout_id, garmin_actualizado FROM plan_sesion "
            "WHERE plan_id = ? ORDER BY orden", (plan_id,))]


def test_el_fin_del_plan_y_las_sesiones_de_la_ventana(tmp_path):
    planes = Planes(tmp_path / "data.sqlite3")
    plan_id = planes.crear(PLAN, AHORA)
    assert planes.fin(plan_id) == date(2026, 11, 22)  # the main race is the last day
    ventana = planes.sesiones_garmin(plan_id, date(2026, 10, 27), date(2026, 11, 22))
    assert [(s["fecha"], s["titulo"], s["garmin_workout_id"], s["garmin_actualizado"]) for s in ventana] == [
        ("2026-11-01", "LR 1h30", None, None), ("2026-11-04", "Series", None, None)]
    assert planes.fin(9999) is None


def test_aplicar_cambia_en_sitio_anade_al_final_y_guarda_los_avisos(tmp_path):  # R1, R3, R5
    planes = Planes(tmp_path / "data.sqlite3")
    plan_id = planes.crear(PLAN, AHORA)
    lr, series = planes.sesiones_garmin(plan_id, date(2026, 10, 27), date(2026, 11, 22))
    p = prog(611, 511, "2026-11-01", "Long run inventado", ["20' a 5:40", "1h10 a 5:10"])
    nuevo = prog(613, 513, "2026-11-03", "Rodaje inventado", ["45' a 5:35"])
    c = cambios([lr, series], [p, nuevo])
    c.avisos[series["id"]] = YA_NO_ESTA  # as if it had been sent and removed in Garmin
    resumen = planes.aplicar_garmin(plan_id, c)
    assert resumen == {"cambiadas": 1, "anadidas": 1, "avisos": 1}
    filas = _filas(planes, plan_id)
    assert [f[:3] for f in filas] == [(0, "2026-10-20", "Rodaje viejo"), (1, "2026-11-01", "Long run inventado"),
                                      (2, "2026-11-04", "Series"), (3, "2026-11-03", "Rodaje inventado")]
    assert filas[1][4:] == (511, "2026-10-29T18:00:00.0")
    reloj = {s["fecha"]: s for s in planes.sesiones_reloj(plan_id)}
    assert reloj["2026-11-01"]["estado"] == "al_dia"  # it is in the watch as it is now
    assert reloj["2026-11-01"]["aviso"] is None
    assert reloj["2026-11-04"]["aviso"] == YA_NO_ESTA
    assert reloj["2026-11-03"]["estado"] == "al_dia"
    assert planes.leer(plan_id)["sesiones"][3] == {"apartado": 0, "fecha": "2026-11-03", "titulo": "Rodaje inventado",
                                                   "bloques": ["45' a 5:35"], "clave": False}
    # the next pass clears a warning that is not true any more
    planes.aplicar_garmin(plan_id, cambios(planes.sesiones_garmin(plan_id, date(2026, 10, 27), date(2026, 11, 22)),
                                           [p, nuevo]))
    assert {s["fecha"]: s["aviso"] for s in planes.sesiones_reloj(plan_id)}["2026-11-04"] is None


def test_una_base_vieja_gana_las_columnas_nuevas(tmp_path):
    db = tmp_path / "data.sqlite3"
    Planes(db).crear(PLAN, AHORA)
    with Planes(db)._connect() as conn:
        columnas = {f["name"] for f in conn.execute("PRAGMA table_info(plan_sesion)")}
    assert {"garmin_actualizado", "garmin_aviso"} <= columnas


def test_el_mismo_programado_dos_veces_no_anade_otra_sesion():  # bug 064: the month views overlap
    s = sesion(1, "2026-11-01", "Tirada", workout=501, schedule=601, actualizado="2026-10-29T18:00:00.0")
    p = prog(601, 501, "2026-11-01", "Tirada")
    c = cambios([s], [p, p])
    assert c.anadir == []
    assert c.avisos[1] is None
