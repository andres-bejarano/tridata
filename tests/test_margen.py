"""Full margin of the day (unit 008): expenditure with Garmin and the planned training, levels,
bar, healthy limit, what still fits and the days of delay. Pure rules over invented data.

Settings of every case unless said: hand-written basal 1600 kcal and no weight saved (so
`basal_del_dia` gives 1600), 0.04 kcal/step, 10,000 steps by default and no plan.
"""
import sqlite3
from datetime import date, timedelta

import pytest

from tridata.models import Activity, DailyStats
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, basal_del_dia, resumen_del_dia
from tridata.nutricion.gasto import estimar_gasto, kcal_previstas
from tridata.nutricion.margen import (
    barra, config_del_dia, dias_de_retraso, gramos_que_caben, limite_saludable, margenes, simular, texto_retraso,
)

HOY = date(2026, 3, 10)  # a Tuesday
LUNES = HOY - timedelta(days=1)
AYER = LUNES


def _ajustes(db, **extra):
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000, **extra},
                    desde=DESDE_SIEMPRE)
    return ajustes


def _config(tmp_path, objetivo=None):
    """The ConfigGasto of the day as unit 007 builds it: basal of `basal_del_dia`, planned or default steps."""
    vigentes = _ajustes(tmp_path / "data.sqlite3").vigentes(HOY)
    return config_del_dia(vigentes.config_gasto(), basal_del_dia(vigentes, [], HOY), objetivo)


def _stats(pasos, dia=HOY):
    return DailyStats(stat_date=dia, steps=pasos, total_calories=9999).to_dict()


def _actividad(id_, tipo, kcal, minutos, cadencia=None, dia=HOY, nombre=None):
    return Activity(activity_id=id_, activity_date=dia, activity_type=tipo, name=nombre or tipo.capitalize(),
                    duration_seconds=minutos * 60, calories=kcal, avg_cadence=cadencia).to_dict()


NATACION = _actividad("n1", "lap_swimming", 300, 45, nombre="Natación")
CARRERA = _actividad("c1", "running", 400, 30, cadencia=170, nombre="Carrera")  # 5,100 estimated steps


# -- R1: expenditure = basal + steps without running + done today + planned (criterio portante) --------

def test_gasto_completo_con_pasos_y_natacion(tmp_path):  # R1
    gasto = estimar_gasto(_config(tmp_path), _stats(4000), [NATACION], None)

    assert gasto["kcal"] == 2300  # 1600 + 10000 x 0.04 + 300
    d = gasto["desglose"]
    assert d["basal_kcal"] == 1600
    assert d["pasos_hechos"] == 4000 and d["pasos_supuestos"] == 6000
    assert d["kcal_por_paso"] == 0.04 and d["pasos_kcal"] == 400
    assert [(a["nombre"], a["kcal"]) for a in d["entreno_hecho"]] == [("Natación", 300)]
    assert d["entreno_hecho_kcal"] == 300
    assert d["previsto"] is None and d["previsto_kcal"] == 0
    assert gasto["garmin"]["hay_datos"] is True


def test_margen_con_niveles_menos_20_y_menos_15(tmp_path):  # R1
    objetivo = {"tipo": None, "porcentaje": None, "niveles": [-20, -15], "entreno": None, "pasos": None}
    vigentes = _ajustes(tmp_path / "data.sqlite3").vigentes(HOY)
    basal = basal_del_dia(vigentes, [], HOY)
    config = config_del_dia(vigentes.config_gasto(), basal, objetivo)
    gasto = estimar_gasto(config, _stats(4000), [NATACION], None)
    apuntes = [{"dia": HOY.isoformat(), "estado": "comido", "kcal": 1000.0, "pendiente_cantidad": False}]

    resumen = resumen_del_dia(apuntes, HOY, vigentes.config_gasto(), objetivo_dia=objetivo, basal=basal,
                              gasto_estimado=gasto)

    assert resumen["gasto"]["kcal"] == 2300
    assert [(m["nivel"], m["kcal"]) for m in resumen["margenes"]] == [(-20, 840), (-15, 955)]
    assert resumen["gasto"]["desglose"]["pasos_hechos"] == 4000
    assert resumen["gasto"]["entreno_suma_kcal"] is True


