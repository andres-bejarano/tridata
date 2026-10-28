"""Unit 039 in a real Chromium at 390 × 844, light and dark: the Asistente shows what the month has cost so far.

«Gasto del mes: X $ de 15 $» (5 $ until unit 041) and a bar (`<meter>`) over the list of conversations, from `/api/conversaciones`
(`tope.gastado`, `tope.dolares`, R9 of unit 032). Empty at 0 $, «< 0,01 $» for crumbs, the warning colour from 80 %
(the meter's `high` region), full at the cap with the notice of always; up to date when coming back to the list.

Uses the server of `test_conversaciones_e2e.py` (Claude doubled: no API, no cost) and sows invented spending.
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402
from test_conversaciones_e2e import AHORA, PRIMERO, TEMAS, ServidorAsistente  # noqa: E402

from tridata.conversaciones import Conversaciones  # noqa: E402

SALIDA_HAIKU_POR_DOLAR = 2_000_000  # 0.50 $ per million tokens out
SALIDA_SONNET_POR_DOLAR = 100_000  # 10 $ per million tokens out


@pytest.fixture
def servidor(tmp_path):
    s = ServidorAsistente(tmp_path / "asistente")
    s.db = tmp_path / "asistente" / "data.sqlite3"
    yield s
    s.parar()


def _gastar(servidor, dolares: float) -> None:
    """Invented spending of this month, with Sonnet's price (Haiku for the crumbs)."""
    modelo, por_dolar = (("claude-haiku-5-5", SALIDA_HAIKU_POR_DOLAR) if dolares < 0.5
                         else ("claude-sonnet-5-5", SALIDA_SONNET_POR_DOLAR))
    uso = {"entrada": 0, "salida": round(dolares * por_dolar), "cache_escritura": 0, "cache_lectura": 0}
    Conversaciones(servidor.db).apuntar_gasto(AHORA, modelo, uso, "mensaje")


@pytest.mark.parametrize(("dolares", "cifra", "aviso"), [
    (0, "0,00 $", False),
    (0.005, "< 0,01 $", False),
    (0.04, "0,04 $", False),
    (12.6, "12,60 $", False),
    (15, "15,00 $", True),
], ids=["cero", "migajas", "poco", "aviso", "tope"])
def test_la_barra_dice_lo_gastado_del_mes(servidor, abrir, dolares, cifra, aviso):  # R1 · R2
    if dolares:
        _gastar(servidor, dolares)
    p = abrir(servidor, "/#asistente", esperar=False)
    gasto = p.locator("#conv-gasto")
    expect(gasto).to_be_visible()
    expect(gasto).to_have_text(f"Gasto del mes: {cifra} de 15 $")
    barra = p.locator("#conv-gasto-barra")
    expect(barra).to_be_visible()
    assert float(barra.get_attribute("max")) == 15
    assert float(barra.get_attribute("value")) == pytest.approx(min(dolares, 15), abs=0.001)
    assert float(barra.get_attribute("high")) == pytest.approx(12)  # from 80 % of the cap: the warning colour
    expect(p.locator("#conv-tope")).to_be_visible() if aviso else expect(p.locator("#conv-tope")).to_be_hidden()


def test_por_encima_del_tope_la_barra_no_pasa_de_llena(servidor, abrir):  # R2
    _gastar(servidor, 18.9)
    p = abrir(servidor, "/#asistente", esperar=False)
    expect(p.locator("#conv-gasto")).to_have_text("Gasto del mes: 18,90 $ de 15 $")
    assert float(p.locator("#conv-gasto-barra").get_attribute("value")) == 15


@TEMAS
def test_se_pone_al_dia_al_volver_a_la_lista(servidor, abrir, capturar, oscuro):  # R1
    p = abrir(servidor, "/#asistente", oscuro=oscuro, esperar=False)
    expect(p.locator("#conv-gasto")).to_have_text("Gasto del mes: 0,00 $ de 15 $")
    capturar(p, f"039-barra-cero-{'oscuro' if oscuro else 'claro'}")
    p.locator("#conv-nueva").click()
    p.locator("#texto").fill(PRIMERO)
    p.locator("#enviar").click()
    expect(p.locator("#conv-chat")).to_contain_text("Vas bien encaminado")
    # the first words come in pieces; the spending is noted when the answer ends, and only then «Pedir opinión a…»
    expect(p.locator("#conv-opinion")).to_be_visible()
    p.evaluate("location.hash = 'asistente'")
    expect(p.locator("#conv-gasto")).to_have_text("Gasto del mes: < 0,01 $ de 15 $")
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= 390, f"horizontal scroll: {ancho} px"
    capturar(p, f"039-barra-tras-pregunta-{'oscuro' if oscuro else 'claro'}")
