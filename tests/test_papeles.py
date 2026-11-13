"""Unit 041 (REC-23 of `preguntar-equipo`): the team's roles and the catalogue of models, without the web.

- the four roles (`conversaciones/papeles.py`): name, icon, default model and the instructions of each one (R1, R6,
  R8, R9 · C-178, C-183, C-185, C-186);

Unit 083 (P-20261113-33e057c3): the catalogue of models, its prices and the requests to the API are gone with the paid
AI, and their tests with them (`hallazgos.md`); `conversaciones/papeles.py` stays (outside that unit).
"""
from __future__ import annotations

import pytest

from tridata.conversaciones.papeles import PAPELES, POR_DEFECTO, bloque

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"


# -- R1 · C-178: the four roles ----------------------------------------------------------------------------------

def test_cuatro_papeles_con_su_nombre_icono_y_modelo_por_defecto():  # R1, R7 · S-51
    assert list(PAPELES) == ["entrenador", "fisio", "nutricionista", "asistente"]
    assert [p.nombre for p in PAPELES.values()] == ["Entrenador", "Fisio", "Nutricionista", "Asistente"]
    assert all(p.icono and p.descripcion and p.instrucciones for p in PAPELES.values())
    assert {k: p.modelo for k, p in PAPELES.items()} == {
        "entrenador": SONNET, "fisio": SONNET, "nutricionista": SONNET, "asistente": HAIKU}
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
