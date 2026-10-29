"""Unit 042 · R1, R2, R3, R4, R6 (R-157..R-160, C-199..C-202): a session of the plan as a guided running workout of
Garmin (`plan/reloj.py`), without AI: its blocks of text («20' a 5:45», «4×12' a 4:00 (2' rec)», «10 km a 4:20»)
become the warm-up, the work, the repeats with their recovery and the cool-down; the end by time or by distance; the
target of pace as a range of speed (±5" per km over the pace of the block, in m/s); «suave» or «por esfuerzo»,
without a target. A block it cannot read is never sent half done: it says which one.

The same blocks go to a local FIT file (the plan B when Garmin fails, R-159), checked here with a small reader of
the FIT protocol written for the test. Sending replaces the one sent before (R-160), with Garmin doubled: no call to
Garmin ever leaves a test. Every session is invented.
"""
from __future__ import annotations

import struct
from datetime import date

import pytest

from tridata.garmin_client import GarminRateLimitError, GarminWorkoutError
from tridata.plan import reloj
from tridata.plan.reloj import GarminNoLlega, NoTraducible, a_garmin, fit_local, lineas, mandar

SERIES = {"id": 7, "fecha": "2026-10-28", "titulo": "Series 4×12'",
          "bloques": ["20' a 5:45", "4×12' a 4:00 (2' rec)", "10' suave"]}


def _rapido(ritmo_s_km: int) -> float:
    return 1000 / ritmo_s_km


class GarminDoble:
    """What `plan/reloj.py` uses of `GarminClient`, in memory: what is in the library and what is scheduled."""

    def __init__(self, *, falla_en: str | None = None, error: Exception | None = None) -> None:
        self.llamadas: list[tuple] = []
        self.entrenos: dict[int, dict] = {}
        self.programados: dict[int, tuple[int, date]] = {}
        self._siguiente = 100
        self.falla_en, self.error = falla_en, error

    def _quizas_falla(self, que: str) -> None:
        if self.falla_en == que:
            raise self.error or GarminWorkoutError("Garmin Connect did not answer")

    def subir_entreno(self, entreno: dict) -> int:
        self.llamadas.append(("subir_entreno", entreno["workoutName"]))
        self._quizas_falla("subir_entreno")
        self._siguiente += 1
        self.entrenos[self._siguiente] = entreno
        return self._siguiente

    def programar(self, workout_id: int, dia: date) -> int:
        self.llamadas.append(("programar", workout_id, dia))
        self._quizas_falla("programar")
        self._siguiente += 1
        self.programados[self._siguiente] = (workout_id, dia)
        return self._siguiente

    def quitar_programado(self, schedule_id: int) -> None:
        self.llamadas.append(("quitar_programado", schedule_id))
        self._quizas_falla("quitar_programado")
        self.programados.pop(schedule_id, None)

    def borrar_entreno(self, workout_id: int) -> None:
        self.llamadas.append(("borrar_entreno", workout_id))
        self._quizas_falla("borrar_entreno")
        self.entrenos.pop(workout_id, None)

    def del_dia(self, dia: date) -> list[dict]:
        """The workouts scheduled that day, as the calendar of Garmin Connect shows them."""
        return [self.entrenos[w] for w, d in self.programados.values() if d == dia and w in self.entrenos]


# -- R1, R6 · C-199: the blocks as a guided workout ---------------------------------------------------------------

