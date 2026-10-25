"""Unit 041 (REC-23 of `preguntar-equipo`): the team's roles and the catalogue of models, without the web.

- the four roles (`conversaciones/papeles.py`): name, icon, default model and the instructions of each one (R1, R6,
  R8, R9 · C-178, C-183, C-185, C-186);
- the catalogue of models (`ia/modelos.py`): the prices of the spending come from it (R7, G-56);
- the criterio portante (R2 · C-179): the request of every role carries the SAME fixed part, byte to byte, with the
  cache mark at its end, and the instructions of the role go after the mark; the tools are the same for all of them.

Every value is invented; the AI is a double (`ClaudeFalso`): no test spends.
"""
from __future__ import annotations

import json

import pytest

from test_conversaciones import BLOQUES, ClaudeFalso
from tridata.conversaciones import PRECIOS, dolares
from tridata.conversaciones.papeles import PAPELES, POR_DEFECTO, bloque
from tridata.ia.conversacion import ConversadorClaude, construir_peticion
from tridata.ia.modelos import MODELOS

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"


# -- R1 · C-178: the four roles ----------------------------------------------------------------------------------

def test_cuatro_papeles_con_su_nombre_icono_y_modelo_por_defecto():  # R1, R7 · S-51
    assert list(PAPELES) == ["entrenador", "fisio", "nutricionista", "asistente"]
    assert [p.nombre for p in PAPELES.values()] == ["Entrenador", "Fisio", "Nutricionista", "Asistente"]
    assert all(p.icono and p.descripcion and p.instrucciones for p in PAPELES.values())
    assert {k: p.modelo for k, p in PAPELES.items()} == {
        "entrenador": SONNET, "fisio": SONNET, "nutricionista": SONNET, "asistente": HAIKU}
    assert all(p.modelo in MODELOS for p in PAPELES.values())
    assert POR_DEFECTO == "asistente"  # the conversations of before


def test_cada_papel_mira_a_su_manera():  # R1
    instrucciones = {k: p.instrucciones for k, p in PAPELES.items()}
    assert len(set(instrucciones.values())) == 4
    assert "entrenador" in instrucciones["entrenador"].lower()
    assert "fisio" in instrucciones["fisio"].lower()
    assert "nutricionista" in instrucciones["nutricionista"].lower()


def test_el_entrenador_da_bloques_redondos_geles_y_cuando_cortar():  # R6 · C-183
    texto = PAPELES["entrenador"].instrucciones
    for frase in ("por bloques", "números redondos", "tiempo o distancia", "ritmo o esfuerzo", "recuperación",
                  "geles", "más de una hora", "cuándo cortar"):
        assert frase in texto, frase


@pytest.mark.parametrize("papel", list(PAPELES))
def test_todos_mandan_al_medico_ante_un_sintoma_de_alarma(papel):  # R8 · C-185, G-55
    texto = PAPELES[papel].instrucciones
    for frase in ("hinchazón", "calor", "rojez", "va a más en reposo", "médico antes de entrenar", "sin diagnosticar"):
        assert frase in texto, frase


@pytest.mark.parametrize("papel", list(PAPELES))
def test_la_semana_con_sus_datos_y_solo_cuando_lo_pide(papel):  # R9 · C-186, S-53
    texto = PAPELES[papel].instrucciones
    assert "opinión sobre su semana" in texto and "datos de esa semana" in texto
    assert "nunca escribes primero" in texto.lower()


@pytest.mark.parametrize("papel", list(PAPELES))
def test_nadie_dice_ya_esta_cambiado_sin_confirmar(papel):  # R5 · G-55
    assert "nunca digas que ya está cambiado" in PAPELES[papel].instrucciones.lower()


def test_el_bloque_del_papel_lleva_su_nombre_y_sus_instrucciones():  # R1, R3
    texto = bloque("fisio")
    assert "Fisio" in texto and PAPELES["fisio"].instrucciones in texto
    with pytest.raises(KeyError):
        bloque("masajista")


# -- R7 · G-56: the catalogue of models ------------------------------------------------------------------------

def test_el_catalogo_de_modelos_y_los_precios_salen_de_el():  # R7 · G-56
    assert set(MODELOS) == {HAIKU, SONNET}
    assert {m.proveedor for m in MODELOS.values()} == {"anthropic"}
    assert (MODELOS[HAIKU].nombre, MODELOS[SONNET].nombre) == ("Haiku", "Sonnet")
    assert all(m.descripcion for m in MODELOS.values())
    assert PRECIOS == {k: m.precios for k, m in MODELOS.items()}
    uso = {"entrada": 1000, "salida": 1000, "cache_escritura": 0, "cache_lectura": 0}
    assert dolares(SONNET, uso) == pytest.approx((1000 * 2 + 1000 * 10) / 1_000_000)
    assert dolares("modelo-fuera-del-catalogo", uso) == dolares(SONNET, uso)  # unknown: the dearest


# -- R2 · C-179: the criterio portante: one shared cache for the four roles -----------------------------------

def _fija(peticion: dict) -> str:
    """Everything up to the cache mark, byte to byte: what the cache keys on."""
    system = peticion["system"]
    marca = next(i for i, b in enumerate(system) if b.get("cache_control"))
    return json.dumps([peticion["tools"], system[:marca + 1]], ensure_ascii=False, sort_keys=True)


def test_la_parte_comun_es_identica_entre_papeles_y_el_papel_va_despues_de_la_marca():  # R2 · C-179
    peticiones = {k: construir_peticion(BLOQUES, [], "¿cómo planteas la tirada?", SONNET, papel=bloque(k))
                  for k in PAPELES}
    fijas = {k: _fija(p) for k, p in peticiones.items()}
    assert len(set(fijas.values())) == 1  # byte to byte, the four
    for k, p in peticiones.items():
        system = p["system"]
        assert [b["text"] for b in system[:len(BLOQUES)]] == list(BLOQUES)
        assert system[len(BLOQUES) - 1]["cache_control"] == {"type": "ephemeral"}  # the mark ends the common part
        assert system[-1]["text"] == bloque(k) and "cache_control" not in system[-1]  # the role, after it
        assert len(system) == len(BLOQUES) + 1
    assert len({json.dumps(p["tools"], sort_keys=True) for p in peticiones.values()}) == 1  # same tools for all


def test_preguntar_al_fisio_tras_el_entrenador_lee_la_cache():  # R2 · C-179
    claude = ClaudeFalso()
    conversador = ConversadorClaude(cliente=claude)
    primera = list(conversador.responder(BLOQUES, [], "¿la tirada?", modelo=SONNET, papel=bloque("entrenador")))[-1]
    segunda = list(conversador.responder(BLOQUES, [], "¿y el gemelo?", modelo=SONNET, papel=bloque("fisio")))[-1]
    assert primera.uso["cache_lectura"] == 0 and primera.uso["cache_escritura"] > 0
    assert segunda.uso["cache_lectura"] > 0 and segunda.uso["cache_escritura"] == 0
    assert claude.peticiones[1]["system"][-1]["text"] == bloque("fisio")
    assert segunda.modelo == SONNET
