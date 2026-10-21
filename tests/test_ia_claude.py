"""Claude interpretation (unit 002). The API is NEVER called: the client is a
unittest.mock double injected into the interpreter, so the suite costs nothing.
"""
import importlib.util
import json
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
import httpx2
import pytest

from tridata.ia import (
    INSTRUCCION, MODELO_POR_DEFECTO, AlimentoInterpretado, ErrorInterpretacion, InterpreteClaude,
    ListaInterpretada, ObjetivoDiaInterpretado, PesoInterpretado,
)

HOY = date(2026, 3, 4)  # a Wednesday
RAIZ = Path(__file__).resolve().parents[1]


def _item(nombre, **campos):
    datos = {
        "nombre": nombre, "cantidad": 100.0, "unidad": "g", "dia": HOY, "kcal": 100.0,
        "proteina_g": None, "hidratos_g": None, "grasa_g": None, "estimado": True,
        "dudoso": False, "supuestos": [],
    }
    datos.update(campos)
    return AlimentoInterpretado(**datos)


def _cliente(*items, stop_reason="end_turn", parsed=True):
    cliente = MagicMock()
    salida = ListaInterpretada(alimentos=list(items)) if parsed else None
    cliente.messages.parse.return_value = SimpleNamespace(parsed_output=salida, stop_reason=stop_reason)
    return cliente


# -- R8: the request carries only the text, today's date and the fixed instruction --

def test_peticion_lleva_solo_texto_fecha_e_instruccion(monkeypatch):
    monkeypatch.delenv("TRIDATA_MODELO_TEXTO", raising=False)
    cliente = _cliente(_item("avena"))
    texto = "37 gramos de avena... no, 33 de avena"

    InterpreteClaude(cliente=cliente).interpretar(texto, HOY)

    kwargs = cliente.messages.parse.call_args.kwargs
    assert set(kwargs) == {"model", "max_tokens", "system", "messages", "output_format", "output_config"}
    assert kwargs["model"] == MODELO_POR_DEFECTO == "claude-haiku-5-5"
    assert kwargs["system"] == INSTRUCCION
    assert kwargs["output_format"] is ListaInterpretada
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["messages"] == [
        {"role": "user", "content": f"Hoy es miércoles, 2026-03-04.\n\nLo que ha escrito:\n{texto}"},
    ]


def test_la_instruccion_es_fija_y_no_lleva_datos_guardados():
    assert isinstance(INSTRUCCION, str)
    assert re.search(r"\d{4}-\d{2}-\d{2}", INSTRUCCION) is None  # no day, no stored data
    # the four rules the contract asks for are in it (G-15, G-16, R-7, R-8)
    for regla in ("último", "estimado", "dudoso", "cantidad"):
        assert regla in INSTRUCCION


def test_el_modelo_sale_de_la_configuracion(monkeypatch):
    monkeypatch.setenv("TRIDATA_MODELO_TEXTO", "claude-sonnet-5-5")
    cliente = _cliente(_item("pan"))
    InterpreteClaude(cliente=cliente).interpretar("pan", HOY)
    assert cliente.messages.parse.call_args.kwargs["model"] == "claude-sonnet-5-5"


# -- R1, R3, R6, R7: structured response -> list ----------------------------------

def test_respuesta_estructurada_a_lista_con_sus_marcas():
    cliente = _cliente(
        _item("jamón de york", cantidad=200, kcal=210, estimado=False),
        _item("tortilla de patata", cantidad=150, kcal=300, estimado=True,
              supuestos=["trozo mediano de 150 g"]),
        _item("kfjd", cantidad=None, unidad=None, kcal=40, dudoso=True),
        _item("leche", cantidad=None, unidad=None, kcal=None),
    )
    lista = InterpreteClaude(cliente=cliente, modelo="m").interpretar("texto inventado", HOY).alimentos

    assert [a.nombre for a in lista] == ["jamón de york", "tortilla de patata", "kfjd", "leche"]
    jamon, tortilla, kfjd, leche = lista
    assert (jamon.cantidad, jamon.unidad, jamon.kcal, jamon.estimado) == (200, "g", 210, False)
    assert tortilla.estimado is True and tortilla.supuestos == ["trozo mediano de 150 g"]
    assert kfjd.dudoso is True and kfjd.kcal is None  # a doubtful item never carries kcal
    assert leche.cantidad is None and leche.dudoso is False
    assert all(a.dia == HOY for a in lista)


