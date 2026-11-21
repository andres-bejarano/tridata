"""Unit 100 (REC-44 of `preguntar-equipo`, R-280..R-282, C-351..C-355, Q-18): a context that fits the team.

The last 14 days (today and the 13 before) keep each food, the hours of the meals and the whole supplementation;
the earlier days of the 8 weeks keep everything else but only the caffeine of the supplementation. The foods of
older days are asked by dates with `get_comidas` (at most 31 days each time), with the same detail.

Over a temporary SQLite with the clock fixed; every datum is invented. Nothing talks to Anthropic, Garmin or Google.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta

import pytest

from tridata.conversaciones.contexto import INSTRUCCIONES
from tridata.conversaciones.desde_pc import contexto, montar_equipo, texto_contexto
from tridata.mcp_server import build_server
from tridata.models import DailyStats, SleepRecord
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas, RegistroPeso
from tridata.nutricion.suplementacion import Suplementacion
from tridata.storage import DataStore

HOY = date(2026, 11, 21)  # a Saturday
AHORA = datetime(2026, 11, 21, 9, 30)
SECCIONES_DEL_DIA = ("desayuno", "comida", "cena")
DETALLE = ("comidas", "horas_comidas", "suplementacion")


class Mundo:
    """A temporary SQLite; `dia_completo` gives a day foods by section, a coffee, a B12, a weight, steps and sleep."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        Ajustes(self.db).cambiar({"basal_kcal": 1650, "kcal_por_paso": 0.04, "pasos_por_defecto": 9000,
                                  "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.store = DataStore(self.db)
        self.sup = Suplementacion(self.db)
        self.equipo = montar_equipo(self.db, store=self.store, token=tmp_path / "google-token.json",
                                    hoy=lambda: HOY, ahora=lambda: AHORA)
        self.server = build_server(self.store, equipo=self.equipo)

    def dia_completo(self, dia: date, alimentos: int = 3) -> None:
        for j in range(alimentos):
            seccion = SECCIONES_DEL_DIA[j % len(SECCIONES_DEL_DIA)]
            self.registro.guardar([Alimento(nombre=f"alimento inventado {j} del {dia.isoformat()}", dia=dia,
                                            cantidad=100 + j, unidad="g", kcal=150 + j, proteina_g=10,
                                            hidratos_g=15, grasa_g=5)],
                                  hoy=HOY, texto="inventado", sin_cambios=True, seccion=seccion)
        self.sup.poner_hora_seccion(dia, "cena", "21:45")
        self.sup.apuntar_varias(dia, [{"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130},
                                      {"que": "b12", "hora": "09:00"}], hoy=HOY, ahora=AHORA)
        self.pesos.apuntar(dia, 72.4, nota="nota inventada", hoy=HOY)
        self.store.save_daily_stats(DailyStats(stat_date=dia, steps=9100, resting_hr=48, total_calories=2500))
        self.store.save_sleep(SleepRecord(sleep_date=dia, total_sleep_seconds=25200, sleep_score=77))

    def datos(self) -> dict:
        return json.loads(contexto(self.equipo).bloques[-1])

    def dias(self) -> dict[str, dict]:
        return {d["fecha"]: d for d in self.datos()["dias"]}

    def llamar(self, nombre: str, argumentos: dict) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


def _hace(n: int) -> date:
    return HOY - timedelta(days=n)


# -- R1 (portante · R-280, C-351, C-352): the border of the 14 days -----------------------------------------------

def test_hace_13_dias_con_alimentos_y_hace_14_sin_ellos(mundo):  # R1 · R-280, C-351, C-352
    for n in (0, 13, 14, 55, 70):
        mundo.dia_completo(_hace(n))
    dias = mundo.dias()
    for n in (0, 13):  # today and the 13 before: as before
        d = dias[_hace(n).isoformat()]
        assert len(d["comidas"]) == 3, n
        assert d["horas_comidas"] == {"desayuno": "~08:00", "comida": "~14:30", "cena": "21:45"}, n
        assert [t["hora"] for t in d["suplementacion"]["tomas"]] == ["09:00", "10:00"], n
        assert d["suplementacion"]["cafeina_mg"] == 130, n
    for n in (14, 55):  # the earlier days of the 8 weeks: without foods, the rest the same
        d = dias[_hace(n).isoformat()]
        assert "comidas" not in d and "horas_comidas" not in d, n
        assert d["suplementacion"] == {"cafeina_mg": 130}, n  # only the caffeine of the day
        assert d["kcal_comidas"] == 453 and d["peso"] == 72.4 and d["nota_peso"] == "nota inventada", n
        assert d["pasos"] == 9100 and d["kcal_garmin"] == 2500 and d["pulso_reposo"] == 48, n
        assert d["sueno_h"] == 7 and d["sueno_puntos"] == 77, n
        assert d["macros"]["proteina_g"] == 30, n
        assert d["dia"] and d["fecha"], n
    texto = "\n".join(contexto(mundo.equipo).bloques)
    for n in (14, 55, 70):  # no food name of those days reaches the context
        assert f"del {_hace(n).isoformat()}" not in texto, n
    # the weeks before the 8 stay as they were: totals
    semanas = mundo.datos()["semanas"]
    assert any(s.get("kcal_media") == 453 and s.get("dias_con_comida") == 1 for s in semanas)


def test_un_dia_viejo_sin_cafeina_no_lleva_suplementacion(mundo):  # R1 · C-352: what is not there is not sent
    dia = _hace(20)
    mundo.registro.guardar([Alimento(nombre="algo inventado", dia=dia, cantidad=50, unidad="g", kcal=90)],
                           hoy=HOY, texto="x", sin_cambios=True)
    mundo.sup.apuntar(dia, {"que": "b12", "hora": "09:00"}, hoy=HOY, ahora=AHORA)
    d = mundo.dias()[dia.isoformat()]
    assert "suplementacion" not in d and "comidas" not in d and d["kcal_comidas"] == 90


# -- R2 (R-281, C-353): get_comidas ----------------------------------------------------------------------------------

def test_get_comidas_da_los_dias_pedidos_con_su_detalle(mundo):  # R2 · R-281, C-353
    desde, hasta = date(2026, 9, 29), date(2026, 10, 5)
    vacio = date(2026, 10, 2)
    for i in range(7):
        dia = desde + timedelta(days=i)
        if dia != vacio:
            mundo.dia_completo(dia)
    datos = json.loads(mundo.llamar("get_comidas", {"desde": desde.isoformat(), "hasta": hasta.isoformat()}))
    dias = datos["dias"]
    assert [d["fecha"] for d in dias] == [(desde + timedelta(days=i)).isoformat() for i in range(7)]
    assert [d["dia"] for d in dias][:2] == ["martes", "miércoles"]
    for d in dias:
        if d["fecha"] == vacio.isoformat():  # a day without anything logged: empty, no error
            assert not any(k in d for k in DETALLE)
            continue
        assert [c["nombre"] for c in d["comidas"]] == [f"alimento inventado {j} del {d['fecha']}" for j in range(3)]
        assert d["horas_comidas"] == {"desayuno": "~08:00", "comida": "~14:30", "cena": "21:45"}
        assert d["suplementacion"]["cafeina_mg"] == 130 and len(d["suplementacion"]["tomas"]) == 2


def test_get_comidas_da_el_mismo_detalle_que_los_dias_recientes(mundo):  # R2 · C-353: the same detail
    ayer = _hace(1)
    mundo.dia_completo(ayer)
    reciente = mundo.dias()[ayer.isoformat()]
    (pedido,) = json.loads(mundo.llamar("get_comidas", {"desde": ayer.isoformat(),
                                                        "hasta": ayer.isoformat()}))["dias"]
    assert pedido == {k: reciente[k] for k in ("fecha", "dia", *DETALLE)}


def test_get_comidas_es_de_solo_lectura(mundo):  # R2 · R-281
    herramientas = {t.name: t for t in asyncio.run(mundo.server.list_tools())}
    herramienta = herramientas["get_comidas"]
    assert herramienta.annotations.read_only_hint
    assert set(herramienta.input_schema["required"]) == {"desde", "hasta"}
    assert "31" in herramienta.description


# -- R3 (R-281, C-354): a bad range says what to fix and gives no data ---------------------------------------------

@pytest.mark.parametrize("desde, hasta, motivo", [
    ("2026-09-01", "2026-10-05", "como mucho 31 días"),
    ("2026-10-05", "2026-09-29", "desde antes que hasta"),
    ("1/10/2026", "2026-10-05", "fecha AAAA-MM-DD"),
    ("2026-10-01", "mañana", "fecha AAAA-MM-DD"),
    ("2026-02-30", "2026-03-01", "fecha AAAA-MM-DD"),
])
def test_un_rango_malo_dice_que_corregir_y_no_da_datos(mundo, desde, hasta, motivo):  # R3 · C-354
    from tridata.conversaciones.desde_pc import comidas_desde_pc

    mundo.dia_completo(date(2026, 10, 1))
    respuesta = comidas_desde_pc(mundo.equipo, desde, hasta)
    assert motivo in respuesta, respuesta
    assert "alimento inventado" not in respuesta and '"dias"' not in respuesta


def test_31_dias_justos_si_valen(mundo):  # R3: the limit itself is allowed
    from tridata.conversaciones.desde_pc import comidas_desde_pc

    datos = json.loads(comidas_desde_pc(mundo.equipo, "2026-09-05", "2026-10-05"))
    assert len(datos["dias"]) == 31


# -- R4 (R-282, C-355): the instructions say how ------------------------------------------------------------------

def test_las_instrucciones_dicen_como_pedir_los_alimentos_viejos():  # R4 · R-282, C-355
    assert "get_comidas" in INSTRUCCIONES
    assert "14 días" in INSTRUCCIONES
    parrafo = INSTRUCCIONES[INSTRUCCIONES.index("get_comidas") - 400:INSTRUCCIONES.index("get_comidas") + 200]
    assert "alimentos" in parrafo and "fechas" in parrafo


# -- R5 (Q-18): the size -----------------------------------------------------------------------------------------

def test_el_contexto_ocupa_como_mucho_el_65_por_ciento(mundo):  # R5 · Q-18
    """56 days with 15 foods each. «Before» is this same context with every day of the 8 weeks carrying its whole
    detail again (as it was until this unit), taken from `get_comidas`: the same JSON with sorted keys."""
    for n in range(56):
        mundo.dia_completo(_hace(n), alimentos=15)
    ahora = texto_contexto(mundo.equipo)
    bloques = contexto(mundo.equipo).bloques
    datos = json.loads(bloques[-1])
    detalle = {}
    for desde, hasta in ((_hace(55), _hace(28)), (_hace(27), HOY)):
        for d in json.loads(mundo.llamar("get_comidas", {"desde": desde.isoformat(),
                                                         "hasta": hasta.isoformat()}))["dias"]:
            detalle[d["fecha"]] = d
    datos["dias"] = [{**d, **{k: detalle[d["fecha"]][k] for k in DETALLE if k in detalle[d["fecha"]]}}
                     for d in datos["dias"]]
    antes = "\n\n".join((*bloques[:-1], json.dumps(datos, ensure_ascii=False, sort_keys=True,
                                                   separators=(",", ":"))))
    assert sum(len(d.get("comidas", [])) for d in datos["dias"]) == 56 * 15  # «before» really has them all
    assert len(ahora) <= 0.65 * len(antes), (len(ahora), len(antes), round(len(ahora) / len(antes), 3))
