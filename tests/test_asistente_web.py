"""The assistant that acts (unit 021) through the web: interpret and dictate bring the cards and save nothing;
confirm, discard, correct and redo-if-changed with Starlette's TestClient over a temporary SQLite.

The Claude interpreter is a `MagicMock` that returns the `cambios` of each case (no API, no cost). Plans
`pedir-al-asistente` (REC-12): R1-R2, R4, R5, R7-R12 of the unit · R-65, R-70..R-73, R-78, S-20, G-37, G-41.
Every value is invented. Today is Thursday 2026-10-22: 8,000 default steps, 0.04 kcal per step, 2,300 kcal.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.ia import AlimentoInterpretado, CambioInterpretado, ListaInterpretada
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas
from tridata.nutricion.recetas import Recetario
from tridata.web import crear_app

HOY = date(2026, 10, 22)
AYER = HOY - timedelta(days=1)
AUDIO = b"\x00\x00\x00\x18ftypM4A audio de prueba"
TABLAS = ("alimento_apuntado", "interpretacion", "peso_dia", "objetivo_dia", "alimento_fijo", "receta", "olla",
          "tarjeta_asistente")


class Web:
    def __init__(self, tmp_path):
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1980, "kcal_por_paso": 0.04, "pasos_por_defecto": 8000},
                             desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.recetario = Recetario(self.db)
        self.recetario.crear_fijo({"nombre": "queso mozzarella de prueba", "kcal": 280, "por": "100g"})
        self.recetario.crear_olla({"nombre": "olla grande", "tara_g": 1500})
        self.interprete = MagicMock()
        self.transcriptor = MagicMock(spec=["transcribir"])
        self.hoy = HOY
        self.cliente = TestClient(crear_app(self.registro, self.interprete, self.ajustes, hoy=lambda: self.hoy,
                                            objetivos=self.objetivos, recetario=self.recetario,
                                            transcriptor=self.transcriptor))
        self.yogur = self.apuntar("yogur natural de prueba", 125, 75, "10:00")

    def apuntar(self, nombre, cantidad, kcal, hora, dia=HOY):
        fila = self.registro.guardar([Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad="g", kcal=kcal)],
                                     hoy=HOY, texto=nombre, sin_cambios=True)[0]
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE alimento_apuntado SET creado = ? WHERE id = ?", (f"{dia.isoformat()}T{hora}:00", fila["id"]))
        return fila["id"]

    def claude(self, *cambios, alimentos=()):
        self.interprete.interpretar.side_effect = lambda texto, hoy: ListaInterpretada(
            alimentos=list(alimentos), cambios=list(cambios))

    def interpretar(self, texto, **extra):
        r = self.cliente.post("/api/interpretar", json={"texto": texto, **extra})
        assert r.status_code == 200, r.text
        return r.json()

    def cuentas(self):
        with sqlite3.connect(self.db) as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLAS}  # noqa: S608

    def tarjetas(self):
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(f) for f in conn.execute("SELECT * FROM tarjeta_asistente ORDER BY id")]

    def pasos(self, dia=HOY):
        return self.cliente.get(f"/api/dia?fecha={dia.isoformat()}").json()["gasto"]["pasos"]

    def post(self, ruta, cuerpo):
        return self.cliente.post(ruta, json=cuerpo)


@pytest.fixture
def w(tmp_path):
    return Web(tmp_path)


PASOS = CambioInterpretado(accion="pasos_dia", valor=15000, dia=HOY, antes_dicho="8000")


def _tarjeta_de_pasos(w):
    w.claude(PASOS)
    (t,) = w.interpretar("hoy en vez de 8 mil pasos son 15 mil")["cambios"]
    return t


# -- R1 · R-64: the card, without changing anything ----------------------------------------------------------------

def test_interpretar_trae_la_tarjeta_y_no_guarda_nada(w):  # R1 · C-82
    antes = w.cuentas()
    t = _tarjeta_de_pasos(w)
    assert t["tipo"] == "propuesta"
    assert t["texto"] == "Voy a cambiar los pasos previstos del 22-10: 8.000 → 15.000"
    assert t["donde"] == "solo ese día, los pasos por defecto siguen en 8.000"
    assert w.pasos() == 8000
    assert w.cuentas() == antes and antes["tarjeta_asistente"] == 0


def test_sin_cambios_la_interpretacion_trae_la_lista_vacia(w):
    w.claude()
    assert w.interpretar("un yogur")["cambios"] == []
    w.interprete.interpretar.side_effect = None
    w.interprete.interpretar.return_value = []  # an interpreter that only knows foods
    assert w.interpretar("un yogur")["cambios"] == []


# -- R2 · R-65, C-83: confirming is like by hand ---------------------------------------------------------------------

def test_confirmar_aplica_como_a_mano(w):  # R2
    t = _tarjeta_de_pasos(w)
    antes = w.cliente.get("/api/dia").json()
    r = w.post("/api/asistente/confirmar", {"tarjeta": t})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["hecho"].startswith("Hecho: los pasos previstos del 22-10 son 15.000.")
    assert "te quedan" in cuerpo["hecho"]
    assert cuerpo["dia"]["fecha"] == "2026-10-22" and cuerpo["dia"]["gasto"]["kcal"] == 2580
    assert w.objetivos.del_dia(HOY)["pasos"] == 15000 and w.pasos() == 15000
    mant = lambda d: next(m["kcal"] for m in d["margenes"] if m["nivel"] == 0)  # noqa: E731
    assert round(mant(cuerpo["dia"]) - mant(antes)) == 280
    (fila,) = w.tarjetas()
    assert (fila["estado"], fila["accion"], fila["antes"], fila["despues"], fila["dia"], fila["corregida"]) == (
        "aplicada", "pasos_dia", "8.000", "15.000", "2026-10-22", 0)
    assert fila["texto_peticion"] == "hoy en vez de 8 mil pasos son 15 mil" and fila["fecha_peticion"] == "2026-10-22"
    assert fila["toca"] == t["texto"]


def test_a_mano_los_pasos_tambien_se_pueden_cambiar_y_volver(w):  # R12: «a mano», the same method
    assert w.post("/api/dia/objetivo", {"pasos": 12000}).status_code == 200
    assert w.pasos() == 12000
    assert w.post("/api/dia/objetivo", {"pasos": 80000}).status_code == 400
    assert w.post("/api/dia/objetivo", {"pasos": None}).status_code == 200
    assert w.pasos() == 8000


def test_dictado_trae_la_tarjeta_y_no_guarda_nada(w):  # R2, R11 · R-73
    w.claude(CambioInterpretado(accion="pasos_dia", valor=15000))
    w.transcriptor.transcribir.return_value = "hoy son 15 mil pasos"
    antes = w.cuentas()
    r = w.cliente.post(f"/api/dictado?id=11111111-2222-4333-8444-555555555555&fecha={HOY}", content=AUDIO,
                       headers={"Content-Type": "audio/mp4"})
    assert r.status_code == 200, r.text
    (t,) = r.json()["interpretacion"]["cambios"]
    assert t["texto"] == "Voy a cambiar los pasos previstos del 22-10: 8.000 → 15.000"
    assert t["peticion"] == {"texto": "hoy son 15 mil pasos", "fecha": "2026-10-22"}
    assert w.cuentas() == antes and w.pasos() == 8000  # the list waits on the phone until it is confirmed


def test_una_grabacion_de_ayer_es_del_dia_de_la_grabacion(w):  # R3, R11 · G-41
    w.claude(CambioInterpretado(accion="quitar_alimento", objeto="manzana"))
    w.apuntar("manzana de prueba", 150, 80, "11:00", dia=AYER)
    w.transcriptor.transcribir.return_value = "quita la manzana"
    r = w.cliente.post(f"/api/dictado?id=11111111-2222-4333-8444-555555555555&fecha={AYER}", content=AUDIO,
                       headers={"Content-Type": "audio/mp4"})
    (t,) = r.json()["interpretacion"]["cambios"]
    assert t["texto"] == "Voy a quitar del 21-10: manzana de prueba 150 g (80 kcal), 11:00"
    assert w.interprete.interpretar.call_args.args[1] == AYER


# -- R4 · R-67: the «before» comes from the base; what was logged keeps its kcal ------------------------------------

def test_corregir_un_ingrediente_conserva_lo_apuntado(w):  # R4 · C-85
    con_queso = w.apuntar("queso mozzarella de prueba", 50, 140, "13:00")
    w.claude(CambioInterpretado(accion="guardar_ingrediente", objeto="queso mozzarella de prueba", valor=290,
                                unidad="g"))
    (t,) = w.interpretar("el queso mozzarella de prueba son 290 por cada 100")["cambios"]
    assert t["texto"] == "Voy a corregir queso mozzarella de prueba en Ingredientes: 280 → 290 kcal/100 g"
    assert w.post("/api/asistente/confirmar", {"tarjeta": t}).status_code == 200
    fijo = next(f for f in w.recetario.fijos() if f["nombre"] == "queso mozzarella de prueba")
    assert fijo["kcal"] == 290
    assert w.registro.alimento(con_queso)["kcal"] == 140  # R-85


# -- R5 · R-80, C-104: a new ingredient, then used without estimating -----------------------------------------------

def test_ingrediente_nuevo_aparece_y_se_usa_sin_estimar(w):  # R5
    w.claude(CambioInterpretado(accion="guardar_ingrediente", objeto="queso de prueba nuevo", valor=300, unidad="g"))
    (t,) = w.interpretar("queso de prueba nuevo son 300 por cada 100")["cambios"]
    assert t["texto"] == "Voy a guardar: queso de prueba nuevo 300 kcal/100 g en Ingredientes"
    assert w.post("/api/asistente/confirmar", {"tarjeta": t}).status_code == 200
    listado = w.cliente.get("/api/recetas").json()
    assert any(f["nombre"] == "queso de prueba nuevo" and f["kcal"] == 300 and f["por"] == "100g"
               for f in listado["fijos"])
    w.claude(alimentos=[AlimentoInterpretado(
        nombre="queso de prueba nuevo", cantidad=50, unidad="g", dia=HOY, kcal=170, proteina_g=None, hidratos_g=None,
        grasa_g=None, estimado=True, dudoso=False, supuestos=[], guardado="queso de prueba nuevo",
        coincidencia="exacta")])
    (a,) = w.interpretar("50 g de queso de prueba nuevo")["alimentos"]
    assert a["kcal"] == 150 and a["estimado"] is False


# -- R7 · R-69, G-37: choosing between two; the card is the confirmation ---------------------------------------------

def test_elegir_entre_dos_y_quitar_solo_ese(w):  # R7 · C-88
    tarde = w.apuntar("yogur natural de prueba", 125, 75, "17:30")
    w.claude(CambioInterpretado(accion="quitar_alimento", objeto="yogur"))
    (e,) = w.interpretar("quita el yogur")["cambios"]
    assert e["tipo"] == "elegir" and [c["texto"][-5:] for c in e["candidatos"]] == ["10:00", "17:30"]
    r = w.post("/api/asistente/rehacer", {"tarjeta": e, "eleccion": tarde})
    assert r.status_code == 200
    t = r.json()["tarjeta"]
    assert t["texto"] == "Voy a quitar del 22-10: yogur natural de prueba 125 g (75 kcal), 17:30"
    r = w.post("/api/asistente/confirmar", {"tarjeta": t})  # no {"confirmado": true}: the card is it
    assert r.status_code == 200, r.text
    assert [a["id"] for a in w.registro.apuntes_del_dia(HOY)] == [w.yogur]


# -- R8 · R-70: discarding changes nothing ---------------------------------------------------------------------------

def test_descartar_no_cambia_nada_y_lo_apunta(w):  # R8 · C-90
    t = _tarjeta_de_pasos(w)
    dia_antes = w.cliente.get("/api/dia").json()
    r = w.post("/api/asistente/descartar", {"tarjeta": t})
    assert r.status_code == 200
    assert w.pasos() == 8000 and w.objetivos.del_dia(HOY) is None
    assert w.cliente.get("/api/dia").json()["margenes"] == dia_antes["margenes"]
    (fila,) = w.tarjetas()
    assert (fila["estado"], fila["antes"], fila["despues"]) == ("descartada", "8.000", "15.000")


# -- R9 · R-71: correcting redoes and rechecks the card -------------------------------------------------------------

def test_corregir_rehace_comprueba_y_confirma(w):  # R9 · C-91
    w.recetario.borrar_fijo(w.recetario.fijos()[0]["id"], confirmado=True)  # no «queso mozzarella de prueba» saved
    w.claude(CambioInterpretado(accion="guardar_ingrediente", objeto="queso mozzarella de prueba", valor=280,
                                unidad="g"))
    (t,) = w.interpretar("queso mozzarella de prueba son 280 por cada 100")["cambios"]
    assert t["texto"] == "Voy a guardar: queso mozzarella de prueba 280 kcal/100 g en Ingredientes"
    r = w.post("/api/asistente/rehacer", {"tarjeta": t, "valores": {"kcal": 1280}})
    assert r.status_code == 200
    mala = r.json()["tarjeta"]
    assert mala["error"] == "nada pasa de 900 kcal/100 g" and mala["corregida"] is True
    r = w.post("/api/asistente/confirmar", {"tarjeta": mala})
    assert r.status_code == 422 and r.json()["error"] == "nada pasa de 900 kcal/100 g"
    assert not any(f["nombre"] == "queso mozzarella de prueba" for f in w.recetario.fijos())
    buena = w.post("/api/asistente/rehacer", {"tarjeta": mala, "valores": {"kcal": "285"}}).json()["tarjeta"]
    assert buena["error"] is None and buena["texto"].endswith("285 kcal/100 g en Ingredientes")
    assert w.post("/api/asistente/confirmar", {"tarjeta": buena}).status_code == 200
    assert next(f for f in w.recetario.fijos() if f["nombre"] == "queso mozzarella de prueba")["kcal"] == 285
    (fila,) = w.tarjetas()
    assert (fila["estado"], fila["corregida"]) == ("aplicada", 1)


# -- R10 · R-72: several at once, and next to a food ----------------------------------------------------------------

def test_dos_tarjetas_cada_una_por_su_cuenta(w):  # R10 · C-92
    w.claude(CambioInterpretado(accion="quitar_alimento", objeto="yogur"), PASOS)
    yogur, pasos = w.interpretar("quita el yogur y hoy son 15 mil pasos")["cambios"]
    assert (yogur["accion"], pasos["accion"]) == ("quitar_alimento", "pasos_dia")
    assert w.post("/api/asistente/confirmar", {"tarjeta": pasos}).status_code == 200
    assert w.post("/api/asistente/descartar", {"tarjeta": yogur}).status_code == 200
    assert w.pasos() == 15000 and [a["id"] for a in w.registro.apuntes_del_dia(HOY)] == [w.yogur]


def test_comida_y_cambio_juntos_confirmar_y_guardar_no_toca_la_tarjeta(w):  # R10
    avena = AlimentoInterpretado(nombre="avena", cantidad=60, unidad="g", dia=HOY, kcal=228, proteina_g=None,
                                 hidratos_g=None, grasa_g=None, estimado=True, dudoso=False, supuestos=[])
    w.claude(PASOS, alimentos=[avena])
    cuerpo = w.interpretar("avena 60 g y hoy son 15 mil pasos")
    assert [a["nombre"] for a in cuerpo["alimentos"]] == ["avena"] and len(cuerpo["cambios"]) == 1
    r = w.post("/api/confirmar", {"texto": cuerpo["texto"], "alimentos": cuerpo["alimentos"], "sin_cambios": True})
    assert r.status_code == 200
    assert w.pasos() == 8000 and w.tarjetas() == []
    assert w.post("/api/asistente/confirmar", {"tarjeta": cuerpo["cambios"][0]}).status_code == 200
    assert w.pasos() == 15000


# -- R12 · R-78, S-20, G-41: what was before changed -----------------------------------------------------------------

@pytest.mark.parametrize("a_mano", [{"pasos": 12000}, "ajustes"])
def test_si_lo_de_antes_cambio_no_se_aplica_y_se_rehace(w, a_mano):  # R12 · C-102
    t = _tarjeta_de_pasos(w)
    if a_mano == "ajustes":  # the default steps of Ajustes, the other «before» of the card
        w.ajustes.cambiar({"pasos_por_defecto": 12000}, desde=HOY)
    else:
        assert w.post("/api/dia/objetivo", a_mano).status_code == 200
    r = w.post("/api/asistente/confirmar", {"tarjeta": t})
    assert r.status_code == 409
    rehecha = r.json()["tarjeta"]
    assert rehecha["texto"] == "Voy a cambiar los pasos previstos del 22-10: 12.000 → 15.000"
    assert w.pasos() == 12000 and w.tarjetas() == []
    assert w.post("/api/asistente/confirmar", {"tarjeta": rehecha}).status_code == 200  # and waits for another OK
    assert w.pasos() == 15000


def test_lo_quitado_entretanto_no_se_aplica(w):  # R12
    w.claude(CambioInterpretado(accion="quitar_alimento", objeto="yogur"))
    (t,) = w.interpretar("quita el yogur")["cambios"]
    w.registro.borrar(w.yogur)
    r = w.post("/api/asistente/confirmar", {"tarjeta": t})
    assert r.status_code == 409 and r.json()["tarjeta"]["tipo"] == "no"


def test_confirmada_al_dia_siguiente_vale_para_el_dia_que_decia(w):  # R12 · G-41
    t = _tarjeta_de_pasos(w)
    w.hoy = HOY + timedelta(days=1)  # past midnight
    r = w.post("/api/asistente/confirmar", {"tarjeta": t})
    assert r.status_code == 200, r.text
    assert w.objetivos.del_dia(HOY)["pasos"] == 15000 and w.objetivos.del_dia(w.hoy) is None


# -- malformed cards ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cuerpo", [{}, {"tarjeta": "x"}, {"tarjeta": {"tipo": "propuesta", "accion": "no_existe"}},
                                    {"tarjeta": {"tipo": "propuesta", "accion": "pasos_dia", "parametros": {}}}])
def test_una_tarjeta_mal_formada_es_400_y_no_cambia_nada(w, cuerpo):
    antes = w.cuentas()
    for ruta in ("/api/asistente/confirmar", "/api/asistente/descartar", "/api/asistente/rehacer"):
        assert w.post(ruta, cuerpo).status_code == 400
    assert w.cuentas() == antes