# -- R10: SDK failures -> one own error ------------------------------------------

def _peticion():
    return httpx2.Request("POST", "https://example.invalid/v1/messages")


@pytest.mark.parametrize("fallo", [
    lambda: anthropic.APIConnectionError(request=_peticion()),
    lambda: anthropic.RateLimitError(
        "límite", response=httpx2.Response(429, request=_peticion()), body=None),
    lambda: anthropic.AuthenticationError(
        "sin clave", response=httpx2.Response(401, request=_peticion()), body=None),
    lambda: anthropic.InternalServerError(
        "caído", response=httpx2.Response(500, request=_peticion()), body=None),
])
def test_fallos_del_sdk_dan_un_error_propio(fallo):
    cliente = MagicMock()
    cliente.messages.parse.side_effect = fallo()
    with pytest.raises(ErrorInterpretacion):
        InterpreteClaude(cliente=cliente, modelo="m").interpretar("pan", HOY)


def test_respuesta_rechazada_da_error_propio():
    cliente = _cliente(stop_reason="refusal", parsed=False)
    with pytest.raises(ErrorInterpretacion):
        InterpreteClaude(cliente=cliente, modelo="m").interpretar("pan", HOY)


def test_respuesta_cortada_da_error_propio():
    cliente = _cliente(_item("pan"), stop_reason="max_tokens")
    with pytest.raises(ErrorInterpretacion):
        InterpreteClaude(cliente=cliente, modelo="m").interpretar("pan", HOY)


def test_sin_clave_al_crear_el_cliente_da_error_propio():
    with patch("tridata.ia.claude.anthropic.Anthropic", side_effect=anthropic.AnthropicError("sin clave")):
        with pytest.raises(ErrorInterpretacion):
            InterpreteClaude(modelo="m").interpretar("pan", HOY)


def test_crear_el_interprete_no_crea_cliente_ni_llama():
    with patch("tridata.ia.claude.anthropic.Anthropic") as fabrica:
        InterpreteClaude(modelo="m")
    fabrica.assert_not_called()


# -- R12: the manual Haiku test (script + invented dictations), with a double -----

