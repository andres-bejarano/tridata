"""Bug 046: a plan pasted from a document has its sections as numbered lines («1. Objetivos») and no «#».

The invented plan of the tests is turned into that shape (its «# » title as a plain first line, each «## X» as
«N. X»); cutting it and importing it must give the same as the «#» one (R5, R6 of unit 040). A body line that starts
with a number («1 día de calidad») and a numbered list inside a section never cut it.
"""
from __future__ import annotations

from datetime import date

from tridata.plan.importar import nombre_del_plan, proponer, trocear

from test_plan_importar import FIXTURE, OrdenadorFalso, sembrar_garmin, store  # noqa: F401  (fixture)

HOY = date(2026, 10, 25)


def numerado(texto: str) -> str:
    """The fixture as a document gives it: the title without «# », and «## X» as «N. X» in order."""
    lineas, n = [], 0
    for linea in texto.splitlines(keepends=True):
        if linea.startswith("## "):
            n += 1
            linea = f"{n}. {linea[3:]}"
        elif linea.startswith("# "):
            linea = linea[2:]
        lineas.append(linea)
    return "".join(lineas)


def con_numeros_dentro(texto: str) -> str:
    """A body line that starts with a number and a numbered list inside the first section."""
    return texto.replace("1. Objetivos\n", "1. Objetivos\n\n1 día de calidad a la semana.\n1. Calentar\n2. Correr\n\n",
                         1)


def test_trocea_por_los_titulos_numerados_sin_perder_nada():
    con_almohadilla = trocear(FIXTURE.read_text(encoding="utf-8"))
    texto = numerado(FIXTURE.read_text(encoding="utf-8"))
    trozos = trocear(texto)
    assert [t.titulo for t in trozos] == [t.titulo for t in con_almohadilla]
    assert "".join(t.texto for t in trozos) == texto


def test_un_numero_dentro_de_un_apartado_no_corta():
    texto = con_numeros_dentro(numerado(FIXTURE.read_text(encoding="utf-8")))
    titulos = [t.titulo for t in trocear(texto)]
    assert titulos == [t.titulo for t in trocear(FIXTURE.read_text(encoding="utf-8"))]
    assert "1 día de calidad a la semana." in trocear(texto)[1].cuerpo


def test_el_nombre_del_plan_es_su_primera_linea():
    assert nombre_del_plan(numerado(FIXTURE.read_text(encoding="utf-8"))) == \
        nombre_del_plan(FIXTURE.read_text(encoding="utf-8"))


def test_importar_numerado_da_lo_mismo_que_con_almohadilla(store):  # noqa: F811
    sembrar_garmin(store)
    original = proponer(FIXTURE.read_text(encoding="utf-8"), OrdenadorFalso(), store, HOY)
    doc = proponer(numerado(FIXTURE.read_text(encoding="utf-8")), OrdenadorFalso(), store, HOY)
    for parte in ("carreras", "semanas", "sesiones", "nutricion", "incidencias", "notas", "diferencias"):
        assert len(doc[parte]) == len(original[parte]), parte
    assert len(doc["carreras"]) > 0 and len(doc["semanas"]) > 0