def test_pasos_previstos_por_la_007_cuentan_en_vez_de_los_por_defecto(tmp_path):  # R1
    objetivo = {"tipo": None, "porcentaje": None, "niveles": None, "entreno": None, "pasos": 8000}
    gasto = estimar_gasto(_config(tmp_path, objetivo), _stats(4000), [NATACION], None)
    assert gasto["kcal"] == 2220
    assert gasto["desglose"]["pasos_supuestos"] == 4000


# -- R2: running steps are not counted twice ----------------------------------------------------

def test_pasos_de_carrera_no_cuentan_dos_veces(tmp_path):  # R2
    gasto = estimar_gasto(_config(tmp_path), _stats(16000), [CARRERA], None)
    d = gasto["desglose"]
    assert d["pasos_carrera_estimados"] == 5100
    assert d["pasos_hechos"] == 10900 and d["pasos_supuestos"] == 0
    assert gasto["pasos"] == 10900
    assert gasto["kcal"] == 2436  # 1600 + 436 + 400


# -- R3: steps lower than what the run alone estimates are not used ------------------------------

def test_pasos_incompletos_no_se_usan_y_avisa(tmp_path):  # R3
    gasto = estimar_gasto(_config(tmp_path), _stats(3000), [CARRERA], None)
    assert gasto["kcal"] == 2400  # 1600 + 10000 x 0.04 + 400
    assert gasto["desglose"]["pasos_hechos"] is None and gasto["desglose"]["pasos_supuestos"] == 10000
    assert gasto["desglose"]["pasos_garmin"] == 3000
    assert any("incompleto" in aviso for aviso in gasto["avisos"])
    assert gasto["desglose"]["entreno_hecho_kcal"] == 400


def test_el_total_de_kcal_del_dia_de_garmin_no_se_usa(tmp_path):  # R3
    con_total = estimar_gasto(_config(tmp_path), _stats(4000), [NATACION], None)
    sin_total = estimar_gasto(_config(tmp_path), {**_stats(4000), "total_calories": None}, [NATACION], None)
    assert con_total["kcal"] == sin_total["kcal"] == 2300


# -- R4: no Garmin data today ---------------------------------------------------------------------

def test_sin_datos_de_garmin(tmp_path):  # R4
    gasto = estimar_gasto(_config(tmp_path), None, [], None)
    assert gasto["kcal"] == 2000
    assert gasto["garmin"]["hay_datos"] is False
    assert "sin datos de Garmin de hoy" in gasto["garmin"]["texto"]
    assert gasto["desglose"]["entreno_hecho"] == []


def test_actividad_sin_kcal_suma_cero_y_se_nombra(tmp_path):  # R4
    sin_kcal = _actividad("f1", "strength_training", None, 40, nombre="Fuerza")
    gasto = estimar_gasto(_config(tmp_path), _stats(12000), [sin_kcal], None)
    assert gasto["kcal"] == 2080  # 1600 + 12000 x 0.04 + 0
    (hecho,) = gasto["desglose"]["entreno_hecho"]
    assert hecho["nombre"] == "Fuerza" and hecho["kcal"] == 0 and hecho["sin_kcal"] is True
    assert any("Fuerza" in aviso for aviso in gasto["avisos"])


# -- R5: planned training scaled by duration ------------------------------------------------------

def test_previsto_escalado_por_duracion(tmp_path):  # R5
    carrera_lunes = _actividad("l1", "running", 350, 35, cadencia=170, dia=LUNES)
    assert kcal_previstas(carrera_lunes, 30) == 300

    previsto = {"actividad_id": "l1", "nombre": "Running", "minutos": 30, "kcal": 300}
    sin = estimar_gasto(_config(tmp_path), None, [], None)
    con = estimar_gasto(_config(tmp_path), None, [], previsto)
    assert con["kcal"] - sin["kcal"] == 300
    assert con["desglose"]["previsto_kcal"] == 300 and con["desglose"]["previsto"]["minutos"] == 30


