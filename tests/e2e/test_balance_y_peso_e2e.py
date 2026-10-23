"""Unit 027: Balance and Peso in one tab, with a chart that chooses what to show and on which axis.

R1 the tabs and the order of Balance, `#peso` → Balance · R2 what to show (Peso · Las dos · Déficit) · R3 the
deficit in % of what was spent that day · R4 the choice is remembered · R5 the range with every view · R6 whole
kcal · R7 without any weight. Real Chromium at 390 × 844, light and dark; Claude, Whisper and «Actualizar Garmin»
are `MagicMock`s (conftest.py). Invented data only; «today» is Wednesday 2026-10-21.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright", reason="E2E needs the `e2e` extra: python -m pip install -e '.[dev,e2e]'")

from playwright.sync_api import expect  # noqa: E402

from tridata.models import DailyStats  # noqa: E402

HOY = date(2026, 10, 21)
LUNES = date(2026, 10, 19)
ANCHO = 390
TEMAS = pytest.mark.parametrize("oscuro", [False, True], ids=["claro", "oscuro"])
OBJETIVO_PESO = {"objetivo_peso": {"kg": 70.0, "fecha": (HOY + timedelta(days=130)).isoformat()}}
LEYENDA_DOS = "● peso · — media 7 d · ┄ objetivo 70,0 kg · ▮ déficit · ▮ superávit"
LEYENDA_PESO = "● peso · — media 7 d · ┄ objetivo 70,0 kg"
LEYENDA_DEFICIT = "▮ déficit · ▮ superávit"
LUNES_DE_AGOSTO = ["3 ago", "10 ago", "17 ago", "24 ago", "31 ago"]
SIN_ALMACEN = """(() => {
  const falla = () => { throw new DOMException("blocked", "SecurityError"); };
  Storage.prototype.getItem = falla; Storage.prototype.setItem = falla; Storage.prototype.removeItem = falla;
})();"""


def _textos(locator) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip() for t in locator.all_inner_texts()]


def _texto(locator) -> str:
    return re.sub(r"\s+", " ", locator.inner_text()).strip()


def _sin_scroll(p, pantalla: str) -> None:
    ancho = p.evaluate("document.documentElement.scrollWidth")
    assert ancho <= ANCHO, f"{pantalla}: horizontal scroll ({ancho}px)"


def _ejes(grafica) -> list[str]:
    return [t.strip() for t in grafica.locator(".titulo-eje").all_text_contents()]


def _ver(p, que: str) -> None:
    p.locator("#grafica-vista button", has_text=re.compile(rf"^{que}$")).click()


def _eje(p, que: str) -> None:
    p.locator("#grafica-eje button", has_text=que).click()


def _pulsado(p, grupo: str) -> list[str]:
    return _textos(p.locator(f"#{grupo} button[aria-pressed=true]"))


def _elegir(p, desde: str, hasta: str) -> None:
    p.locator("#balance-desde").fill(desde)
    p.locator("#balance-hasta").fill(hasta)
    p.locator("#balance-ver").click()


def _escala_pct(grafica) -> float:
    """Pixels per percentage point, measured on the labels of the % axis (two of them, through 0)."""
    marcas = {}
    for t in grafica.locator("text.eje").all():
        texto = t.text_content().strip()
        m = re.fullmatch(r"([+−]?)(\d+) %|0", texto)
        if m:
            v = 0 if texto == "0" else int(m.group(2)) * (-1 if m.group(1) == "−" else 1)
            marcas[v] = float(t.get_attribute("y"))
    assert 0 in marcas and len(marcas) >= 2, f"the % axis has no labels through 0: {marcas}"
    otro = next(v for v in marcas if v != 0)
    return abs(marcas[otro] - marcas[0]) / abs(otro)


def _a_la_vista(p, grafica) -> None:
    """For the screenshot: the whole chart above the box and the tabs (not just its first pixels)."""
    grafica.evaluate("(e) => e.scrollIntoView({ block: 'end' })")
    p.evaluate("window.scrollBy(0, document.getElementById('abajo').getBoundingClientRect().height + 8)")


def _semana_con_pesos(servidor) -> None:
    servidor.pesos_de_prueba()
    servidor.semana_de_prueba()


# -- R1: the tabs and the order of Balance ---------------------------------------------------------------

@TEMAS
def test_una_pestana_balance_con_todo_en_orden(arrancar, abrir, capturar, oscuro):  # R1
    servidor = arrancar(OBJETIVO_PESO)
    _semana_con_pesos(servidor)
    p = abrir(servidor, "/#peso", oscuro=oscuro)  # an old bookmark
    expect(p).to_have_url(servidor.url + "/#balance")
    expect(p.locator("section#balance")).to_be_visible()
    expect(p.locator("section#peso")).to_have_count(0)
    # unit 029: Balance is a part of Nutrición, the second tab of five
    assert _textos(p.locator("nav.pestanas .pestana")) == ["Hoy", "Nutrición", "Fitness", "Asistente", "Ajustes"]
    assert _textos(p.locator("#partes a")) == ["Día", "Balance", "Recetas"]
    expect(p.locator('.pestana[data-ir="nutricion"]')).to_have_attribute("aria-current", "page")
    expect(p.locator('#partes a[data-parte="balance"]')).to_have_attribute("aria-current", "page")

    contenido = p.locator("#balance-contenido")
    expect(contenido.locator("#balance-semana .fila-dia")).to_have_count(7)
    bloques = ["#peso-hoy", "#balance-semana", "#balance-grafica", "#balance-tus-semanas", "#balance-ultimos-pesos"]
    alturas = [contenido.locator(b).bounding_box()["y"] for b in bloques]
    assert alturas == sorted(alturas), f"the blocks of Balance are not in order: {dict(zip(bloques, alturas, strict=True))}"
    # the weight of today, as the Peso tab showed it
    tarjeta = p.locator("#peso-hoy")
    expect(p.locator("#peso-cabecera")).to_have_text("Peso de hoy · miércoles 21 oct")
    expect(tarjeta.locator("#peso-cifra")).to_contain_text("72,4")
    expect(tarjeta.locator("#peso-media")).to_contain_text("72,5 kg")
    expect(tarjeta.locator("#peso-media")).to_contain_text("2 días con peso")
    expect(tarjeta.locator("#peso-basal")).to_contain_text("1.600")
    expect(tarjeta.locator("#peso-objetivo")).to_contain_text("Objetivo 70,0 kg")
    expect(tarjeta.locator("#peso-objetivo")).to_contain_text("Te faltan 2,5 kg: hace falta")  # from the 7-day mean, 72,5
    # «Tus semanas» open, «Últimos pesos» folded and complete when opened
    expect(p.locator("#balance-tus-semanas")).to_have_attribute("open", "")
    expect(p.locator("#balance-semanas .semana").first).to_be_visible()
    expect(p.locator("#balance-ultimos-pesos")).not_to_have_attribute("open", "")
    expect(p.locator("#peso-ultimos")).to_be_hidden()
    p.locator("#balance-ultimos-pesos summary").click()
    expect(p.locator("#peso-ultimos")).to_be_visible()
    assert _textos(p.locator("#peso-ultimos .kcal")) == ["72,4 kg", "72,6 kg", "73,0 kg"]
    _sin_scroll(p, "balance")
    sufijo = "-oscuro" if oscuro else ""
    capturar(p, f"balance-arriba{sufijo}")
    capturar(p, f"balance-entera{sufijo}", completa=True)

    # Ajustes has the same five tabs (unit 029)
    p.locator(".pestana", has_text="Ajustes").click()
    assert _textos(p.locator("nav.pestanas .pestana")) == ["Hoy", "Nutrición", "Fitness", "Asistente", "Ajustes"]


# -- R2: what to show --------------------------------------------------------------------------------------

@TEMAS
def test_peso_deficit_y_las_dos(arrancar, abrir, capturar, oscuro):  # R2
    servidor = arrancar(OBJETIVO_PESO)
    servidor.historial_de_prueba(date(2026, 6, 1))
    sufijo = "-oscuro" if oscuro else ""
    p = abrir(servidor, "/#balance", oscuro=oscuro)
    tarjeta = p.locator("#balance-grafica")
    grafica = tarjeta.locator("svg")
    expect(grafica.locator(".barra-kcal")).to_have_count(28)
    assert _textos(p.locator("#grafica-vista button")) == ["Peso", "Las dos", "Déficit"]
    assert _textos(p.locator("#grafica-eje button")) == ["Déficit en kcal", "Déficit en %"]
    assert _pulsado(p, "grafica-vista") == ["Las dos"] and _pulsado(p, "grafica-eje") == ["Déficit en kcal"]
    for b in p.locator("#grafica-vista button, #grafica-eje button").all():
        assert b.bounding_box()["height"] >= 44
    expect(grafica.locator(".barra-kcal")).to_have_count(28)
    expect(grafica.locator("circle.punto-peso")).to_have_count(14)
    assert _ejes(grafica) == ["kg", "kcal"]

    _ver(p, "Peso")
    expect(grafica.locator(".barra-kcal")).to_have_count(0)
    expect(grafica.locator(".linea-cero")).to_have_count(0)
    expect(grafica.locator("circle.punto-peso")).to_have_count(14)
    expect(grafica.locator("polyline.linea-media")).to_have_count(1)
    expect(grafica.locator("line.linea-objetivo")).to_have_count(1)
    assert _ejes(grafica) == ["kg"]
    expect(p.locator("#grafica-eje")).to_be_hidden()
    expect(tarjeta.locator(".leyenda-grafica")).to_have_text(LEYENDA_PESO)
    assert _pulsado(p, "grafica-vista") == ["Peso"]
    expect(grafica.locator(".marca-x")).to_have_count(4)
    _sin_scroll(p, "solo peso")
    _a_la_vista(p, grafica)
    capturar(p, f"grafica-solo-peso{sufijo}")

    _ver(p, "Déficit")
    expect(grafica.locator("circle.punto-peso")).to_have_count(0)
    expect(grafica.locator("polyline.linea-media")).to_have_count(0)
    expect(grafica.locator("line.linea-objetivo")).to_have_count(0)
    expect(grafica.locator(".barra-kcal")).to_have_count(28)
    assert _ejes(grafica) == ["kcal"]
    expect(p.locator("#grafica-eje")).to_be_visible()
    expect(tarjeta.locator(".leyenda-grafica")).to_have_text(LEYENDA_DEFICIT)
    expect(grafica.locator(".marca-x")).to_have_count(4)
    _a_la_vista(p, grafica)
    capturar(p, f"grafica-solo-deficit{sufijo}")

    _ver(p, "Las dos")
    expect(grafica.locator(".barra-kcal")).to_have_count(28)
    expect(grafica.locator("circle.punto-peso")).to_have_count(14)
    assert _ejes(grafica) == ["kg", "kcal"]
    expect(tarjeta.locator(".leyenda-grafica")).to_have_text(LEYENDA_DOS)
    _a_la_vista(p, grafica)
    capturar(p, f"grafica-las-dos{sufijo}")


# -- R3: the deficit in % -----------------------------------------------------------------------------------

@TEMAS
def test_deficit_en_porcentaje(servidor, abrir, capturar, oscuro):  # R3
    servidor.apuntar("Comida del lunes", LUNES, None, 2000, hoy=LUNES)  # −500 over 2,500 spent
    servidor.store.save_daily_stats(DailyStats(stat_date=LUNES, steps=20000, total_calories=2500))
    servidor.apuntar("Comida del martes", LUNES + timedelta(days=1), None, 2150, hoy=LUNES + timedelta(days=1))
    servidor.cierres.al_dia(HOY)
    p = abrir(servidor, "/#balance", oscuro=oscuro)
    grafica = p.locator("#balance-grafica svg")
    lunes = grafica.locator(f"rect.barra-kcal[data-dia='{LUNES.isoformat()}']")
    expect(lunes).to_have_count(1)
    _ver(p, "Déficit")
    _eje(p, "Déficit en %")
    assert _pulsado(p, "grafica-eje") == ["Déficit en %"]
    assert _ejes(grafica) == ["%"]
    expect(lunes).to_have_attribute("data-valor", "-20")
    expect(lunes).to_have_class(re.compile(r"\bbarra-deficit\b"))
    expect(lunes.locator("title")).to_contain_text("−20 %")
    alto = float(lunes.get_attribute("height"))
    assert abs(alto - 20 * _escala_pct(grafica)) < 1.5, f"−20 % drawn {alto}px high"
    expect(p.locator("#balance-grafica .leyenda-grafica")).to_have_text(
        "▮ déficit · ▮ superávit · en % de lo gastado ese día")
    _sin_scroll(p, "deficit en %")
    _a_la_vista(p, grafica)
    capturar(p, "grafica-deficit-pct" + ("-oscuro" if oscuro else ""))
    _ver(p, "Las dos")
    assert _ejes(grafica) == ["kg", "%"]
    expect(lunes).to_have_attribute("data-valor", "-20")


def test_dia_sin_porcentaje_no_pinta_barra(servidor, abrir):  # R3
    servidor.apuntar("Comida del lunes", LUNES, None, 2000, hoy=LUNES)
    servidor.store.save_daily_stats(DailyStats(stat_date=LUNES, steps=20000, total_calories=2500))
    servidor.apuntar("Comida del martes", LUNES + timedelta(days=1), None, 2150, hoy=LUNES + timedelta(days=1))
    servidor.cierres.al_dia(HOY)

    def sin_pct_el_martes(ruta):
        respuesta = ruta.fetch()
        datos = respuesta.json()
        for b in datos.get("barras", []):
            if b["dia"] == (LUNES + timedelta(days=1)).isoformat():
                b["balance_pct"] = None
        ruta.fulfill(response=respuesta, body=json.dumps(datos), headers={"content-type": "application/json"})

    p = abrir(servidor, "/#balance", antes=lambda pagina: pagina.route(re.compile(r".*/api/balance\?.*"), sin_pct_el_martes))
    grafica = p.locator("#balance-grafica svg")
    martes = grafica.locator(f"rect.barra-kcal[data-dia='{(LUNES + timedelta(days=1)).isoformat()}']")
    expect(martes).to_have_count(1)  # in kcal it is there
    _eje(p, "Déficit en %")
    expect(grafica.locator(f"rect.barra-kcal[data-dia='{LUNES.isoformat()}']")).to_have_attribute("data-valor", "-20")
    expect(martes).to_have_count(0)  # no invented 0


def test_por_semanas_usa_el_porcentaje_de_la_semana(servidor, abrir):  # R3
    servidor.historial_de_prueba(date(2026, 6, 1))
    p = abrir(servidor, "/#balance")
    grafica = p.locator("#balance-grafica svg")
    _eje(p, "Déficit en %")
    p.locator("#balance-rangos button", has_text="Todo").click()
    expect(grafica.locator(".barra-kcal")).to_have_count(21)
    semanas = p.request.get(servidor.url + "/api/balance?rango=todo").json()["barras"]
    esperado = {b["dia"]: b["balance_pct"] for b in semanas}
    dibujado = {r.get_attribute("data-dia"): float(r.get_attribute("data-valor"))
                for r in grafica.locator(".barra-kcal").all()}
    assert dibujado == esperado
    assert _ejes(grafica) == ["kg", "%"]


# -- R4: the choice is remembered ------------------------------------------------------------------------------

def test_recuerda_lo_elegido(servidor, abrir, ir_a):  # R4
    servidor.historial_de_prueba(date(2026, 9, 1))
    p = abrir(servidor, "/#balance")
    _ver(p, "Peso")
    ir_a(p, "dia")
    expect(p.locator("section#dia")).to_be_visible()
    ir_a(p, "balance")
    expect(p.locator("#balance-grafica svg")).to_have_count(1)
    assert _pulsado(p, "grafica-vista") == ["Peso"]
    expect(p.locator("#balance-grafica svg .barra-kcal")).to_have_count(0)

    _ver(p, "Déficit")
    _eje(p, "Déficit en %")
    p.reload()
    expect(p.locator("#balance-grafica svg")).to_have_count(1)
    assert _pulsado(p, "grafica-vista") == ["Déficit"]
    assert _pulsado(p, "grafica-eje") == ["Déficit en %"]
    assert _ejes(p.locator("#balance-grafica svg")) == ["%"]


def test_sin_almacenamiento_arranca_en_las_dos_y_kcal(servidor, abrir):  # R4
    servidor.historial_de_prueba(date(2026, 9, 1))
    p = abrir(servidor, "/#balance", antes=lambda pagina: pagina.add_init_script(SIN_ALMACEN))
    grafica = p.locator("#balance-grafica svg")
    expect(grafica).to_have_count(1)
    assert _pulsado(p, "grafica-vista") == ["Las dos"]
    assert _pulsado(p, "grafica-eje") == ["Déficit en kcal"]
    _ver(p, "Peso")  # it still works for this visit
    assert _ejes(grafica) == ["kg"]
    # (the fixture fails the test if the page threw)


# -- R5: the range with every view and both axes -------------------------------------------------------------

@pytest.mark.parametrize(("vista", "eje"), [("Peso", None), ("Déficit", "kcal"), ("Déficit", "%"),
                                            ("Las dos", "kcal"), ("Las dos", "%")])
def test_rango_en_cada_vista(servidor, abrir, vista, eje):  # R5
    servidor.historial_de_prueba(date(2026, 6, 1))
    p = abrir(servidor, "/#balance")
    grafica = p.locator("#balance-grafica svg")
    _ver(p, vista)
    if eje:
        _eje(p, f"Déficit en {eje}")
    barras = 0 if vista == "Peso" else None
    p.locator("#balance-rangos button", has_text="3 meses").click()
    expect(grafica.locator(".marca-x")).to_have_count(13)
    if barras is None:
        expect(grafica.locator(".barra-kcal")).to_have_count(91)
    else:
        expect(grafica.locator(".barra-kcal")).to_have_count(0)
        expect(grafica.locator("circle.punto-peso")).not_to_have_count(0)
    p.locator("#balance-fechas-boton").click()
    _elegir(p, "2026-08-01", "2026-09-01")
    expect(p.locator("#balance-periodo")).to_have_text("del 1 ago al 1 sept")
    assert [t.strip() for t in grafica.locator(".etiqueta-x").all_text_contents()] == LUNES_DE_AGOSTO
    expect(grafica.locator(".barra-kcal")).to_have_count(32 if barras is None else 0)
    assert _pulsado(p, "grafica-vista") == [vista]
    p.locator("#balance-rangos button", has_text="4 semanas").click()
    expect(grafica.locator(".marca-x")).to_have_count(4)
    expect(grafica.locator(".barra-kcal")).to_have_count(28 if barras is None else 0)
    if eje:
        assert _pulsado(p, "grafica-eje") == [f"Déficit en {eje}"]
    _sin_scroll(p, f"{vista} {eje}")


# -- R6: whole kcal with the thousands dot -------------------------------------------------------------------

def _decimales_que_no_son_kg_ni_pct(texto: str) -> list[str]:
    """Every number with a decimal comma that is not followed by «kg» or «%» (nor the «peso 72,4» to say): none may
    be kcal."""
    return [m.group(0) for m in re.finditer(r"(?<![\d.,])(?<!peso )[+−-]?\d[\d.]*,\d+(?!\d)(?!\s*(?:kg|%))", texto)]


def _miles_sin_punto(texto: str) -> list[str]:
    return [m.group(0) for m in re.finditer(r"(?<![\d.,])\d{4,}(?![\d])", texto) if not re.fullmatch(r"20\d\d", m.group(0))]


def test_kcal_enteras_con_punto_de_millar(arrancar, abrir, ir_a):  # R6
    servidor = arrancar({**OBJETIVO_PESO, "edad": 38, "altura_cm": 178.5})
    martes = LUNES + timedelta(days=1)
    servidor.ajustes.cambiar({"limite_saludable_pct": 25}, desde=date(1, 1, 1))
    servidor.apuntar("Comida del lunes", LUNES, None, 590.6, hoy=LUNES)
    servidor.store.save_daily_stats(DailyStats(stat_date=LUNES, steps=20000, total_calories=2530.4))
    servidor.apuntar("Comida del martes", martes, None, 2150.35, hoy=martes)
    servidor.apuntar("Avena", HOY, 60, 228.7, estimado=True)
    servidor.apuntar("Arroz con pollo", HOY, 350, 532.45)
    servidor.store.save_daily_stats(DailyStats(stat_date=HOY, steps=6543, total_calories=None))
    servidor.pesos_de_prueba()
    servidor.cierres.al_dia(HOY)
    p = abrir(servidor, "/#balance")
    expect(p.locator("#balance-semana .fila-dia")).to_have_count(7)
    p.locator("#balance-ultimos-pesos summary").click()
    expect(p.locator("#peso-ultimos")).to_be_visible()
    texto = p.locator("#balance-contenido").inner_text()
    titulos = " | ".join(t.strip() for t in p.locator("#balance-grafica svg title, #balance-grafica svg text").all_text_contents())
    for que, t in (("Balance", texto), ("la gráfica", titulos)):
        assert _decimales_que_no_son_kg_ni_pct(t) == [], f"{que}: kcal with decimals"
        assert _miles_sin_punto(t) == [], f"{que}: kcal of 4 figures without the thousands dot"
    expect(p.locator("#balance-semana .fila-dia").nth(0)).to_contain_text("−1.940 kcal")
    assert re.search(r"Basal\s+[\d.]+\s", p.locator("#peso-basal").inner_text())

    ir_a(p, "dia")
    expect(p.locator("#queda-cifra")).not_to_have_text("—")
    barra = " ".join([p.locator("#queda").inner_text(), p.locator("#cierre").inner_text()])
    assert _decimales_que_no_son_kg_ni_pct(barra) == [], "Hoy: kcal with decimals"
    assert _miles_sin_punto(barra) == [], "Hoy: kcal of 4 figures without the thousands dot"


# -- R7: without any weight ----------------------------------------------------------------------------------

@TEMAS
def test_sin_ningun_peso(servidor, abrir, capturar, oscuro):  # R7
    servidor.semana_de_prueba()
    p = abrir(servidor, "/#balance", oscuro=oscuro)
    expect(p.locator("#peso-hoy")).to_contain_text("Aún no hay pesos. Para apuntar el de hoy, di o escribe «peso")
    expect(p.locator("#peso-cifra")).to_have_text("—")
    expect(p.locator("#balance-semana .fila-dia")).to_have_count(7)
    grafica = p.locator("#balance-grafica svg")
    expect(grafica.locator(".barra-kcal")).to_have_count(3)
    _ver(p, "Peso")
    expect(grafica).to_contain_text("Aún no hay pesos")
    expect(grafica.locator("circle.punto-peso")).to_have_count(0)
    _ver(p, "Déficit")
    expect(grafica.locator(".barra-kcal")).to_have_count(3)
    expect(p.locator("#balance-ultimos-pesos")).to_have_count(0)
    _sin_scroll(p, "sin pesos")
    capturar(p, "balance-sin-pesos" + ("-oscuro" if oscuro else ""))
