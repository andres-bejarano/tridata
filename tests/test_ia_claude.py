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
    ListaInterpretada,
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
    lista = InterpreteClaude(cliente=cliente, modelo="m").interpretar("texto inventado", HOY)

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
