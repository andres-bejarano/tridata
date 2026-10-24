"""Bug 036: the strict schema sent to Claude must fit the API's grammar limit (no network: the schema shape)."""

from anthropic.lib._parse._transform import transform_schema

from tridata.ia.claude import CambioInterpretado, LineaInterpretada, ListaInterpretada


def _cambio_enviado() -> dict:
    esquema = transform_schema(ListaInterpretada.model_json_schema())
    return esquema["$defs"]["CambioInterpretado"]


def test_un_cambio_no_lleva_listas_de_objetos_dentro():
    # A list of objects inside each element of `cambios` is what made the API answer «grammar too large».
    lineas = _cambio_enviado()["properties"]["lineas"]
    assert lineas == {**lineas, "type": "array", "items": {"type": "string"}}


def test_los_textos_y_fechas_de_un_cambio_no_pueden_ser_null():
    propiedades = _cambio_enviado()["properties"]
    for nombre in ("objeto", "campo", "texto", "unidad", "antes_dicho", "falta", "dia", "desde", "fecha"):
        assert propiedades[nombre].get("type") == "string", nombre
        assert "anyOf" not in propiedades[nombre], nombre


def test_lo_que_llega_vacio_es_nada_y_las_lineas_vuelven_a_ser_lineas():
    cambio = CambioInterpretado.model_validate({
        "accion": "crear_receta", "objeto": "", "campo": "", "valor": None, "valores": [], "texto": "prueba",
        "cantidad": None, "unidad": "", "kcal_estimadas": None, "dia": "", "desde": "", "fecha": "2026-10-24",
        "antes_dicho": "", "falta": "",
        "lineas": ["100|g|arroz|", "1|unidad|huevo|78", "|||sal|"],
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
