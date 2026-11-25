"""Unit 112 in the web (R2, R4 · R-341, R-343, C-434, C-436, C-437): Nutrición › Día.

`GET /api/recetas` brings the variants of each recipe (what the search box offers as «Receta · variante»); a food
chosen as one (`guardado: {tipo: "variante", id}`) is logged with ITS kcal/100 g, worked out by the server. The JSON of
the day says which foods are dessert; `POST /api/alimento/N/postre` marks or unmarks one, and only that changes.

Starlette's TestClient over a temporary SQLite; nothing talks to Garmin, Google or a paid service. Invented data.
"""
from __future__ import annotations

from datetime import date

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.nutricion.recetas import Recetario
from tridata.web import crear_app

HOY = date(2026, 11, 25)
VARIANTE = "Garbanzos de la abuela · sin chorizo"


class Web:
    def __init__(self, tmp_path) -> None:
        db = tmp_path / "data.sqlite3"
        self.registro = RegistroComidas(db)
        ajustes = Ajustes(db)
        ajustes.cambiar({"basal_kcal": 1900, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
        self.recetario = Recetario(db)
        self.receta = self.recetario.crear_receta({"nombre": "Garbanzos de la abuela", "peso_neto_g": 1000,
                                                   "ingredientes": [{"nombre": "garbanzo cocido de prueba",
                                                                     "cantidad": 1000, "unidad": "g",
                                                                     "kcal": 1260}]}, HOY)
        self.variante = self.recetario.crear_variante(self.receta["id"], {"nombre": "sin chorizo", "kcal_100g": 105})
        self.cliente = TestClient(crear_app(self.registro, None, ajustes, hoy=lambda: HOY, recetario=self.recetario))

    def dia(self) -> dict:
        respuesta = self.cliente.get(f"/api/dia?fecha={HOY.isoformat()}")
        assert respuesta.status_code == 200, respuesta.text
        return respuesta.json()


@pytest.fixture
def web(tmp_path) -> Web:
    return Web(tmp_path)


# -- R2 · R-341, C-434: the search box offers the variant, and choosing it uses its kcal --------------------------

def test_api_recetas_lleva_las_variantes(web):  # R2
    (receta,) = web.cliente.get("/api/recetas").json()["recetas"]
    (v,) = receta["variantes"]
    assert (v["id"], v["nombre"], v["kcal_100g"]) == (web.variante["id"], "sin chorizo", 105)


def test_anadir_a_mano_la_variante_elegida_usa_sus_kcal(web):  # R2
    respuesta = web.cliente.post("/api/alimento", json={
        "nombre": "Garbanzos de la abuela · sin chorizo", "cantidad": 350, "unidad": "g", "kcal": 999,
        "dia": HOY.isoformat(), "guardado": {"tipo": "variante", "id": web.variante["id"]}})
    assert respuesta.status_code == 200, respuesta.text
    a = respuesta.json()["alimento"]
    assert a["nombre"] == VARIANTE and round(a["kcal"]) == 368 and a["estimado"] is False
    assert a["origen"] == "de tu receta «Garbanzos de la abuela» · sin chorizo"
    assert web.recetario.receta(web.receta["id"])["en_uso"]["kcal_100g"] == 126


def test_variante_borrada_antes_de_guardar_no_apunta_nada(web):  # R2
    web.recetario.borrar_variante(web.variante["id"], confirmado=True)
    respuesta = web.cliente.post("/api/alimento", json={
        "nombre": VARIANTE, "cantidad": 350, "unidad": "g", "dia": HOY.isoformat(),
        "guardado": {"tipo": "variante", "id": web.variante["id"]}})
    assert respuesta.status_code == 422, respuesta.text
    assert web.registro.apuntes_del_dia(HOY) == []


def test_cambiar_un_alimento_por_la_variante_conserva_fila_y_seccion(web):  # R2 (the change card, unit 074)
    a = web.registro.anadir(Alimento(nombre="garbanzos", dia=HOY, cantidad=350, unidad="g", kcal=400), HOY)
    web.registro.mover_seccion(a["id"], "comida")
    respuesta = web.cliente.post(f"/api/alimento/{a['id']}", json={
        "nombre": VARIANTE, "cantidad": 350, "unidad": "g", "dia": HOY.isoformat(),
        "guardado": {"tipo": "variante", "id": web.variante["id"]}})
    assert respuesta.status_code == 200, respuesta.text
    cambiado = web.registro.alimento(a["id"])
    assert (cambiado["nombre"], round(cambiado["kcal"]), cambiado["seccion"]) == (VARIANTE, 368, "comida")


# -- R4 · R-343, C-436, C-437: the dessert in the day -----------------------------------------------------------

def _cena(web) -> tuple[dict, dict, dict]:
    filas = []
    for nombre, kcal in (("merluza de prueba", 180), ("ensalada de prueba", 60), ("yogur de prueba", 77)):
        a = web.registro.anadir(Alimento(nombre=nombre, dia=HOY, cantidad=100, unidad="g", kcal=kcal), HOY)
        filas.append(web.registro.mover_seccion(a["id"], "cena"))
    return filas[0], filas[1], filas[2]


def test_marcar_postre_en_el_dia_no_cambia_el_total(web):  # R4
    _, _, yogur = _cena(web)
    antes = web.dia()
    assert all(a["postre"] is False for a in antes["alimentos"])
    respuesta = web.cliente.post(f"/api/alimento/{yogur['id']}/postre", json={"postre": True})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["alimento"]["postre"] is True and cuerpo["alimento"]["seccion"] == "cena"
    despues = cuerpo["dia"]
    (marcado,) = [a for a in despues["alimentos"] if a["id"] == yogur["id"]]
    assert marcado["postre"] is True and marcado["seccion"] == "cena"
    assert despues["total_kcal"] == antes["total_kcal"]
    assert despues["margenes"] == antes["margenes"]


def test_mover_un_postre_sigue_siendo_postre_y_quitar_la_marca_lo_devuelve(web):  # R4 · S-116
    _, _, yogur = _cena(web)
    web.cliente.post(f"/api/alimento/{yogur['id']}/postre", json={"postre": True})
    movido = web.cliente.post(f"/api/alimento/{yogur['id']}/seccion", json={"seccion": "comida"}).json()
    assert (movido["alimento"]["seccion"], movido["alimento"]["postre"]) == ("comida", True)
    quitado = web.cliente.post(f"/api/alimento/{yogur['id']}/postre", json={"postre": False}).json()
    assert (quitado["alimento"]["seccion"], quitado["alimento"]["postre"]) == ("comida", False)


def test_anadir_a_mano_como_postre(web):  # R4: the page may send the mark when adding
    respuesta = web.cliente.post("/api/alimento", json={"nombre": "flan de prueba", "cantidad": 100, "unidad": "g",
                                                        "kcal": 120, "dia": HOY.isoformat(), "postre": True})
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["alimento"]["postre"] is True


@pytest.mark.parametrize("cuerpo", [{}, {"postre": "sí"}, {"postre": 1}, {"postre": None}])
def test_postre_mal_enviado_no_cambia_nada(web, cuerpo):  # R4
    _, _, yogur = _cena(web)
    respuesta = web.cliente.post(f"/api/alimento/{yogur['id']}/postre", json=cuerpo)
    assert respuesta.status_code in (400, 422), respuesta.text
    assert web.registro.alimento(yogur["id"])["postre"] is False


def test_postre_de_un_alimento_que_no_esta_es_404(web):  # R4
    assert web.cliente.post("/api/alimento/999/postre", json={"postre": True}).status_code == 404