@pytest.mark.parametrize("minutos", [0, -5, "mucho", None])
def test_previsto_con_minutos_malos_se_rechaza(minutos):  # R5
    with pytest.raises(ValueError):
        kcal_previstas(_actividad("l1", "running", 350, 35, dia=LUNES), minutos)


# -- R6: levels of the day, saved for that day only; 1-5 levels of -50..+50 % --------------------

def test_niveles_elegidos_se_guardan_para_hoy_y_manana_vuelven(tmp_path):  # R6
    objetivos = ObjetivosDia(tmp_path / "data.sqlite3")
    objetivos.cambiar(HOY, niveles=[-20, -15, 0])
    assert objetivos.del_dia(HOY)["niveles"] == [-20, -15, 0]
    assert objetivos.del_dia(HOY + timedelta(days=1)) is None


def test_niveles_se_cambian_dos_veces_el_mismo_dia(tmp_path):  # R6 (precisión de Andrés)
    objetivos = ObjetivosDia(tmp_path / "data.sqlite3")
    objetivos.cambiar(HOY, niveles=[-20, -15, 0])
    objetivos.cambiar(HOY, niveles=[-30])
    assert objetivos.del_dia(HOY)["niveles"] == [-30]
    with sqlite3.connect(tmp_path / "data.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM objetivo_dia").fetchone()[0] == 1


def test_vale_un_solo_nivel(tmp_path):  # R6 (precisión de Andrés)
    objetivos = ObjetivosDia(tmp_path / "data.sqlite3")
    objetivos.cambiar(HOY, niveles=[0])
    assert objetivos.del_dia(HOY)["niveles"] == [0]
    resumen = resumen_del_dia([], HOY, _config(tmp_path), objetivo_dia=objetivos.del_dia(HOY))
    assert [m["nivel"] for m in resumen["margenes"]] == [0]


@pytest.mark.parametrize("niveles", [[-60], [-20, 51], [], [-30, -20, -10, 0, 10, 20], ["mucho"]])
def test_niveles_no_validos_no_cambian_nada(tmp_path, niveles):  # R6
    objetivos = ObjetivosDia(tmp_path / "data.sqlite3")
    objetivos.cambiar(HOY, niveles=[-20, -15])
    with pytest.raises(ValueError):
        objetivos.cambiar(HOY, niveles=niveles)
    assert objetivos.del_dia(HOY)["niveles"] == [-20, -15]


def test_cambiar_no_pierde_lo_demas_del_dia(tmp_path):  # R6, R7
    objetivos = ObjetivosDia(tmp_path / "data.sqlite3")
    objetivos.guardar(HOY, {"tipo": "entreno", "entreno": "rodaje", "pasos": 8000})
    objetivos.cambiar(HOY, porcentaje=0)
    objetivos.cambiar(HOY, previsto={"actividad_id": "l1", "nombre": "Carrera", "minutos": 30, "kcal": 300})
    dia = objetivos.del_dia(HOY)
    assert (dia["tipo"], dia["entreno"], dia["pasos"], dia["porcentaje"]) == ("entreno", "rodaje", 8000, 0)
    assert dia["previsto"]["kcal"] == 300
    objetivos.cambiar(HOY, previsto=None)
    assert objetivos.del_dia(HOY)["previsto"] is None and objetivos.del_dia(HOY)["pasos"] == 8000


def test_el_nuevo_dia_a_mitad_del_dia_sustituye_los_niveles(tmp_path):  # R6 (precisión de Andrés)
    objetivos = ObjetivosDia(tmp_path / "data.sqlite3")
    objetivos.cambiar(HOY, niveles=[-20, -15], previsto={"actividad_id": "l1", "nombre": "C", "minutos": 30,
                                                          "kcal": 300})
    objetivos.guardar(HOY, {"tipo": "entreno", "niveles": [-10]})  # what unit 007 saves from the text box
    dia = objetivos.del_dia(HOY)
    assert dia["niveles"] == [-10]
    assert dia["previsto"]["kcal"] == 300  # the planned training chosen on screen stays