def _script():
    ruta = RAIZ / "scripts" / "probar_interpretacion.py"
    spec = importlib.util.spec_from_file_location("probar_interpretacion", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _dictados():
    return json.loads((RAIZ / "tests" / "fixtures" / "dictados.json").read_text(encoding="utf-8"))


def test_dictados_inventados_entre_20_y_30_bien_formados():
    datos = _dictados()
    date.fromisoformat(datos["hoy"])
    assert 20 <= len(datos["casos"]) <= 30
    for caso in datos["casos"]:
        assert caso["texto"].strip()
        for esperado in caso["esperado"]:
            assert set(esperado) >= {"nombre", "cantidad", "unidad", "dia"}
            date.fromisoformat(esperado["dia"])


def _como_si_claude_acertara(caso):
    salida = []
    for e in caso["esperado"]:
        nombre = e["nombre"][0] if isinstance(e["nombre"], list) else e["nombre"]
        salida.append(_item(nombre, cantidad=e["cantidad"], unidad=e["unidad"],
                            dia=date.fromisoformat(e["dia"]), dudoso=e.get("dudoso", False),
                            kcal=None if e.get("dudoso") else 100.0))
    return salida


def test_script_cuenta_aciertos_con_un_doble():
    script, datos = _script(), _dictados()
    interprete = MagicMock()
    interprete.interpretar.side_effect = lambda texto, hoy: _como_si_claude_acertara(
        next(c for c in datos["casos"] if c["texto"] == texto))

    resultado = script.evaluar(interprete, datos)

    assert resultado.aciertos == resultado.total == len(datos["casos"])
    assert interprete.interpretar.call_args.args[1] == date.fromisoformat(datos["hoy"])


def test_script_marca_fallo_si_la_cantidad_o_el_dia_no_cuadran():
    script = _script()
    esperado = [{"nombre": ["avena"], "cantidad": 33, "unidad": "g", "dia": "2026-03-04"}]
    bien = [{"nombre": "Copos de avena", "cantidad": 33.0, "unidad": "gramos", "dia": "2026-03-04",
             "dudoso": False}]
    assert script.comparar(esperado, bien) == []
    assert script.comparar(esperado, [dict(bien[0], cantidad=37.0)])
    assert script.comparar(esperado, [dict(bien[0], dia="2026-03-05")])
    assert script.comparar(esperado, bien + [dict(bien[0], nombre="avena")])  # one too many


# == unit 007: one interpretation with every piece (weight, objective of the day, doubtful) ==

HOY7 = date(2026, 3, 10)  # a Tuesday


def _piezas(**campos):
    datos = {"alimentos": [], "peso": None, "objetivo_dia": None, "dudosas": []}
    datos.update(campos)
    return ListaInterpretada(**datos)


def _cliente_piezas(salida):
    cliente = MagicMock()
    cliente.messages.parse.return_value = SimpleNamespace(parsed_output=salida, stop_reason="end_turn")
    return cliente


def test_nuevo_dia_trae_todas_las_piezas():  # R6
    porridge = _item("porridge", cantidad=None, unidad=None, kcal=None, dia=HOY7)
    salida = _piezas(
        alimentos=[porridge],
        peso=PesoInterpretado(kg=70.2, dia=HOY7, nota=None),
        objetivo_dia=ObjetivoDiaInterpretado(tipo="descanso", porcentaje=None, niveles=None,
                                             entreno="30 min suaves", pasos=None),
    )
    cliente = _cliente_piezas(salida)
    texto = "nuevo día 70,2, descanso, 30 minutos suaves, desayuno porridge"

    resultado = InterpreteClaude(cliente=cliente, modelo="m").interpretar(texto, HOY7)

    assert resultado.peso.kg == 70.2 and resultado.peso.dia == HOY7
    assert resultado.objetivo_dia.tipo == "descanso" and resultado.objetivo_dia.entreno == "30 min suaves"
    assert [a.nombre for a in resultado.alimentos] == ["porridge"]
    assert resultado.alimentos[0].cantidad is None
    assert resultado.dudosas == []


def test_frase_dudosa_viene_aparte():  # R8
    salida = _piezas(peso=PesoInterpretado(kg=70.2, dia=HOY7, nota=None), dudosas=["lo del martes ya sabes"])
    resultado = InterpreteClaude(cliente=_cliente_piezas(salida), modelo="m").interpretar(
        "nuevo día 70,2 y lo del martes ya sabes", HOY7)
    assert resultado.dudosas == ["lo del martes ya sabes"]
    assert resultado.alimentos == []


def test_solo_peso_sin_alimentos():  # R10
    salida = _piezas(peso=PesoInterpretado(kg=70.2, dia=HOY7, nota="retención tras la cena"))
    resultado = InterpreteClaude(cliente=_cliente_piezas(salida), modelo="m").interpretar(
        "peso 70,2 retención tras la cena", HOY7)
    assert resultado.peso.nota == "retención tras la cena" and resultado.alimentos == []


def test_peticion_de_nuevo_dia_sigue_siendo_minima(monkeypatch):  # R9
    monkeypatch.delenv("TRIDATA_MODELO_TEXTO", raising=False)
    cliente = _cliente_piezas(_piezas(peso=PesoInterpretado(kg=70.2, dia=HOY7, nota=None)))
    InterpreteClaude(cliente=cliente).interpretar("peso 70,2", HOY7)
    kwargs = cliente.messages.parse.call_args.kwargs
    assert set(kwargs) == {"model", "max_tokens", "system", "messages", "output_format", "output_config"}
    assert kwargs["system"] == INSTRUCCION
    assert kwargs["messages"] == [
        {"role": "user", "content": "Hoy es martes, 2026-03-10.\n\nLo que ha escrito:\npeso 70,2"}]


def test_esquema_pide_todas_las_piezas_siempre():
    from anthropic.lib._parse._transform import transform_schema

    esquema = transform_schema(ListaInterpretada)
    assert set(esquema["required"]) == {"alimentos", "peso", "objetivo_dia", "dudosas"}
    for nombre in ("PesoInterpretado", "ObjetivoDiaInterpretado"):
        definicion = esquema["$defs"][nombre]
        assert set(definicion["required"]) == set(definicion["properties"])


def test_la_instruccion_explica_las_piezas():
    for regla in ("peso", "nuevo día", "objetivo_dia", "dudosas", "niveles", "pasos"):
        assert regla in INSTRUCCION
    assert re.search(r"\d{4}-\d{2}-\d{2}", INSTRUCCION) is None


# -- unit 007: the manual Haiku test also judges weight, objective and doubtful phrases --------

def test_dictados_de_piezas_entre_5_y_8_bien_formados():
    datos = _dictados()
    assert 20 <= len(datos["casos"]) <= 30  # the food ones are untouched
    assert 5 <= len(datos["piezas"]) <= 8
    for caso in datos["piezas"]:
        assert caso["texto"].strip()
        esperado = caso["esperado"]
        assert set(esperado) == {"peso", "objetivo_dia", "dudosas", "alimentos"}
        if esperado["peso"]:
            date.fromisoformat(esperado["peso"]["dia"])
            assert 30 <= esperado["peso"]["kg"] <= 250


def _como_si_claude_acertara_piezas(caso):
    e = caso["esperado"]
    peso = e["peso"] and PesoInterpretado(kg=e["peso"]["kg"], dia=date.fromisoformat(e["peso"]["dia"]),
                                          nota="nota inventada" if e["peso"].get("nota") else None)
    o = e["objetivo_dia"]
    objetivo = o and ObjetivoDiaInterpretado(
        tipo=o["tipo"][0] if o["tipo"] else None, porcentaje=o["porcentaje"], niveles=o["niveles"],
        entreno="algo suave" if o["entreno"] else None, pasos=o["pasos"])
    return ListaInterpretada(alimentos=_como_si_claude_acertara({"esperado": e["alimentos"]}), peso=peso or None, objetivo_dia=objetivo or None,
                             dudosas=["frase inventada"] * e["dudosas"])


def test_script_cuenta_aciertos_de_piezas_con_un_doble():
    script, datos = _script(), _dictados()
    interprete = MagicMock()
    interprete.interpretar.side_effect = lambda texto, hoy: _como_si_claude_acertara_piezas(
        next(c for c in datos["piezas"] if c["texto"] == texto))

    resultado = script.evaluar_piezas(interprete, datos)

    assert resultado.aciertos == resultado.total == len(datos["piezas"])
    assert interprete.interpretar.call_count == len(datos["piezas"])  # the 20-30 food cases are not called


def test_script_marca_fallo_en_las_piezas():
    script = _script()
    esperado = {"peso": {"kg": 70.2, "dia": "2026-03-04"},
                "objetivo_dia": {"tipo": ["descanso"], "porcentaje": None, "niveles": None, "entreno": True, "pasos": None},
                "dudosas": 0, "alimentos": []}
    bien = {"peso": {"kg": 70.2, "dia": "2026-03-04", "nota": None},
            "objetivo_dia": {"tipo": "descanso", "porcentaje": None, "niveles": None, "entreno": "30 min suaves",
                             "pasos": None},
            "dudosas": [], "alimentos": []}
    assert script.comparar_piezas(esperado, bien) == []
    assert script.comparar_piezas(esperado, {**bien, "peso": {**bien["peso"], "kg": 72.0}})
    assert script.comparar_piezas(esperado, {**bien, "peso": {**bien["peso"], "dia": "2026-03-03"}})
    assert script.comparar_piezas(esperado, {**bien, "peso": None})
    assert script.comparar_piezas(esperado, {**bien, "objetivo_dia": {**bien["objetivo_dia"], "tipo": "entreno"}})
    assert script.comparar_piezas(esperado, {**bien, "objetivo_dia": {**bien["objetivo_dia"], "entreno": None}})
    assert script.comparar_piezas(esperado, {**bien, "dudosas": ["nuevo día"]})
    assert script.comparar_piezas(esperado, {**bien, "alimentos": [{"nombre": "nuevo día", "cantidad": None,
                                                                     "unidad": None, "dia": "2026-03-04"}]})
    sin_objetivo = {**esperado, "objetivo_dia": None}
    vacio = {"tipo": None, "porcentaje": None, "niveles": None, "entreno": None, "pasos": None}
    assert script.comparar_piezas(sin_objetivo, {**bien, "objetivo_dia": vacio}) == []  # empty counts as absent


# == unit 011: what is saved (names and aliases only) and the manual test with recipes ==

HOY11 = date(2026, 10, 21)  # a Wednesday


def _recetario_con_numeros(tmp_path):
    """3 recipes, 2 fixed foods (one with the alias «guayaba») and 2 pots, with recognisable values."""
    from tridata.nutricion.recetas import Recetario

    rec = Recetario(tmp_path / "data.sqlite3")
    rec.crear_fijo({"nombre": "dulce de guayaba de ciclismo", "kcal": 93, "por": "unidad", "alias": ["guayaba"]})
    rec.crear_fijo({"nombre": "gel de prueba", "kcal": 107, "por": "unidad"})
    rec.crear_olla({"nombre": "olla exprés azul", "tara_g": 1537})
    rec.crear_olla({"nombre": "cazo verde", "tara_g": 733})
    for nombre in ("garbanzos de la abuela", "lasaña de la tía", "crema de calabaza inventada"):
        rec.crear_receta({"nombre": nombre, "ingredientes": [
            {"nombre": "harina misteriosa", "cantidad": 417, "unidad": "g", "kcal": 1459}],
            "peso_neto_g": 1871}, hoy=HOY11)
    return rec


def test_la_peticion_lleva_solo_nombres_y_alias(tmp_path):  # R5, C-47
    rec = _recetario_con_numeros(tmp_path)
    cliente = _cliente(_item("garbanzos de la abuela"))
    InterpreteClaude(cliente=cliente, modelo="m", guardados=rec.nombres).interpretar("dos guayabas", HOY11)
    kwargs = cliente.messages.parse.call_args.kwargs
    assert set(kwargs) == {"model", "max_tokens", "system", "messages", "output_format", "output_config"}
    assert kwargs["system"] == INSTRUCCION
    contenido = kwargs["messages"][0]["content"]
    assert contenido.startswith("Hoy es miércoles, 2026-10-21.")
    assert contenido.endswith("Lo que ha escrito:\ndos guayabas")
    for nombre in ("garbanzos de la abuela", "lasaña de la tía", "crema de calabaza inventada",
                   "dulce de guayaba de ciclismo", "gel de prueba", "guayaba"):
        assert f"«{nombre}»" in contenido
    peticion = repr(kwargs)
    for prohibido in ("harina", "olla", "cazo", "1537", "733", "93", "107", "417", "1459", "1871"):
        assert prohibido not in peticion
    sin_fecha = contenido.split("\n", 1)[1]
    assert re.search(r"\d", sin_fecha) is None  # no grams, kcal or tares anywhere


def test_sin_nada_guardado_la_peticion_es_la_de_siempre(tmp_path, monkeypatch):  # R5
    from tridata.nutricion.recetas import Recetario

    monkeypatch.delenv("TRIDATA_MODELO_TEXTO", raising=False)
    cliente = _cliente(_item("avena"))
    InterpreteClaude(cliente=cliente, guardados=Recetario(tmp_path / "data.sqlite3").nombres).interpretar("avena", HOY)
    assert cliente.messages.parse.call_args.kwargs["messages"] == [
        {"role": "user", "content": "Hoy es miércoles, 2026-03-04.\n\nLo que ha escrito:\navena"}]


def test_el_esquema_pide_guardado_y_coincidencia():  # R3, D1
    from anthropic.lib._parse._transform import transform_schema

    alimento = transform_schema(ListaInterpretada)["$defs"]["AlimentoInterpretado"]
    assert {"guardado", "coincidencia"} <= set(alimento["required"])
    assert _item("pan").guardado is None and _item("pan").coincidencia is None  # optional in Python
    for regla in ("guardado", "coincidencia", "exacta", "parecida", "receta"):
        assert regla in INSTRUCCION
    assert "no hay recetas ni alimentos guardados" not in INSTRUCCION


def _dictados_recetas():
    return json.loads((RAIZ / "tests" / "fixtures" / "dictados_recetas.json").read_text(encoding="utf-8"))


def test_dictados_de_recetas_bien_formados():  # R17
    datos = _dictados_recetas()
    date.fromisoformat(datos["hoy"])
    assert len(datos["casos"]) == 8
    assert len(datos["guardados"]["recetas"]) == 3 and len(datos["guardados"]["fijos"]) == 2
    for caso in datos["casos"]:
        assert caso["texto"].strip()
        for e in caso["esperado"]:
            assert set(e) == {"nombre", "guardado", "coincidencia", "cantidad", "unidad"}
            assert e["coincidencia"] in ("exacta", "parecida", None)
            assert (e["guardado"] is None) == (e["coincidencia"] is None)


def _como_si_claude_acertara_recetas(caso):
    return [_item(e["nombre"][0], cantidad=e["cantidad"], unidad=e["unidad"], dia=HOY11,
                  guardado=e["guardado"][0] if e["guardado"] else None, coincidencia=e["coincidencia"])
            for e in caso["esperado"]]


def test_script_de_recetas_cuenta_aciertos_con_un_doble():  # R17
    script, datos = _script(), _dictados_recetas()
    interprete = MagicMock()
    interprete.interpretar.side_effect = lambda texto, hoy: _como_si_claude_acertara_recetas(
        next(c for c in datos["casos"] if c["texto"] == texto))
    resultado = script.evaluar_recetas(interprete, datos)
    assert resultado.aciertos == resultado.total == 8
    assert sorted(script.nombres_guardados(datos)) == sorted([
        "garbanzos de la abuela", "lasaña de la tía", "crema de calabaza inventada", "dulce de guayaba de ciclismo",
        "guayaba", "gel de prueba"])


def test_script_de_recetas_marca_los_fallos():  # R17
    script = _script()
    esperado = [{"nombre": ["guayaba"], "guardado": ["guayaba", "dulce de guayaba de ciclismo"],
                 "coincidencia": "exacta", "cantidad": 2, "unidad": "unidad"}]
    bien = {"nombre": "dulce de guayaba", "cantidad": 2, "unidad": "unidades", "guardado": "Dulce de guayaba de ciclismo",
            "coincidencia": "exacta"}
    assert script.comparar_recetas(esperado, [bien]) == []
    assert script.comparar_recetas(esperado, [{**bien, "guardado": None, "coincidencia": None}])
    assert script.comparar_recetas(esperado, [{**bien, "coincidencia": "parecida"}])
    assert script.comparar_recetas(esperado, [{**bien, "cantidad": 3}])
    assert script.comparar_recetas(esperado, [{**bien, "unidad": "g"}])
    assert script.comparar_recetas(esperado, [bien, {**bien, "nombre": "guayaba"}])  # one too many
