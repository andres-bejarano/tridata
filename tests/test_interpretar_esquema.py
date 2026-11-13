"""Bug 036 / 055: a change given as text is read back (`conversaciones/cambios.py::CambioInterpretado`, what the
coach's `preparar_cambio` / `hacer_cambio` validate). Unit 083: the schema sent to the API is gone with the API, and
the two tests of its shape with it (`hallazgos.md`); how what comes as text is read stays.
"""

from tridata.conversaciones.cambios import CambioInterpretado, LineaInterpretada


def test_lo_que_llega_vacio_es_nada_y_las_lineas_vuelven_a_ser_lineas():
    cambio = CambioInterpretado.model_validate({
        "accion": "crear_receta", "objeto": "", "campo": "", "valor": None, "valores": [], "texto": "prueba",
        "cantidad": None, "unidad": "", "kcal_estimadas": None, "dia": "", "desde": "", "fecha": "2026-10-24",
        "antes_dicho": "", "falta": "",
        "lineas": ["100|g|arroz|", "1|unidad|huevo|78", "||sal|"],
    })
    assert cambio.objeto is None and cambio.dia is None and cambio.valores is None and cambio.falta is None
    assert str(cambio.fecha) == "2026-10-24" and cambio.texto == "prueba"
    assert cambio.lineas == [
        LineaInterpretada(nombre="arroz", cantidad=100, unidad="g", kcal_estimadas=None),
        LineaInterpretada(nombre="huevo", cantidad=1, unidad="unidad", kcal_estimadas=78),
        LineaInterpretada(nombre="sal", cantidad=None, unidad=None, kcal_estimadas=None),
    ]


def test_un_cambio_hecho_en_python_sigue_igual():
    # The rest of the app (and its tests) builds changes with LineaInterpretada objects and None: unchanged.
    linea = LineaInterpretada(nombre="arroz", cantidad=100, unidad="g", kcal_estimadas=None)
    cambio = CambioInterpretado(accion="crear_receta", texto="prueba", lineas=[linea])
    assert cambio.lineas == [linea] and cambio.objeto is None


def test_varios_valores_viajan_en_un_texto_y_vuelven_a_ser_numeros():
    cambio = CambioInterpretado.model_validate({"accion": "niveles_dia", "valores": "-10; -20;0"})
    assert cambio.valores == [-10, -20, 0]


def test_un_numero_mal_escrito_no_tumba_la_interpretacion():
    # Revisión 1: «1,5», «arroz» solo o «-10;a» no deben hacer fallar toda la respuesta.
    cambio = CambioInterpretado.model_validate({
        "accion": "nueva_receta", "valores": "-10;a;2,5", "lineas": ["1,5|g|arroz|", "arroz", "mucho|g|sal|unas"]})
    assert cambio.valores == [-10, 2.5]
    assert cambio.lineas == [
        LineaInterpretada(nombre="arroz", cantidad=1.5, unidad="g", kcal_estimadas=None),
        LineaInterpretada(nombre="arroz", cantidad=None, unidad=None, kcal_estimadas=None),
        LineaInterpretada(nombre="sal", cantidad=None, unidad="g", kcal_estimadas=None),
    ]
