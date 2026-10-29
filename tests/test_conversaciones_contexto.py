"""Unit 032 · R3, R5 (R-114, R-115, G-46, C-155, C-156) — the criterio portante: every conversation sees ALL the
context, the same, and none sees what was said in another one (S-41).

The context is built on this PC (`conversaciones/contexto.py`): the fixed instructions with the catalogue of
actions, the medical transcription and the data in JSON (the last 8 weeks day by day, the earlier ones by week, the
load with the HR of Ajustes), without the hour. The line «Ve tus …, hasta hoy 9:42» goes apart. Every datum is
invented: foods, weights, activities, sleep, HR, HRV and a medical transcription made from a PDF written by the test
and «read» by a double.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from test_conversaciones import VALOR_INVENTADO, PdfFalso, pdf_sintetico
from test_plan import plan_de_prueba
from tridata.conversaciones.contexto import Contexto, linea, montar
from tridata.conversaciones.medicos import Medicos, TranscriptorPDF
from tridata.models import Activity, DailyStats, HRVRecord, SleepRecord
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas, RegistroPeso
from tridata.nutricion.asistente import texto_acciones
from tridata.plan import Planes
from tridata.storage import DataStore

HOY = date(2026, 10, 24)  # a Saturday
AHORA = datetime(2026, 10, 24, 9, 42)
PULSO = {"fc_max": 186, "fc_reposo": 46, "zonas_fc": [[46, 120], [121, 140], [141, 155], [156, 170], [171, 186]],
         "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}


class Mundo:
    """A temporary SQLite with every kind of datum Andrés has, all invented."""

    def __init__(self, tmp_path, *, con_medicos: bool = True) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1700, "kcal_por_paso": 0.04, "pasos_por_defecto": 9000,
                              "objetivo_pct": -10, **PULSO}, desde=DESDE_SIEMPRE)
        self.ajustes.anadir_carrera("10K de prueba", "2026-12-03", 1)
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.store = DataStore(self.db)
        self.comer("tostada de prueba", HOY, 210)
        self.comer("lentejas de prueba", HOY - timedelta(days=10), 480)
        self.comer("comida antigua de prueba", HOY - timedelta(days=100), 2222)
        self.pesos.apuntar(HOY, 71.9, hoy=HOY)
        self.pesos.apuntar(HOY - timedelta(days=70), 72.6, hoy=HOY)
        self.store.save_activities([
            Activity(activity_id="a1", activity_date=HOY - timedelta(days=3), activity_type="running",
                     name="Carrera de prueba al río", duration_seconds=2700, distance_meters=10000, avg_hr=151,
                     calories=640),
            Activity(activity_id="a0", activity_date=HOY - timedelta(days=90), activity_type="cycling",
                     name="Bici antigua de prueba", duration_seconds=5400, distance_meters=45000, avg_hr=132),
        ])
        self.store.save_daily_stats(DailyStats(stat_date=HOY, steps=8123, resting_hr=47, total_calories=2650))
        self.store.save_sleep(SleepRecord(sleep_date=HOY, total_sleep_seconds=26100, sleep_score=81))
        self.store.save_hrv(HRVRecord(hrv_date=HOY, last_night_avg=63))
        carpeta = tmp_path / "medicos"
        carpeta.mkdir()
        if con_medicos:
            (carpeta / "analitica-de-prueba.pdf").write_bytes(pdf_sintetico("analitica inventada"))
            (carpeta / "ergometria-de-prueba.pdf").write_bytes(pdf_sintetico("ergometria inventada"))
        self.medicos = Medicos(self.db, carpeta if con_medicos else None, TranscriptorPDF(cliente=PdfFalso()))

    def comer(self, nombre: str, dia: date, kcal: float) -> None:
        self.registro.guardar([Alimento(nombre=nombre, dia=dia, cantidad=100, unidad="g", kcal=kcal)],
                              hoy=dia, texto=nombre, sin_cambios=True)

    def contexto(self, hoy: date = HOY, **extra) -> Contexto:
        estado = self.medicos.al_dia()
        return montar(registro=self.registro, pesos=self.pesos, ajustes=self.ajustes, store=self.store,
                      transcripciones=self.medicos.transcripciones(), aviso_medico=estado["aviso"], hoy=hoy, **extra)

    def plan(self) -> Planes:
        """Unit 041: an invented plan (`test_plan.plan_de_prueba`: S8 to S10 around `HOY`) with one incident and the
        race nutrition, saved in this SQLite."""
        planes = Planes(self.db)
        planes.crear(plan_de_prueba(
            incidencias=[{"apartado": 1, "fecha": "2026-10-20", "texto": "Presión de prueba en el gemelo"}],
            nutricion=[{"apartado": 1, "producto": "Gel de prueba", "pauta": "min 30, 60 y 90"}],
            sesiones=[*plan_de_prueba()["sesiones"],
                      {"apartado": 1, "fecha": "2026-10-28", "titulo": "Series de prueba 3×2000",
                       "bloques": ["20' a 5:30", "3×2000 a 4:15 (3' rec)", "15' suave"], "clave": True}]),
            AHORA)
        return planes


def _datos(ctx: Contexto) -> dict:
    return json.loads(ctx.bloques[-1])


def test_el_contexto_lleva_todo(tmp_path):  # R3 · R-114, G-46, C-155
    ctx = Mundo(tmp_path).contexto()
    texto = "\n".join(ctx.bloques)
    assert texto_acciones() in ctx.bloques[0]  # the catalogue of the 021 (G-37), once and fixed
    assert VALOR_INVENTADO in ctx.bloques[1] and "58,31" in ctx.bloques[1]  # the blood test and the stress test
    d = _datos(ctx)
    assert d["hoy"] == "2026-10-24"
    dias = {x["fecha"]: x for x in d["dias"]}
    assert len(d["dias"]) == 56 and d["dias"][0]["fecha"] == (HOY - timedelta(days=55)).isoformat()  # 8 weeks
    hoy = dias["2026-10-24"]
    assert [c["nombre"] for c in hoy["comidas"]] == ["tostada de prueba"]  # foods
    assert hoy["peso"] == 71.9  # weight
    assert hoy["pulso_reposo"] == 47 and hoy["pasos"] == 8123  # resting HR, steps
    assert hoy["sueno_h"] == 7.25 and hoy["sueno_puntos"] == 81  # sleep
    assert hoy["hrv"] == 63  # HRV
    carrera = dias[(HOY - timedelta(days=3)).isoformat()]
    assert [e["nombre"] for e in carrera["entrenos"]] == ["Carrera de prueba al río"]  # trainings
    assert carrera["carga"]["tss"] > 0 and "ctl" in hoy["carga"]  # the load (031)
    assert d["pulso"]["fc_max"] == 186 and "prueba de esfuerzo" in d["pulso"]["texto"]  # its HR (G-49)
    assert [c["nombre"] for c in d["carreras"]] == ["10K de prueba"]
    # what is older than 8 weeks goes by week: totals, never the names of the foods
    assert "comida antigua de prueba" not in texto and "Bici antigua de prueba" not in texto
    semanas = {s["lunes"]: s for s in d["semanas"]}
    antigua = semanas[(HOY - timedelta(days=100) - timedelta(days=(HOY - timedelta(days=100)).weekday())).isoformat()]
    assert antigua["kcal_media"] == 2222 and antigua["dias_con_comida"] == 1
    assert any(s.get("entrenos") == 1 and s.get("km") == 45.0 for s in d["semanas"])
    assert any(s.get("peso_medio") == 72.6 for s in d["semanas"])


def test_el_contexto_no_lleva_la_hora_y_se_repite_byte_a_byte(tmp_path):  # R3, R10 · P3
    mundo = Mundo(tmp_path)
    uno, dos = mundo.contexto(), mundo.contexto()
    assert uno.bloques == dos.bloques
    assert "9:42" not in "".join(uno.bloques) and "09:42" not in "".join(uno.bloques)


def test_un_dato_nuevo_cambia_el_contexto(tmp_path):  # R2 · C-157: the race of yesterday is known
    mundo = Mundo(tmp_path)
    antes = mundo.contexto()
    mundo.store.save_activities([Activity(activity_id="a2", activity_date=HOY - timedelta(days=1),
                                          activity_type="running", name="Carrera de ayer de prueba",
                                          duration_seconds=1500, distance_meters=5000, avg_hr=160)])
    despues = mundo.contexto()
    assert despues.bloques != antes.bloques
    assert "Carrera de ayer de prueba" in despues.bloques[-1]


def test_la_linea_dice_que_ve_y_hasta_cuando(tmp_path):  # R5 · R-115, C-156
    ctx = Mundo(tmp_path).contexto()
    assert linea(ctx, AHORA) == ("Ve tus comidas, peso, entrenos, sueño, pulso, HRV, analítica y prueba de esfuerzo, "
                                 "hasta hoy 9:42")
    assert "9:42" not in "".join(ctx.bloques)  # apart, never inside the fixed part


def test_sin_datos_medicos_lo_dice_en_la_linea(tmp_path):  # R4, R5
    ctx = Mundo(tmp_path, con_medicos=False).contexto()
    texto = linea(ctx, AHORA)
    assert texto.startswith("Ve tus comidas, peso, entrenos, sueño, pulso y HRV, hasta hoy 9:42")
    assert "analítica" in texto and "TRIDATA_MEDICOS_DIR" in texto
    assert VALOR_INVENTADO not in "".join(ctx.bloques)


# -- the criterio portante, through the routes: two conversations, the same fixed part --------------------------

@pytest.fixture
def web(tmp_path):
    from test_conversaciones_web import Web

    return Web(tmp_path)


def test_todas_las_conversaciones_ven_todo_y_lo_mismo(web):  # R3 · R-114, G-46, C-155, S-41
    web.sembrar()
    web.claude.responder("Tu peso va bien.")
    peso = web.nueva("¿cómo va mi peso de noviembre?")
    web.claude.responder("Para el 10K vas bien.")
    carrera = web.nueva("quiero hacer 10 km el 3 de diciembre en 45 minutos")
    assert peso["conversacion"]["id"] != carrera["conversacion"]["id"]
    p_peso, p_carrera = web.claude.peticiones
    assert p_peso["system"] == p_carrera["system"]  # byte to byte: none sees less
    fija = "\n".join(b["text"] for b in p_carrera["system"])
    for dato in ("tostada de prueba", "71.9", "Carrera de prueba al río", '"pulso_reposo":47', '"hrv":63',
                 '"sueno_h":7.25', '"ctl"', VALOR_INVENTADO, "58,31", "10K de prueba"):
        assert dato in fija, dato
    # what was said in one conversation never reaches the other (S-41)
    dicho = json.dumps(p_carrera["messages"], ensure_ascii=False)
    assert "peso de noviembre" not in dicho and "Tu peso va bien" not in dicho
    assert p_carrera["messages"] == [{"role": "user", "content": "quiero hacer 10 km el 3 de diciembre en 45 minutos"}]


# -- unit 041 · R2 (C-179, G-46): the plan in course and its incidents, the same for the four roles ---------------

def test_el_contexto_lleva_el_plan_en_curso_y_sus_incidencias(tmp_path):  # R2 · C-179
    mundo = Mundo(tmp_path)
    ctx = mundo.contexto(planes=mundo.plan())
    d = _datos(ctx)
    plan = d["plan"]
    assert plan["nombre"] == "Road to Ejemplo 26"
    assert [c["nombre"] for c in plan["carreras"]] == ["10K de ejemplo", "Maratón de ejemplo"]
    assert plan["carreras"][1]["objetivo"] == "3:05"
    semanas = {s["n"]: s for s in plan["semanas"]}
    assert set(semanas) == {8, 9, 10}
    assert semanas[9]["previsto_s"] == 16200 and semanas[9]["hoy"] is True  # planned...
    assert semanas[9]["hecho"]["carrera"]["n"] == 1  # ...and done: the run of `Mundo` on the Wednesday
    assert semanas[10]["hecho"] is None  # not started: nothing done, never a 0
    sesiones = {s["fecha"]: s for s in plan["sesiones"]}
    assert sesiones["2026-10-28"]["bloques"] == ["20' a 5:30", "3×2000 a 4:15 (3' rec)", "15' suave"]
    assert sesiones["2026-10-28"]["clave"] is True
    assert plan["nutricion"] == [{"producto": "Gel de prueba", "pauta": "min 30, 60 y 90"}]
    assert d["incidencias"] == [{"fecha": "2026-10-20", "texto": "Presión de prueba en el gemelo"}]
    assert "plan" in ctx.bloques[0] and "incidencias" in ctx.bloques[0]  # the instructions say what they are
    uno, dos = mundo.contexto(planes=Planes(mundo.db)), mundo.contexto(planes=Planes(mundo.db))
    assert uno.bloques == dos.bloques  # still byte to byte


def test_sin_plan_el_contexto_lo_dice_y_no_inventa(tmp_path):  # R2
    mundo = Mundo(tmp_path)
    d = _datos(mundo.contexto(planes=Planes(mundo.db)))
    assert d.get("plan") is None and d.get("incidencias") in (None, [])


def test_la_linea_dice_tu_plan_las_incidencias_y_el_modelo(tmp_path):  # R2, R7 · C-179, C-184
    mundo = Mundo(tmp_path)
    ctx = mundo.contexto(planes=mundo.plan())
    texto = linea(ctx, AHORA, "claude-sonnet-5-5")
    assert texto.startswith("Ve tus comidas, peso, entrenos, sueño, pulso, HRV, tu plan Road to Ejemplo 26, "
                            "incidencias (1)")
    assert "analítica y prueba de esfuerzo, hasta hoy 9:42" in texto
    assert texto.endswith(" · Sonnet")
    assert linea(ctx, AHORA, "claude-haiku-5-5").endswith(" · Haiku")
    assert linea(ctx, AHORA).endswith("hasta hoy 9:42")  # without a model, as before


def test_los_cuatro_papeles_ven_lo_mismo_con_el_plan(web):  # R2 · C-179, G-46 · the criterio portante
    web.plan()
    peticiones = []
    for papel in ("entrenador", "fisio", "nutricionista", "asistente"):
        web.claude.responder(f"Respuesta de prueba del {papel}.")
        fin = web.nueva("¿cómo voy?", papel=papel)
        assert fin["tipo"] == "fin", fin
        peticiones.append(web.claude.peticiones[-1])
    comunes = {json.dumps(p["system"][:-1], ensure_ascii=False, sort_keys=True) for p in peticiones}
    assert len(comunes) == 1  # byte to byte
    fija = "\n".join(b["text"] for b in peticiones[0]["system"][:-1])
    for dato in ("Road to Ejemplo 26", "Series de prueba 3×2000", "Gel de prueba", "Presión de prueba en el gemelo",
                 "tostada de prueba", VALOR_INVENTADO):
        assert dato in fija, dato
    assert peticiones[0]["system"][-2]["cache_control"] == {"type": "ephemeral"}
    assert len({p["system"][-1]["text"] for p in peticiones}) == 4  # each one its role, after the mark
    assert all("cache_control" not in p["system"][-1] for p in peticiones)


# -- unit 044 · R5 (R-149, C-191): the open incidents always, and the closed ones of the plan in course -------------

def test_el_contexto_lleva_las_abiertas_y_las_cerradas_con_sus_visitas(tmp_path):  # R5 · C-191
    mundo = Mundo(tmp_path)
    planes = mundo.plan()  # with one incident of its .md: «Presión de prueba en el gemelo»
    plan_id = planes.en_curso(HOY)
    abierta = planes.abrir_incidencia(plan_id, "tobillo", "derecho, lateral", date(2026, 10, 20),
                                      "molestia inventada al acabar el rodaje", AHORA)
    planes.anadir_visita(abierta, date(2026, 10, 22), "fisio", "sin lesión inventada, alta para correr", AHORA)
    cerrada = planes.abrir_incidencia(plan_id, "rodilla", None, date(2026, 9, 24), "sobrecarga inventada", AHORA)
    planes.anadir_visita(cerrada, date(2026, 9, 30), "médico", "reposo inventado", AHORA)
    planes.cerrar_incidencia(cerrada, "bici nunca tras la tirada larga", date(2026, 10, 1))
    # another plan, not in course: its open one goes too, its closed one does not
    otro = planes.crear({"nombre": "Otro plan de prueba", "apartados": [{"titulo": "Objetivos", "parte": "objetivos"}],
                         "carreras": [{"apartado": 0, "nombre": "Carrera de prueba 27", "fecha": "2027-07-04",
                                       "objetivo": "acabar", "estado": "pendiente", "principal": True}],
                         "semanas": [], "sesiones": [], "nutricion": [], "incidencias": [], "notas": []}, AHORA)
    planes.abrir_incidencia(otro, "cadera", None, date(2026, 10, 10), "molestia inventada de otro plan", AHORA)
    vieja = planes.abrir_incidencia(otro, "hombro", None, date(2026, 5, 1), "cerrada inventada de otro plan", AHORA)
    planes.cerrar_incidencia(vieja, "lección de otro plan", date(2026, 5, 20))

    ctx = mundo.contexto(planes=planes)
    incidencias = _datos(ctx)["incidencias"]
    texto = json.dumps(incidencias, ensure_ascii=False)
    assert "cerrada inventada de otro plan" not in texto and "lección de otro plan" not in texto
    por_texto = {i["texto"]: i for i in incidencias}
    assert set(por_texto) == {"Presión de prueba en el gemelo", "tobillo derecho, lateral: molestia inventada al "
                              "acabar el rodaje", "rodilla: sobrecarga inventada",
                              "cadera: molestia inventada de otro plan"}
    tobillo = por_texto["tobillo derecho, lateral: molestia inventada al acabar el rodaje"]
    assert (tobillo["estado"], tobillo["fecha"]) == ("abierta", "2026-10-20")
    assert tobillo["visitas"] == [{"fecha": "2026-10-22", "quien": "fisio",
                                   "que_dijo": "sin lesión inventada, alta para correr"}]
    rodilla = por_texto["rodilla: sobrecarga inventada"]
    assert (rodilla["estado"], rodilla["leccion"], rodilla["cerrada"]) == (
        "cerrada", "bici nunca tras la tirada larga", "2026-10-01")
    assert rodilla["visitas"] == [{"fecha": "2026-09-30", "quien": "médico", "que_dijo": "reposo inventado"}]
    assert por_texto["cadera: molestia inventada de otro plan"]["estado"] == "abierta"
    assert [i["texto"] for i in incidencias][:2] == [  # the open ones first
        "tobillo derecho, lateral: molestia inventada al acabar el rodaje", "cadera: molestia inventada de otro plan"]
    instrucciones = ctx.bloques[0]
    assert "abiertas" in instrucciones and "tenlas en cuenta" in instrucciones  # every role is told (R-149)


def test_la_nota_del_dia_va_en_el_contexto(tmp_path):  # R6 · C-192
    mundo = Mundo(tmp_path)
    planes = mundo.plan()
    planes.nota_del_dia(planes.en_curso(HOY), date(2026, 10, 21), "FC alta para el ritmo, piernas del domingo", AHORA)
    plan = _datos(mundo.contexto(planes=planes))["plan"]
    assert plan["notas_del_dia"] == [{"fecha": "2026-10-21", "texto": "FC alta para el ritmo, piernas del domingo"}]


# -- bug 052: every date the team talks about carries its weekday; the model never has to work it out ---------------

MIERCOLES = date(2026, 10, 28)  # the Wednesday of the report: the coach said «mié 29/10»


def test_cada_fecha_lleva_su_dia_de_la_semana(tmp_path):  # bug 052 · R1
    mundo = Mundo(tmp_path)
    d = _datos(mundo.contexto(hoy=MIERCOLES, planes=mundo.plan()))
    assert d["hoy"] == "2026-10-28" and d["dia_semana"] == "miércoles"
    dias = {x["fecha"]: x for x in d["dias"]}
    assert dias["2026-10-28"]["dia"] == "miércoles" and dias["2026-10-24"]["dia"] == "sábado"
    assert all("dia" in x for x in d["dias"])
    sesiones = {s["fecha"]: s for s in d["plan"]["sesiones"]}
    assert sesiones["2026-10-28"]["dia"] == "miércoles" and sesiones["2026-10-14"]["dia"] == "miércoles"
    semana = next(s for s in d["plan"]["semanas"] if s["n"] == 10)
    assert (semana["desde"], semana["desde_dia"], semana["hasta"], semana["hasta_dia"]) == \
        ("2026-10-26", "lunes", "2026-11-01", "domingo")
    calendario = d["calendario"]  # from the Monday of this week to the Sunday of the next one
    assert len(calendario) == 14
    assert calendario[0] == {"fecha": "2026-10-26", "dia": "lunes"}
    assert calendario[-1] == {"fecha": "2026-11-08", "dia": "domingo"}
    assert {"fecha": "2026-10-28", "dia": "miércoles"} in calendario
    assert {"fecha": "2026-10-29", "dia": "jueves"} in calendario


def test_las_instrucciones_dicen_que_el_dia_se_lee_no_se_calcula(tmp_path):  # bug 052 · R2
    instrucciones = Mundo(tmp_path).contexto(hoy=MIERCOLES).bloques[0]
    assert '"dia_semana"' in instrucciones and '"calendario"' in instrucciones
    assert "no lo calcules" in instrucciones


def test_el_resto_del_contexto_no_cambia(tmp_path):  # bug 052 · R3
    mundo = Mundo(tmp_path)
    d = _datos(mundo.contexto(hoy=MIERCOLES, planes=mundo.plan()))
    sesion = next(s for s in d["plan"]["sesiones"] if s["fecha"] == "2026-10-28")
    assert set(sesion) == {"id", "fecha", "dia", "titulo", "bloques", "clave"}
    semana = next(s for s in d["plan"]["semanas"] if s["n"] == 10)
    assert set(semana) - {"desde_dia", "hasta_dia"} == {"n", "desde", "hasta", "previsto_s", "clave", "hoy", "hecho"}


# -- unit 054 · R7: the team knows what Google Calendar carries (it said «deberían aparecer», and they did not) ------

def test_el_contexto_dice_si_google_calendar_esta_conectado_y_que_lleva(tmp_path):  # R7
    mundo = Mundo(tmp_path)
    conectado = _datos(mundo.contexto(calendario_conectado=True))["google_calendar"]
    assert conectado["conectado"] is True
    lleva = conectado["lleva"]
    assert "sesiones previstas del plan en curso" in lleva
    assert "entrenos hechos" in lleva and "2026-08-01" in lleva and "10 minutos" in lleva

    sin = _datos(mundo.contexto())["google_calendar"]  # without saying it: not connected
    assert sin["conectado"] is False and "nada" in sin["lleva"]
    assert _datos(mundo.contexto(calendario_conectado=False))["google_calendar"] == sin


def test_las_instrucciones_dicen_que_el_equipo_no_toca_el_calendario(tmp_path):  # R7
    instrucciones = Mundo(tmp_path).contexto().bloques[0]
    assert '"google_calendar"' in instrucciones
    assert "no puedes poner ni quitar nada" in instrucciones
    assert "Sincronizar ahora" in instrucciones and "Ajustes" in instrucciones


def test_la_app_le_pasa_al_equipo_el_estado_del_calendario(web):  # R7: through the routes
    web.sembrar()
    web.claude.responder("Mira Ajustes.")
    web.nueva("¿está mi rodaje de ayer en el calendario?")
    (peticion,) = web.claude.peticiones
    fija = "\n".join(b["text"] for b in peticion["system"])
    assert '"google_calendar":{"conectado":false' in fija