def test_las_series_son_calentamiento_repeticion_y_vuelta_a_la_calma():  # R1 · C-199
    entreno = a_garmin(SERIES)
    assert entreno["workoutName"] == "Series 4×12'"
    assert entreno["sportType"]["sportTypeKey"] == "running" and entreno["sportType"]["sportTypeId"] == 1
    (segmento,) = entreno["workoutSegments"]
    assert segmento["sportType"]["sportTypeKey"] == "running"
    calentar, series, enfriar = segmento["workoutSteps"]

    assert calentar["type"] == "ExecutableStepDTO" and calentar["stepType"]["stepTypeKey"] == "warmup"
    assert calentar["endCondition"]["conditionTypeKey"] == "time" and calentar["endConditionValue"] == 1200
    assert calentar["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    # 5:45 ± 5": from 5:50 (slower, less m/s) to 5:40 (faster)
    assert calentar["targetValueOne"] == pytest.approx(_rapido(350))
    assert calentar["targetValueTwo"] == pytest.approx(_rapido(340))

    assert series["type"] == "RepeatGroupDTO" and series["numberOfIterations"] == 4
    assert series["stepType"]["stepTypeKey"] == "repeat"
    trabajo, recuperacion = series["workoutSteps"]
    assert trabajo["stepType"]["stepTypeKey"] == "interval" and trabajo["endConditionValue"] == 720
    assert trabajo["targetValueOne"] == pytest.approx(_rapido(245))  # 4:05
    assert trabajo["targetValueTwo"] == pytest.approx(_rapido(235))  # 3:55
    assert recuperacion["stepType"]["stepTypeKey"] == "recovery"
    assert recuperacion["endCondition"]["conditionTypeKey"] == "time" and recuperacion["endConditionValue"] == 120
    assert recuperacion["targetType"]["workoutTargetTypeKey"] == "no.target"

    assert enfriar["stepType"]["stepTypeKey"] == "cooldown" and enfriar["endConditionValue"] == 600
    assert enfriar["targetType"]["workoutTargetTypeKey"] == "no.target"  # «suave»: no target

    orden = [calentar["stepOrder"], series["stepOrder"], trabajo["stepOrder"], recuperacion["stepOrder"],
             enfriar["stepOrder"]]
    assert orden == [1, 2, 3, 4, 5]  # the steps of Garmin are numbered one after the other, the repeated inside
    assert entreno["estimatedDurationInSecs"] == 1200 + 4 * (720 + 120) + 600


def test_por_distancia_y_por_tiempo_en_horas():  # R1 · C-199
    sesion = {"id": 8, "fecha": "2026-10-31", "titulo": "Tirada de prueba",
              "bloques": ["1h30 a 5:30", "10 km a 4:20", "3×1000 a 4:05 (90\" rec)", "2,5 km por esfuerzo"]}
    rodaje, ritmo, series, esfuerzo = a_garmin(sesion)["workoutSegments"][0]["workoutSteps"]
    assert rodaje["stepType"]["stepTypeKey"] == "warmup" and rodaje["endConditionValue"] == 5400
    assert ritmo["stepType"]["stepTypeKey"] == "interval"
    assert ritmo["endCondition"]["conditionTypeKey"] == "distance" and ritmo["endConditionValue"] == 10000
    assert ritmo["targetValueOne"] == pytest.approx(_rapido(265))  # 4:25
    assert ritmo["targetValueTwo"] == pytest.approx(_rapido(255))  # 4:15
    assert series["numberOfIterations"] == 3
    trabajo, recuperacion = series["workoutSteps"]
    assert trabajo["endCondition"]["conditionTypeKey"] == "distance" and trabajo["endConditionValue"] == 1000
    assert recuperacion["endConditionValue"] == 90
    assert esfuerzo["stepType"]["stepTypeKey"] == "cooldown"
    assert esfuerzo["endCondition"]["conditionTypeKey"] == "distance" and esfuerzo["endConditionValue"] == 2500
    assert esfuerzo["targetType"]["workoutTargetTypeKey"] == "no.target"  # «por esfuerzo»: no target


def test_una_sola_linea_es_un_bloque_de_trabajo():  # R1
    (bloque,) = a_garmin({"id": 9, "fecha": "2026-10-29", "titulo": "Rodaje", "bloques": ["40' a 5:40"]}
                         )["workoutSegments"][0]["workoutSteps"]
    assert bloque["stepType"]["stepTypeKey"] == "interval" and bloque["endConditionValue"] == 2400


@pytest.mark.parametrize("raro", ["texto raro", "geles min 45", "20' a 5:30 con cambios de ritmo", "4×12' a 4:00 (rec)"])
def test_un_bloque_que_no_sabe_leer_no_se_manda_a_medias(raro):  # R6
    sesion = {**SERIES, "bloques": ["20' a 5:45", raro, "10' suave"]}
    with pytest.raises(NoTraducible) as exc:
        a_garmin(sesion)
    assert f"«{raro}»" in str(exc.value)  # it says which block
    with pytest.raises(NoTraducible):
        fit_local(sesion)
    with pytest.raises(NoTraducible):
        lineas(sesion)


def test_sin_bloques_o_que_no_es_carrera_no_se_manda():  # R6, out of scope: bike and swimming
    with pytest.raises(NoTraducible):
        a_garmin({**SERIES, "bloques": []})
    with pytest.raises(NoTraducible) as exc:
        a_garmin({**SERIES, "titulo": "Rodillo de bici", "bloques": ["1h a 30 km/h"]})
    assert "carrera" in str(exc.value)


@pytest.mark.parametrize("guion", ["-", "–", "—", " – "])
def test_un_rango_de_ritmo_con_guion_largo_se_lee_igual(guion):  # bug 050 · R1, R2
    sesion = {"id": 10, "fecha": "2026-10-28", "titulo": "Tempo 2×15'",
              "bloques": [f"15' a 5:00{guion}6:00", f"2×15' a 4:00{guion}4:15 (3' rec)"]}
    calentar, series = a_garmin(sesion)["workoutSegments"][0]["workoutSteps"]
    assert calentar["targetValueOne"] == pytest.approx(_rapido(360))  # 6:00, the slowest
    assert calentar["targetValueTwo"] == pytest.approx(_rapido(300))  # 5:00, the fastest
    trabajo, _ = series["workoutSteps"]
    assert trabajo["targetValueOne"] == pytest.approx(_rapido(255))
    assert trabajo["targetValueTwo"] == pytest.approx(_rapido(240))
    assert lineas(sesion) == ["Calentamiento: 15' a 5:00-6:00 /km",
                              "2 × 15' a 4:00-4:15 /km, con 3' de recuperación"]
    assert fit_local(sesion)


def test_las_lineas_de_la_tarjeta_dicen_el_margen_de_ritmo():  # R1 · C-199
    assert lineas(SERIES) == [
        "Calentamiento: 20' a 5:40-5:50 /km",
        "4 × 12' a 3:55-4:05 /km, con 2' de recuperación",
        "Vuelta a la calma: 10' sin ritmo",
    ]


# -- R3 · C-201: the same session as a FIT file --------------------------------------------------------------------

def _crc(datos: bytes) -> int:
    tabla = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
    crc = 0
    for byte in datos:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = tabla[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc = crc ^ tmp ^ tabla[nibble]
    return crc


def _leer_fit(datos: bytes) -> list[tuple[int, dict[int, object]]]:
    """[(global message number, {field: value})] of a FIT file (normal headers, little endian, no developer data)."""
    assert datos[0] == 14 and datos[8:12] == b".FIT"
    assert struct.unpack_from("<H", datos, 12)[0] == _crc(datos[:12])  # the CRC of the header
    tamano = struct.unpack_from("<I", datos, 4)[0]
    assert len(datos) == 14 + tamano + 2
    assert _crc(datos) == 0  # the CRC of the whole file, its last two bytes included
    formatos = {0x00: "B", 0x02: "B", 0x84: "H", 0x86: "I", 0x8C: "I"}
    definiciones: dict[int, tuple[int, list[tuple[int, int, int]]]] = {}
    mensajes = []
    i = 14
    while i < 14 + tamano:
        cabecera = datos[i]
        i += 1
        local = cabecera & 0x0F
        if cabecera & 0x40:
            assert datos[i + 1] == 0  # little endian
            global_, n = struct.unpack_from("<HB", datos, i + 2)
            campos = [tuple(datos[i + 5 + 3 * k: i + 8 + 3 * k]) for k in range(n)]
            definiciones[local] = (global_, campos)  # type: ignore[assignment]
            i += 5 + 3 * n
            continue
        global_, campos = definiciones[local]
        valores: dict[int, object] = {}
        for numero, tam, base in campos:
            crudo = datos[i: i + tam]
            i += tam
            valores[numero] = (crudo.split(b"\0")[0].decode() if base == 0x07
                               else struct.unpack("<" + formatos[base], crudo)[0])
        mensajes.append((global_, valores))
    return mensajes


def test_el_fichero_lleva_los_mismos_bloques():  # R3 · C-201
    mensajes = _leer_fit(fit_local(SERIES))
    (fichero,) = [v for g, v in mensajes if g == 0]
    assert fichero[0] == 5  # file_id.type: workout
    (entreno,) = [v for g, v in mensajes if g == 26]
    assert entreno[4] == 1 and entreno[8] == "Series 4×12'"  # sport running, its name
    pasos = [v for g, v in mensajes if g == 27]
    assert entreno[6] == len(pasos) == 5
    assert [p[254] for p in pasos] == [0, 1, 2, 3, 4]  # message_index
    calentar, trabajo, recuperacion, repetir, enfriar = pasos
    assert (calentar[7], calentar[1], calentar[2], calentar[3]) == (2, 0, 1_200_000, 0)  # warmup, time in ms, speed
    assert (calentar[5], calentar[6]) == (round(_rapido(350) * 1000), round(_rapido(340) * 1000))  # in mm/s
    assert (trabajo[7], trabajo[2], trabajo[5], trabajo[6]) == (0, 720_000, round(_rapido(245) * 1000),
                                                                round(_rapido(235) * 1000))
    assert (recuperacion[7], recuperacion[2], recuperacion[3]) == (4, 120_000, 2)  # recovery, open target
    assert (repetir[1], repetir[2], repetir[4]) == (6, 1, 4)  # repeat from step 1, four times
    assert (enfriar[7], enfriar[2], enfriar[3]) == (3, 600_000, 2)  # cooldown, open target


def test_el_fichero_por_distancia_va_en_centimetros():  # R3
    sesion = {"id": 8, "fecha": "2026-10-31", "titulo": "Ritmo", "bloques": ["10 km a 4:20"]}
    (paso,) = [v for g, v in _leer_fit(fit_local(sesion)) if g == 27]
    assert (paso[1], paso[2]) == (1, 1_000_000)


# -- R2, R4 · C-200, C-202: sending, and sending again ------------------------------------------------------------

def test_mandar_sube_y_programa_en_su_dia():  # R2 · C-200
    garmin = GarminDoble()
    workout_id, schedule_id = mandar(SERIES, lambda: garmin)
    assert garmin.llamadas == [("subir_entreno", "Series 4×12'"), ("programar", workout_id, date(2026, 10, 28))]
    assert garmin.programados[schedule_id] == (workout_id, date(2026, 10, 28))
    assert garmin.entrenos[workout_id] == a_garmin(SERIES)


def test_mandarla_otra_vez_sustituye_la_anterior():  # R4 · C-202
    garmin = GarminDoble()
    viejo, viejo_programado = mandar(SERIES, lambda: garmin)
    olvidadas = []
    tres = {**SERIES, "titulo": "Series 3×10'", "bloques": ["20' a 5:45", "3×10' a 4:00 (2' rec)", "10' suave"],
            "garmin_workout_id": viejo, "garmin_schedule_id": viejo_programado}
    nuevo, _ = mandar(tres, lambda: garmin, al_quitar=lambda: olvidadas.append(True))
    assert garmin.llamadas[2:4] == [("quitar_programado", viejo_programado), ("borrar_entreno", viejo)]
    assert [e["workoutName"] for e in garmin.del_dia(date(2026, 10, 28))] == ["Series 3×10'"]  # one, the new one
    assert viejo not in garmin.entrenos and nuevo in garmin.entrenos
    assert olvidadas == [True]  # the old ids are forgotten as soon as Garmin no longer has it


@pytest.mark.parametrize(("error", "dice"), [
    (GarminRateLimitError("429"), "limitando"),
    (GarminWorkoutError("no answer"), "no la ha aceptado o no responde"),
    (ConnectionError("sin red"), "no la ha aceptado o no responde"),
], ids=["429", "rechazo", "sin-conexion"])
def test_si_garmin_falla_lo_dice(error, dice):  # R3 · C-201
    garmin = GarminDoble(falla_en="subir_entreno", error=error)
    with pytest.raises(GarminNoLlega) as exc:
        mandar(SERIES, lambda: garmin)
    assert dice in str(exc.value)
    assert garmin.entrenos == {} and garmin.programados == {}


def test_si_falla_al_programar_no_deja_el_entreno_suelto():  # R3, R4: nothing half sent
    garmin = GarminDoble(falla_en="programar")
    with pytest.raises(GarminNoLlega):
        mandar(SERIES, lambda: garmin)
    assert garmin.llamadas[-1][0] == "borrar_entreno"
    assert garmin.entrenos == {} and garmin.programados == {}


def test_un_bloque_raro_no_llama_a_garmin():  # R6
    garmin = GarminDoble()
    with pytest.raises(NoTraducible):
        mandar({**SERIES, "bloques": ["texto raro"]}, lambda: garmin)
    assert garmin.llamadas == []


def test_sin_credenciales_no_entra():  # R3: without GARMIN_EMAIL / GARMIN_PASSWORD it says so
    with pytest.raises(GarminNoLlega) as exc:
        reloj.mandar(SERIES, lambda: reloj.cliente_garmin({}))
    assert "Garmin" in str(exc.value)
