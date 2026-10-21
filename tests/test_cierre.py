"""Close the day in two steps (unit 014): the food, and then, with Garmin, the expenditure; and the
kcal per step calibrated with the days closed with Garmin.

Plans: `cerrar-dia` R-24..R-28 (C-24..C-28, C-79, C-80, C-81), rule G-25 and the states of «Día de
comida»; `sincronizar-dia` (the sync revalidates the recent days, the close only reads what it brought).
Pure rules first, then `Cierres` over a temporary SQLite with an invented `DataStore`. Garmin is never
called: its rows are written straight into the temporary store. Every value is invented.

Settings of every case unless said: hand-written basal 1600 kcal and no weight saved (so `basal_del_dia`
gives 1600), 0.04 kcal/step, 10,000 steps by default and no plan. «Today» is Wednesday 2026-10-21.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from tridata.models import Activity, DailyStats
from tridata.nutricion import (
    DESDE_SIEMPRE, Ajustes, Alimento, Cierres, ObjetivosDia, RegistroComidas, RegistroPeso, balance, calibrar,
    es_relevante, garmin_incompleto, gasto_estimado_del_dia, media_semana,
)
from tridata.storage import DataStore

HOY = date(2026, 10, 21)  # a Wednesday
MARTES = date(2026, 10, 20)
LUNES = date(2026, 10, 19)
TEXTO_ESPERA = "Garmin aún no tiene el día completo: sigue con el gasto estimado"


def _actividad(id_, dia, tipo, kcal, minutos, cadencia=None, nombre=None):
    return Activity(activity_id=id_, activity_date=dia, activity_type=tipo, name=nombre or tipo.capitalize(),
                    duration_seconds=minutos * 60, calories=kcal, avg_cadence=cadencia)


def _carrera(dia, id_="c1"):
    """400 kcal, 30 min at 170 spm: 5,100 running steps estimated."""
    return _actividad(id_, dia, "running", 400, 30, cadencia=170, nombre="Carrera")


def _natacion(dia, id_="n1", kcal=300):
    return _actividad(id_, dia, "lap_swimming", kcal, 45, nombre="Natación")


class Mundo:
    """Every repository of the app over one temporary SQLite, and `Cierres` wired to them."""

    def __init__(self, tmp_path, **extra):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000, **extra},
                             desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.store = DataStore(self.db)
        self.cierres = Cierres(self.db, ajustes=self.ajustes, pesos=self.pesos, objetivos=self.objetivos,
                               store=self.store)

    def comer(self, dia, nombre, kcal, *, planeado=False):
        """A food of `dia`: eaten, or planned the day before for that day."""
        hoy = dia - timedelta(days=1) if planeado else dia
        alimento = Alimento(nombre=nombre, dia=dia, cantidad=1, unidad="ración", kcal=kcal)
        return self.registro.guardar([alimento], hoy=hoy, texto=nombre, sin_cambios=True)[0]

    def garmin(self, dia, total, pasos=10000, actividades=()):
        self.store.save_daily_stats(DailyStats(stat_date=dia, steps=pasos, total_calories=total))
        if actividades:
            self.store.save_activities(list(actividades))

    def estimado(self, dia):
        return gasto_estimado_del_dia(dia, self.ajustes, self.pesos, self.objetivos, self.store)["kcal"]

    def filas(self, tabla):
        with sqlite3.connect(self.db) as conn:
            return [tuple(f) for f in conn.execute(f"SELECT * FROM {tabla} ORDER BY 1")]  # noqa: S608 - fixed names


@pytest.fixture
def m(tmp_path):
    return Mundo(tmp_path)


# -- pure rules -------------------------------------------------------------------------------------

@pytest.mark.parametrize(("total", "actividades", "motivo"), [
    (1450, [], "basal"),  # below the basal of 1600
    (1700, [_actividad("b1", MARTES, "cycling", 1900, 180, nombre="Bici")], "ejercicio"),  # exercise alone is more (G-10)
    (None, [], "total"),  # no total at all
])
def test_dia_incompleto_por_senales(total, actividades, motivo):  # R4 · R-26, C-26, G-10
    stats = DailyStats(stat_date=MARTES, steps=10000, total_calories=total).to_dict()
    texto = garmin_incompleto(stats, [a.to_dict() for a in actividades], 1600)
    assert texto is not None and motivo in texto


def test_dia_sin_fila_de_garmin_esta_incompleto():  # R4 · R-26
    assert garmin_incompleto(None, [], 1600) is not None


def test_dia_completo_no_tiene_motivo():  # R4 · R-26, C-26
    stats = DailyStats(stat_date=MARTES, steps=10000, total_calories=2550).to_dict()
    bici = _actividad("b1", MARTES, "cycling", 1900, 180).to_dict()
    assert garmin_incompleto(stats, [bici], 1600) is None


@pytest.mark.parametrize(("antes", "despues", "comido", "limite", "relevante"), [
    (2500, 2550, 2000, None, False),  # −500 → −550: small, same sign, no limit
    (2500, 2800, 2000, None, True),  # more than 200 kcal
    (2500, 2700, 2000, None, False),  # exactly 200 is not «more than 200»
    (2100, 2150, 2120, None, True),  # +20 → −30: the balance changes sign (C-81)
    (2500, 2560, 1900, 25, True),  # −24 % → −25,8 %: crosses the healthy limit (C-81)
    (2500, 2560, 1900, None, False),  # the same without a limit
])
def test_relevancia_de_una_correccion(antes, despues, comido, limite, relevante):  # R5 · R-27, G-25, C-27, C-81
    assert es_relevante(antes, despues, comido, limite) is relevante


def test_balance_y_su_porcentaje():  # R1, R3 · R-24, R-25
    assert balance(2000, 2300) == {"kcal": -300, "pct": -13.0}
    assert balance(2000, 2550) == {"kcal": -550, "pct": -21.6}
    assert balance(2120, 2100) == {"kcal": 20, "pct": 1.0}


def test_media_de_la_semana():  # R1 · R-24, C-24
    media = media_semana([{"comido": 2000, "gasto": 2500}, {"comido": 2000, "gasto": 2300}])
    assert media == {"dias": 2, "balance_medio_kcal": -400, "balance_pct": -16.7}
    assert media_semana([]) == {"dias": 0, "balance_medio_kcal": None, "balance_pct": None}


def _dia_cal(i, real, pasos=10000, basal=1600, actividades=300):
    return {"dia": date(2026, 10, 1) + timedelta(days=i), "real": real, "basal": basal,
            "actividades_kcal": actividades, "pasos_sin_carrera": pasos}


def test_calibracion_con_tres_dias():  # R6 · R-28, C-28
    assert calibrar([_dia_cal(i, 2450) for i in range(3)]) == 0.055


def test_calibracion_con_dos_dias_no_calibra():  # R6 · R-28, C-80
    assert calibrar([_dia_cal(i, 2450) for i in range(2)]) is None


def test_calibracion_sin_los_dias_de_pasos_incompletos_o_pocos():  # R6 · R-28, C-80
    dias = [_dia_cal(0, 2450), _dia_cal(1, 2450), _dia_cal(2, 2450, pasos=None)]
    assert calibrar(dias) is None
    dias = [_dia_cal(0, 2450), _dia_cal(1, 2450), _dia_cal(2, 1650, pasos=999)]
    assert calibrar(dias) is None


def test_calibracion_con_los_ultimos_14_que_valen():  # R6 · R-28
    viejo = _dia_cal(0, 4900)  # would move the result if it counted
    assert calibrar([viejo] + [_dia_cal(i, 2450) for i in range(1, 15)]) == 0.055


def test_calibracion_fuera_de_rango_da_el_valor_para_descartarlo():  # R7 · R-28, C-80
    assert calibrar([_dia_cal(i, 4900) for i in range(3)]) == 0.3


# -- closing the food -------------------------------------------------------------------------------

def test_cerrar_la_comida_da_lo_planeado_por_comido(m):  # R1 · R-24, C-24
    m.comer(HOY, "Comida", 1400)
    cena = m.comer(HOY, "Cena", 600, planeado=True)
    assert cena["estado"] == "planeado"

    assert m.cierres.cerrar_comida(HOY, "boton") is True

    estados = {a["nombre"]: a["estado"] for a in m.registro.apuntes_del_dia(HOY)}
    assert estados == {"Comida": "comido", "Cena": "comido"}
    cierre = m.cierres.del_dia(HOY)
    assert cierre["estado"] == "comida_cerrada" and cierre["motivo_cierre"] == "boton"
    bloque = m.cierres.bloque_del_dia(HOY, 2000, 2300)
    assert bloque["balance_kcal"] == -300 and bloque["balance_pct"] == -13.0
    assert bloque["texto"] == "Comida cerrada · gasto estimado"
    assert "balance provisional −300 kcal (−13 %), con el gasto estimado" in bloque["balance_texto"]


def test_cerrar_planeados_en_la_misma_transaccion(m):  # R1 · R-24
    m.comer(MARTES, "Merienda", 200, planeado=True)
    m.comer(MARTES, "Fruta", 80, planeado=True)
    assert m.registro.cerrar_planeados(MARTES) == 2
    assert {a["estado"] for a in m.registro.apuntes_del_dia(MARTES)} == {"comido"}
    assert m.registro.cerrar_planeados(MARTES) == 0


def test_un_dia_sin_nada_apuntado_no_se_cierra(m):  # R2 · R-24, C-79
    with pytest.raises(ValueError):
        m.cierres.cerrar_comida(LUNES, "boton")
    assert m.cierres.del_dia(LUNES) is None


def test_cerrar_dos_veces_no_cambia_nada(m):  # R2 · R-24
    m.comer(HOY, "Comida", 1400)
    assert m.cierres.cerrar_comida(HOY, "boton") is True
    antes = m.cierres.del_dia(HOY)
    assert m.cierres.cerrar_comida(HOY, "boton") is False
    assert m.cierres.del_dia(HOY) == antes


def test_al_empezar_el_dia_siguiente_se_cierra_sola(m):  # R2 · R-24, C-79
    m.comer(MARTES, "Comida", 1500)
    m.comer(MARTES, "Merienda", 200, planeado=True)
    m.comer(HOY, "Desayuno", 300)  # today stays open

    m.cierres.al_dia(HOY)

    martes = m.cierres.del_dia(MARTES)
    assert martes["estado"] == "comida_cerrada" and martes["motivo_cierre"] == "dia_siguiente"
    assert {a["nombre"]: a["estado"] for a in m.registro.apuntes_del_dia(MARTES)}["Merienda"] == "comido"
    assert m.cierres.del_dia(LUNES) is None  # nothing logged: not closed
    assert m.cierres.del_dia(HOY) is None
    filas = {t: m.filas(t) for t in ("cierre_dia", "cierre_rastro", "calibracion", "alimento_apuntado")}

    m.cierres.al_dia(HOY)  # twice changes nothing

    assert {t: m.filas(t) for t in filas} == filas


# -- closing with Garmin ----------------------------------------------------------------------------

def _martes_de_r3(m):
    """Food closed in 2,000; estimated 2,400 (10,000 steps without running + a 400 kcal run)."""
    m.comer(MARTES, "Comida", 2000)
    m.cierres.cerrar_comida(MARTES, "boton")
    m.store.save_activities([_carrera(MARTES)])
    assert m.estimado(MARTES) == 2400


def test_cierre_con_garmin_sustituye_el_estimado(m):  # R3 · R-25, C-25 (criterio portante)
    _martes_de_r3(m)
    m.garmin(MARTES, 2550, pasos=15100)

    m.cierres.al_dia(HOY)

    cierre = m.cierres.del_dia(MARTES)
    assert cierre["estado"] == "cerrado_garmin"
    assert cierre["gasto_real"] == 2550 and cierre["gasto_estimado"] == 2400
    assert cierre["cerrado_garmin_en"] is not None
    bloque = m.cierres.bloque_del_dia(MARTES, 2000, 2400)
    assert bloque["estado"] == "cerrado_garmin"
    assert bloque["texto"] == "Cerrado con Garmin: gasto real 2.550 (estimado 2.400)"
    assert bloque["gasto_real_kcal"] == 2550 and bloque["gasto_estimado_kcal"] == 2400
    assert (bloque["balance_kcal"], bloque["balance_pct"]) == (-550, -21.6)


def test_un_olvido_recalcula_sin_cambiar_el_estado(m):  # R3 · R-25, C-25
    _martes_de_r3(m)
    m.garmin(MARTES, 2550, pasos=15100)
    m.cierres.al_dia(HOY)
    m.registro.anadir(Alimento(nombre="Yogur olvidado", dia=MARTES, cantidad=1, unidad="ud", kcal=150), hoy=HOY)
    comido = sum(a["kcal"] for a in m.registro.apuntes_del_dia(MARTES))

    bloque = m.cierres.bloque_del_dia(MARTES, comido, 2400)

    assert bloque["estado"] == "cerrado_garmin" and bloque["balance_kcal"] == -400


@pytest.mark.parametrize("incompleto", ["bajo_basal", "bici", "sin_total"])
def test_dia_incompleto_espera_y_se_cierra_cuando_llega(m, incompleto):  # R4 · R-26, C-26
    m.comer(MARTES, "Comida", 2000)
    if incompleto == "bajo_basal":
        m.garmin(MARTES, 1450)
    elif incompleto == "bici":
        m.garmin(MARTES, 1700, actividades=[_actividad("b1", MARTES, "cycling", 1900, 180, nombre="Bici")])
    else:
        m.garmin(MARTES, None)

    m.cierres.al_dia(HOY)

    cierre = m.cierres.del_dia(MARTES)
    assert cierre["estado"] == "comida_cerrada" and cierre["gasto_real"] is None
    assert cierre["espera_motivo"]
    bloque = m.cierres.bloque_del_dia(MARTES, 2000, m.estimado(MARTES))
    assert bloque["texto"] == TEXTO_ESPERA and bloque["espera_motivo"]

    m.garmin(MARTES, 2550)  # a later sync brings the whole day
    m.cierres.al_dia(HOY)

    cierre = m.cierres.del_dia(MARTES)
    assert cierre["estado"] == "cerrado_garmin" and cierre["gasto_real"] == 2550
    assert cierre["espera_motivo"] is None


def test_hoy_no_se_cierra_con_garmin(m):  # R3 · R-25: today's total is never the whole day
    m.comer(HOY, "Comida", 2000)
    m.cierres.cerrar_comida(HOY, "boton")
    m.garmin(HOY, 2600)
    m.cierres.al_dia(HOY)
    assert m.cierres.del_dia(HOY)["estado"] == "comida_cerrada"


# -- corrections ------------------------------------------------------------------------------------

def _dos_dias_cerrados(m):
    for dia in (date(2026, 10, 13), date(2026, 10, 14)):
        m.comer(dia, "Comida", 2000)
        m.garmin(dia, 2500)
    m.cierres.al_dia(HOY)
    assert m.cierres.avisos_pendientes() == []  # the first close warns of nothing


def test_correcciones_con_rastro_y_aviso_solo_si_relevante(m):  # R5 · R-27, C-27, G-25
    _dos_dias_cerrados(m)
    m.garmin(date(2026, 10, 13), 2550)
    m.garmin(date(2026, 10, 14), 2800)

    m.cierres.al_dia(HOY)

    assert m.cierres.del_dia(date(2026, 10, 13))["gasto_real"] == 2550
    assert m.cierres.del_dia(date(2026, 10, 14))["gasto_real"] == 2800
    trece = m.cierres.bloque_del_dia(date(2026, 10, 13), 2000, 2000)
    assert [(r["gasto_antes"], r["gasto_despues"], r["balance_antes"], r["balance_despues"], r["relevante"])
            for r in trece["rastro"]] == [(2500, 2550, -500, -550, False)]
    avisos = m.cierres.avisos_pendientes()
    assert len(avisos) == 1
    aviso = avisos[0]
    assert aviso["dia"] == "2026-10-14"
    assert aviso["texto"] == "Garmin ha corregido el gasto del 2026-10-14: 2.500 → 2.800 kcal; balance −500 → −800"
    assert (aviso["gasto_antes"], aviso["gasto_despues"], aviso["balance_antes"], aviso["balance_despues"]) == \
        (2500, 2800, -500, -800)

    m.cierres.marcar_visto(aviso["id"])

    assert m.cierres.avisos_pendientes() == []
    assert len(m.cierres.bloque_del_dia(date(2026, 10, 14), 2000, 2000)["rastro"]) == 1  # seen, not deleted
    rastro = m.filas("cierre_rastro")
    m.cierres.al_dia(HOY)  # no change: nothing
    assert m.filas("cierre_rastro") == rastro


def test_avisan_el_cambio_de_signo_y_el_cruce_del_limite(tmp_path):  # R5 · R-27, C-81
    m = Mundo(tmp_path, limite_saludable_pct=25)
    signo, limite = date(2026, 10, 13), date(2026, 10, 14)
    m.comer(signo, "Comida", 2120)
    m.garmin(signo, 2100)
    m.comer(limite, "Comida", 1900)
    m.garmin(limite, 2500)
    m.cierres.al_dia(HOY)
    m.garmin(signo, 2150)
    m.garmin(limite, 2560)

    m.cierres.al_dia(HOY)

    assert sorted(a["dia"] for a in m.cierres.avisos_pendientes()) == ["2026-10-13", "2026-10-14"]


def test_un_total_incompleto_de_un_dia_cerrado_se_ignora(m):  # R5 · R-26, G-25
    _dos_dias_cerrados(m)
    m.garmin(date(2026, 10, 13), 1000)  # below the basal: incomplete again
    m.cierres.al_dia(HOY)
    assert m.cierres.del_dia(date(2026, 10, 13))["gasto_real"] == 2500
    assert m.filas("cierre_rastro") == []


def test_marcar_visto_un_aviso_que_no_existe(m):  # R5
    with pytest.raises(KeyError):
        m.cierres.marcar_visto(99)


# -- calibration ------------------------------------------------------------------------------------

TRES_DIAS = (date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18))


def _tres_dias(m, real, *, incompleto=None):
    for i, dia in enumerate(TRES_DIAS):
        m.comer(dia, "Comida", 2000)
        if dia == incompleto:  # the run alone estimates more than the step counter
            m.garmin(dia, real, pasos=3000, actividades=[_carrera(dia, f"c{i}")])
        else:
            m.garmin(dia, real, actividades=[_natacion(dia, f"n{i}")])


def test_calibracion_guardada_con_vigencia(m):  # R6 · R-28, C-28
    _tres_dias(m, 2450)
    assert m.estimado(TRES_DIAS[0]) == 2300

    m.cierres.al_dia(HOY)

    assert m.ajustes.vigentes(HOY).kcal_por_paso == 0.055
    assert m.ajustes.vigentes(MARTES).kcal_por_paso == 0.04  # the days before do not change
    assert m.ajustes.vigentes(HOY).desde["kcal_por_paso"] == HOY
    assert m.estimado(HOY) == 2150  # 10,000 steps and no training: 2,000 → 2,150
    assert m.estimado(TRES_DIAS[0]) == 2300  # still with 0.04
    cal = m.cierres.ultima_calibracion(HOY)
    assert cal["fecha"] == HOY.isoformat() and cal["kcal_por_paso"] == 0.055
    assert cal["dias"] == 3 and cal["diferencia_media"] == 150 and cal["descartada_motivo"] is None
    assert len(m.filas("calibracion")) == 1

    m.cierres.al_dia(HOY)  # nothing new: no new calibration
    assert len(m.filas("calibracion")) == 1
    assert len(m.ajustes.historial("kcal_por_paso")) == 2


def test_con_un_dia_de_pasos_incompletos_no_calibra(m):  # R6 · R-28, C-80
    _tres_dias(m, 2450, incompleto=TRES_DIAS[2])
    m.cierres.al_dia(HOY)
    assert m.cierres.del_dia(TRES_DIAS[2])["estado"] == "cerrado_garmin"  # closed, but it does not count
    assert m.ajustes.vigentes(HOY).kcal_por_paso == 0.04
    assert len(m.ajustes.historial("kcal_por_paso")) == 1


def test_con_dos_dias_no_calibra(m):  # R6 · R-28, C-80
    for i, dia in enumerate(TRES_DIAS[:2]):
        m.comer(dia, "Comida", 2000)
        m.garmin(dia, 2450, actividades=[_natacion(dia, f"n{i}")])
    m.cierres.al_dia(HOY)
    assert m.ajustes.vigentes(HOY).kcal_por_paso == 0.04
    assert m.cierres.ultima_calibracion(HOY) is None


def test_calibracion_fuera_de_rango_no_se_guarda(m):  # R7 · R-28, C-80
    _tres_dias(m, 4900)
    m.cierres.al_dia(HOY)
    assert m.ajustes.vigentes(HOY).kcal_por_paso == 0.04
    assert len(m.ajustes.historial("kcal_por_paso")) == 1
    cal = m.cierres.ultima_calibracion(HOY)
    assert cal["descartada_motivo"] and "fuera de rango" in cal["texto"]


# -- the week ---------------------------------------------------------------------------------------

def test_media_de_la_semana_de_lunes_a_domingo(m):  # R1 · R-24, C-24
    m.comer(LUNES, "Comida", 2000)
    m.garmin(LUNES, 2500)
    m.comer(date(2026, 10, 18), "Domingo de la semana anterior", 3000)  # another week: does not count
    m.cierres.al_dia(HOY)
    m.comer(HOY, "Comida", 2000)
    m.cierres.cerrar_comida(HOY, "boton")

    semana = m.cierres.semana(HOY, 2000, 2300)

    assert (semana["desde"], semana["hasta"]) == ("2026-10-19", "2026-10-25")
    assert semana["dias"] == 2
    assert semana["balance_medio_kcal"] == -400 and semana["balance_pct"] == -16.7
    assert semana["texto"] == "media de la semana −400 kcal/día (−16,7 %), 2 días"