def test_la_tabla_de_la_007_gana_la_columna_previsto_sin_perder_filas(tmp_path):
    db = tmp_path / "data.sqlite3"
    with sqlite3.connect(db) as conn:  # the table exactly as unit 007 left it
        conn.execute("CREATE TABLE objetivo_dia (dia TEXT PRIMARY KEY, tipo TEXT, porcentaje REAL, niveles TEXT, "
                     "entreno TEXT, pasos INTEGER, creado TEXT NOT NULL)")
        conn.execute("INSERT INTO objetivo_dia VALUES ('2026-03-09', 'descanso', NULL, NULL, NULL, NULL, 'x')")
    ObjetivosDia(db)
    objetivos = ObjetivosDia(db)  # twice: idempotent
    assert objetivos.del_dia(AYER)["tipo"] == "descanso" and objetivos.del_dia(AYER)["previsto"] is None


# -- R7: days of delay of the weight target -------------------------------------------------------

def _plan(pct, hasta):
    return {HOY + timedelta(days=i): pct for i in range((hasta - HOY).days + 1)}


@pytest.mark.parametrize(("nuevo", "dias"), [(0, 1), (-10, 0.5), (-30, -0.5), (-20, 0)])
def test_dias_de_retraso(nuevo, dias):  # R7
    assert dias_de_retraso(_plan(-20, date(2026, 3, 30)), HOY, nuevo) == pytest.approx(dias)


def test_dias_de_retraso_sin_plan_o_fuera_no_hay_numero():  # R7
    assert dias_de_retraso({}, HOY, 0) is None
    assert dias_de_retraso(_plan(0, date(2026, 3, 30)), HOY, -10) is None  # a 0 % plan never gets there
    assert dias_de_retraso({HOY: None, HOY + timedelta(days=1): None}, HOY, -10) is None


def test_texto_del_retraso():  # R7
    assert texto_retraso(1) == "llegas al objetivo 1 día más tarde"
    assert texto_retraso(0.5) == "llegas al objetivo 0,5 días más tarde"
    assert texto_retraso(-0.5) == "llegas al objetivo 0,5 días antes"
    assert texto_retraso(0) == "llegas al objetivo el mismo día"


# -- R8: yesterday does not count -----------------------------------------------------------------

def test_lo_de_ayer_no_cuenta_en_hoy(tmp_path):  # R8
    apuntes = [{"dia": AYER.isoformat(), "estado": "comido", "kcal": 3000.0, "pendiente_cantidad": False}]
    resumen = resumen_del_dia(apuntes, HOY, _config(tmp_path),
                              gasto_estimado=estimar_gasto(_config(tmp_path), None, [], None))
    assert resumen["total_kcal"] == 0
    assert [m["kcal"] for m in resumen["margenes"]] == [1800, 2000, 2200]
    assert resumen["barra"]["quedan_kcal"] == 2000


# -- R9: what still fits ---------------------------------------------------------------------------

def test_cuanto_me_cabe():  # R9
    cabe = gramos_que_caben(400, 360, 100)
    assert cabe["cantidad"] == 111 and "111" in cabe["texto"]


@pytest.mark.parametrize("margen", [0, -50])
def test_sin_margen_no_cabe(margen):  # R9
    cabe = gramos_que_caben(margen, 360, 100)
    assert cabe["cantidad"] == 0 and cabe["texto"] == "no te cabe hoy"


# -- R10: simulation over the day ------------------------------------------------------------------

