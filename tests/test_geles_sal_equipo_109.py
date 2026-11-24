"""Unit 109 (REC-52): what the coach of the PC does with gels, salt and saved products — `apuntar_toma` with
`que: gel|sal`, the new `guardar_producto_suplemento` — and what `get_contexto` gives him (R1, R3, R6, R7).

Through the MCP server (`mcp_server.py`, `conversaciones/desde_pc.py`) over a temporary SQLite with the clock fixed.
Nothing talks to Anthropic, Garmin or Google. Every value is invented.
Plans: `apuntar-comida` R-317, R-319, R-322, R-323, C-404, C-410, C-411, C-412, S-108.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from tridata.conversaciones import Conversaciones
from tridata.conversaciones.desde_pc import TITULO, contexto, montar_equipo
from tridata.mcp_server import build_server
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.nutricion.suplementacion import Suplementacion
from tridata.storage import DataStore

HOY = date(2026, 11, 24)  # a Tuesday
AYER = HOY - timedelta(days=1)
DOMINGO = date(2026, 11, 29)  # a day that has not come yet
AHORA = datetime(2026, 11, 24, 16, 40)


class Mundo:
    """Today 1.200 kcal of an invented rice; Sunday, nothing yet."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        Ajustes(self.db).cambiar({"basal_kcal": 1650, "kcal_por_paso": 0.04, "pasos_por_defecto": 9000,
                                  "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.registro.guardar([Alimento(nombre="arroz inventado", dia=HOY, cantidad=300, unidad="g", kcal=1200)],
                              hoy=HOY, texto="arroz", sin_cambios=True)
        self.store = DataStore(self.db)
        self.equipo = montar_equipo(self.db, store=self.store, token=tmp_path / "google-token.json",
                                    hoy=lambda: HOY, ahora=lambda: AHORA)
        self.server = build_server(self.store, equipo=self.equipo)

    def llamar(self, nombre: str, argumentos: dict) -> str:
        resultado = asyncio.run(self.server.call_tool(nombre, argumentos))
        assert not resultado.is_error, resultado
        return "".join(c.text for c in resultado.content)

    def toma(self, dia: date, tomas: list[dict]) -> str:
        return self.llamar("apuntar_toma", {"papel": "nutricionista", "dia": dia.isoformat(), "tomas": tomas})

    def sup(self) -> Suplementacion:
        return Suplementacion(self.db)

    def kcal(self, dia: date = HOY) -> float:
        return sum(a["kcal"] or 0 for a in self.registro.apuntes_del_dia(dia))

    def volcado(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return list(conn.iterdump())
        finally:
            conn.close()

    def mensajes_pc(self) -> list[dict]:
        conversaciones = Conversaciones(self.db)
        return [m for c in conversaciones.lista() if c["titulo"] == TITULO for m in conversaciones.mensajes(c["id"])]

    def datos(self) -> dict:
        return json.loads(contexto(self.equipo).bloques[-1])


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


GEL = {"que": "gel", "producto": "Gel cola de prueba", "mg": 100}


# -- R1: gels for a day that has not come, with the «sí» --------------------------------------------------------------

def test_apuntar_geles_del_domingo_sin_kcal(mundo):  # R1 · R-317, G-68, C-404
    antes_alimentos = mundo.registro.apuntes_del_dia(HOY)
    respuesta = mundo.toma(DOMINGO, [{**GEL, "minuto": 60}, {**GEL, "minuto": 90}])
    assert respuesta.startswith("Hecho")
    assert "min 60" in respuesta and "min 90" in respuesta
    assert "kcal" in respuesta and "Nutrición en carrera" in respuesta  # S-108: his kcal are another question
    r = mundo.sup().resumen(DOMINGO)
    assert r["cafeina_mg"] == 200 and [t["minuto"] for t in r["en_carrera"]] == [60, 90]
    assert mundo.registro.apuntes_del_dia(DOMINGO) == [] and mundo.kcal(DOMINGO) == 0
    assert mundo.registro.apuntes_del_dia(HOY) == antes_alimentos and mundo.kcal() == 1200
    assert any("min 60" in m["texto"] for m in mundo.mensajes_pc())  # «Desde el PC» (R-196)


def test_gel_de_un_producto_guardado_sin_mg(mundo):  # R6 · R-319: «usa los del producto»
    mundo.sup().guardar_producto({"nombre": "Gel 226 cola", "tipo": "gel", "mg_cafeina": 100, "sodio_mg": 50})
    respuesta = mundo.toma(DOMINGO, [{"que": "gel", "producto": "gel 226 cola", "cantidad": 2, "minuto": 60}])
    assert respuesta.startswith("Hecho"), respuesta
    (toma,) = mundo.sup().tomas_del_dia(DOMINGO)
    assert (toma["producto"], toma["mg"], toma["sodio_mg"]) == ("Gel 226 cola", 200, 100)


def test_gel_sin_hora_ni_minuto(mundo):  # R7 · R-323, C-411
    antes = mundo.volcado()
    respuesta = mundo.toma(DOMINGO, [GEL])
    assert respuesta.startswith("No se ha apuntado nada") and "falta la hora o el minuto" in respuesta
    assert mundo.volcado() == antes


@pytest.mark.parametrize("toma, motivo", [
    ({**GEL, "mg": "nan", "minuto": 60}, "finito"),
    ({**GEL, "cantidad": 0, "minuto": 60}, "mayor que 0"),
    ({"que": "sal", "minuto": 75}, "sodio"),
    ({"que": "cafe", "forma": "doble", "hora": "10:00", "mg": "inf"}, "finito"),
])
def test_tomas_no_validas_no_apuntan_nada(mundo, toma, motivo):  # R7 · C-412
    antes = mundo.volcado()
    respuesta = mundo.toma(DOMINGO, [toma])
    assert respuesta.startswith("No se ha apuntado nada") and motivo in respuesta
    assert mundo.volcado() == antes


# -- R6: saving a product, with the «sí» -------------------------------------------------------------------------------

def test_guardar_y_cambiar_un_producto(mundo):  # R6 · R-319, R-322
    respuesta = mundo.llamar("guardar_producto_suplemento", {"papel": "nutricionista", "nombre": "Gel 226 cola",
                                                              "tipo": "gel", "mg_cafeina": 100, "sodio_mg": 50})
    assert respuesta.startswith("Hecho") and "Gel 226 cola" in respuesta
    respuesta = mundo.llamar("guardar_producto_suplemento", {"papel": "nutricionista", "nombre": "GEL 226 COLA",
                                                              "tipo": "gel", "mg_cafeina": 75, "sodio_mg": 50})
    assert respuesta.startswith("Hecho") and "cambiado" in respuesta
    (gel,) = mundo.sup().productos()
    assert (gel["nombre"], gel["mg_cafeina"], gel["sodio_mg"]) == ("Gel 226 cola", 75, 50)
    sal = mundo.llamar("guardar_producto_suplemento", {"papel": "nutricionista", "nombre": "Pastilla de prueba",
                                                        "tipo": "sal", "sodio_mg": 215})
    assert sal.startswith("Hecho")
    assert len(mundo.mensajes_pc()) == 3


@pytest.mark.parametrize("args, motivo", [
    ({"nombre": "X", "tipo": "barrita", "mg_cafeina": 10}, "gel o sal"),
    ({"nombre": "X", "tipo": "gel", "mg_cafeina": "nan"}, "finito"),
    ({"nombre": " ", "tipo": "gel", "mg_cafeina": 10}, "nombre"),
])
def test_producto_no_valido_no_guarda(mundo, args, motivo):  # R7
    antes = mundo.volcado()
    respuesta = mundo.llamar("guardar_producto_suplemento", {"papel": "nutricionista", **args})
    assert respuesta.startswith("No se ha guardado") and motivo in respuesta
    assert mundo.volcado() == antes


def test_las_herramientas_explican_geles_y_sal(mundo):  # R6 · R-322, S-108
    tools = {t.name: t for t in asyncio.run(mundo.server.list_tools())}
    toma = tools["apuntar_toma"].description
    for palabra in ("gel", "sal", "minuto", "producto", "cantidad", "sodio_mg", "Nutrición en carrera",
                    "guardar_producto_suplemento", "falta la hora o el minuto"):
        assert palabra in toma, palabra
    producto = tools["guardar_producto_suplemento"]
    assert not producto.annotations.read_only_hint
    assert "«sí»" in producto.description and "Andrés" in producto.description and "Hecho" in producto.description
    assert set(producto.input_schema["required"]) == {"papel", "nombre", "tipo"}


# -- R6: the context ---------------------------------------------------------------------------------------------------

def test_el_contexto_del_domingo_lleva_geles_sal_y_sodio(mundo):  # R6 · R-322, C-410
    mundo.sup().guardar_producto({"nombre": "Gel 226 cola", "tipo": "gel", "mg_cafeina": 100, "sodio_mg": 50})
    mundo.toma(DOMINGO, [{"que": "gel", "producto": "Gel 226 cola", "minuto": 60},
                         {"que": "gel", "producto": "Gel 226 cola", "minuto": 90},
                         {"que": "sal", "producto": "Pastilla de prueba", "sodio_mg": 215, "minuto": 75}])
    datos = mundo.datos()
    (domingo,) = [d for d in datos["planeado_despues_de_hoy"] if d["fecha"] == DOMINGO.isoformat()]
    s = domingo["suplementacion"]
    assert [(t["minuto"], t["que"]) for t in s["en_carrera"]] == [(60, "Gel 226 cola"), (75, "Pastilla de prueba"),
                                                                 (90, "Gel 226 cola")]
    assert s["en_carrera"][0]["mg"] == 100 and s["en_carrera"][1]["sodio_mg"] == 215
    assert (s["cafeina_mg"], s["cafeina_cafe_mg"], s["cafeina_geles_mg"], s["sodio_mg"]) == (200, 0, 200, 315)
    assert datos["productos_suplemento"] == [{"id": 1, "nombre": "Gel 226 cola", "tipo": "gel", "mg_cafeina": 100,
                                              "sodio_mg": 50}]


def test_el_contexto_de_hoy_con_cafe_y_gel(mundo):  # R6: a recent day too
    mundo.toma(HOY, [{"que": "cafe", "forma": "doble", "hora": "10:00", "mg": 130},
                     {**GEL, "hora": "11:15"}])
    (hoy,) = [d for d in mundo.datos()["dias"] if d["fecha"] == HOY.isoformat()]
    s = hoy["suplementacion"]
    assert [(t["hora"], t["que"]) for t in s["tomas"]] == [("10:00", "café doble"), ("11:15", "Gel cola de prueba")]
    assert (s["cafeina_mg"], s["cafeina_cafe_mg"], s["cafeina_geles_mg"], s["ultima"]) == (230, 130, 100, "11:15")
    assert "sodio_mg" not in s and "en_carrera" not in s


def test_sin_productos_el_contexto_no_los_lleva(mundo):  # R6: what is not there is not sent
    assert "productos_suplemento" not in mundo.datos()