def test_simular_una_lista_de_500(tmp_path):  # R10
    objetivo = {"tipo": None, "porcentaje": None, "niveles": [-20, -15], "entreno": None, "pasos": None}
    config = _config(tmp_path, objetivo)
    apuntes = [{"dia": HOY.isoformat(), "estado": "comido", "kcal": 1000.0, "pendiente_cantidad": False}]
    resumen = resumen_del_dia(apuntes, HOY, config, objetivo_dia=objetivo,
                              gasto_estimado=estimar_gasto(config, _stats(4000), [NATACION], None))
    despues = simular(resumen, 500)
    assert [(m["nivel"], m["kcal"]) for m in despues["margenes"]] == [(-20, 340), (-15, 455)]
    assert despues["barra"]["comido_kcal"] == 1500
    assert resumen["total_kcal"] == 1000  # the day itself is untouched


# -- R11: the bar --------------------------------------------------------------------------------

def test_barra_mantenimiento():  # R11
    b = barra(2400, 1800)
    assert b["pct"] == 75 and b["quedan_kcal"] == 600 and b["texto"] == "quedan 600 kcal"


def test_barra_deficit():  # R11
    b = barra(2000 * 0.8, 400)
    assert b["pct"] == 25 and b["quedan_kcal"] == 1200


def test_barra_pasada():  # R11
    b = barra(2400, 2600)
    assert b["pct"] == 108 and b["pasado_kcal"] == 200 and b["texto"] == "te has pasado 200 kcal"


def test_sin_plan_el_objetivo_es_mantenimiento_y_se_dice(tmp_path):  # R11
    resumen = resumen_del_dia([], HOY, _config(tmp_path), plan={"objetivo_pct": None, "motivo": None,
                                                                 "limite_saludable_pct": None})
    assert resumen["objetivo"]["porcentaje"] == 0 and resumen["objetivo"]["origen"] == "sin_plan"
    assert "mantenimiento" in resumen["objetivo"]["texto"]
    assert resumen["barra"]["objetivo_kcal"] == 2000


def test_el_objetivo_del_dia_manda_sobre_el_plan(tmp_path):  # R7, R11
    plan = {"objetivo_pct": -20, "motivo": "ajuste", "limite_saludable_pct": None}
    del_plan = resumen_del_dia([], HOY, _config(tmp_path), plan=plan)
    assert del_plan["objetivo"]["porcentaje"] == -20 and del_plan["barra"]["objetivo_kcal"] == 1600
    cambiado = resumen_del_dia([], HOY, _config(tmp_path), plan=plan,
                               objetivo_dia={"tipo": None, "porcentaje": 0, "niveles": None, "entreno": None,
                                             "pasos": None})
    assert cambiado["objetivo"]["porcentaje"] == 0 and cambiado["objetivo"]["origen"] == "dia"
    assert cambiado["barra"]["objetivo_kcal"] == 2000


# -- R12: the healthy limit ------------------------------------------------------------------------

def test_limite_en_deficit():  # R12
    lim = limite_saludable(2400, -20, 25)
    assert lim["kcal"] == 1800 and lim["superado"] is False and lim["aviso"] is None
    pasado = limite_saludable(2400, -30, 25)
    assert pasado["kcal"] == 1800 and pasado["superado"] is True and "límite" in pasado["aviso"]


def test_limite_en_superavit():  # R12
    lim = limite_saludable(2400, 10, 25)
    assert lim["kcal"] == 3000 and lim["superado"] is False
    assert limite_saludable(2400, 30, 25)["superado"] is True


def test_sin_limite_no_hay_marca():  # R12
    assert limite_saludable(2400, -30, None) is None
    assert barra(2400, 100)["limite"] is None


def test_la_barra_lleva_el_limite(tmp_path):  # R12
    resumen = resumen_del_dia([], HOY, _config(tmp_path), plan={"objetivo_pct": -30, "motivo": "ajuste",
                                                                 "limite_saludable_pct": 25})
    assert resumen["barra"]["limite"]["kcal"] == 1500 and resumen["barra"]["limite"]["superado"] is True


def test_margenes_siguen_igual():
    assert margenes(2300, 1000, (-20, -15)) == {-20: 840, -15: 955}
