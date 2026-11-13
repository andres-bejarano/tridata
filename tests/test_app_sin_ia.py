"""Unit 080 (P-20261105-cf32bea5, map v29): the app without the paid AI.

R6 (the load-bearing one): no route of the server calls an AI, and `crear_app` / `tridata web` start without a key of
the API, without creating any AI client and without loading Whisper. R8 (R-223, C-276, C-277): a weight already
written down is changed in the app. R9 (R-224, C-278, C-279): Ajustes shows «Lo que ha hecho el coach». R10: nothing of
the base is deleted. R2: /api/hoy has no «last of the Asistente». Every value is invented.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.conversaciones import Conversaciones
from tridata.conversaciones.desde_pc import TITULO, conversacion_pc
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas, RegistroPeso
from tridata.web import crear_app
from tridata.web.app import ESTATICOS

HOY = date(2026, 11, 12)
AHORA = datetime(2026, 11, 12, 9, 30)

# R6: every route that called (or only served) the paid AI
RUTAS_DE_IA = [
    ("POST", "/api/interpretar"), ("POST", "/api/dictado"), ("POST", "/api/confirmar"),
    ("GET", "/api/conversaciones"), ("POST", "/api/conversaciones"), ("GET", "/api/conversaciones/1"),
    ("POST", "/api/conversaciones/1/mensajes"), ("POST", "/api/conversaciones/1/opinion"),
    ("POST", "/api/conversaciones/1/reintentar"), ("POST", "/api/conversaciones/1/titulo"),
    ("POST", "/api/conversaciones/1/borrar"), ("POST", "/api/conversaciones/dictado"),
    ("GET", "/api/conversaciones/medicos"), ("POST", "/api/conversaciones/medicos/releer"),
    ("POST", "/api/conversaciones/mensaje/1/tarjetas"), ("POST", "/api/conversaciones/ruta"),
    ("POST", "/api/conversaciones/1/ruta"),
    ("GET", "/api/equipo"), ("PUT", "/api/equipo"), ("GET", "/api/rutas"),
    ("POST", "/api/asistente/rehacer"), ("POST", "/api/asistente/confirmar"), ("POST", "/api/asistente/descartar"),
    ("POST", "/api/plan/importar"), ("POST", "/api/plan/confirmar"),
]


@pytest.fixture
def sin_ia(monkeypatch):
    """No key of the API in the environment, and (unit 083: there is no AI client left to forbid one by one) the
    libraries of the paid API and of the dictation cannot even be imported."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)


class Web:
    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                             desde=DESDE_SIEMPRE)
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.app = crear_app(self.registro, None, self.ajustes, hoy=lambda: HOY, ahora=lambda: AHORA)
        self.cliente = TestClient(self.app)


@pytest.fixture
def web(tmp_path, sin_ia) -> Web:
    return Web(tmp_path)


# -- R6 (portante): no door to the API ------------------------------------------------------------------------------

@pytest.mark.parametrize(("metodo", "ruta"), RUTAS_DE_IA)
def test_las_rutas_de_ia_no_existen(tmp_path, sin_ia, metodo, ruta):  # R6 · S-5
    web = Web(tmp_path)
    with TestClient(web.app) as cliente:  # the start of the app runs too (it loaded Whisper before)
        r = cliente.request(metodo, ruta, json={"texto": "una manzana inventada"})
    assert r.status_code == 404, f"{metodo} {ruta} still answers {r.status_code}"


def test_la_app_arranca_sin_clientes_de_ia(tmp_path, sin_ia):  # R6
    web = Web(tmp_path)
    with TestClient(web.app) as cliente:
        assert cliente.get("/").status_code == 200
        assert cliente.get("/api/dia").status_code == 200
        assert cliente.get("/api/plan").status_code == 200


def test_crear_app_no_pide_interprete(tmp_path, sin_ia):  # R6: ignored if given (unit 083: no `transcriptor` left)
    db = tmp_path / "data.sqlite3"
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    interprete = MagicMock()
    app = crear_app(RegistroComidas(db), interprete, ajustes, hoy=lambda: HOY)
    with TestClient(app) as cliente:
        assert cliente.get("/api/dia").status_code == 200
    assert interprete.mock_calls == []  # nothing is interpreted


def test_tridata_web_arranca_sin_clave_ni_avisos_de_ia(tmp_path, sin_ia, monkeypatch, capsys):  # R6
    import uvicorn

    from tridata import cli

    db = tmp_path / "data.sqlite3"
    Ajustes(db).cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    for variable in ("TRIDATA_MEDICOS_DIR", "GARMIN_EMAIL", "GARMIN_PASSWORD", "TRIDATA_GOOGLE_CLIENTE"):
        monkeypatch.delenv(variable, raising=False)
    arrancada = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: arrancada.append(app))
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # even without the `voz` extra nothing is said
    assert cli._cmd_web("127.0.0.1", 8765, db_path=db) == 0
    assert len(arrancada) == 1
    salida = capsys.readouterr()
    texto = (salida.out + salida.err).lower()
    for palabra in ("dictado", "whisper", "medicos_dir", "asistente", "anthropic"):
        assert palabra not in texto, palabra
    with TestClient(arrancada[0]) as cliente:
        assert cliente.post("/api/interpretar", json={"texto": "x"}).status_code == 404


# -- R2: Hoy has three cards ------------------------------------------------------------------------------------------

def test_hoy_sin_ultima_del_asistente(web):  # R2 · S-27
    h = web.cliente.get("/api/hoy").json()
    assert set(h) >= {"comida", "entreno", "como_llegas"}
    assert "asistente" not in h


def test_las_pantallas_no_tienen_lo_quitado():  # R1, R2, R3, R4, R5 (what the E2E sees, in the files)
    index = (ESTATICOS / "index.html").read_text(encoding="utf-8")
    assert 'data-ir="asistente"' not in index and 'id="asistente"' not in index  # R1
    assert 'id="texto"' not in index and 'id="grabar"' not in index and 'id="hoy-asistente"' not in index  # R2
    for fichero in ("app.js", "recetas.js", "plan.js", "ajustes.js", "hoy.js"):
        js = (ESTATICOS / fichero).read_text(encoding="utf-8")
        for ruta in ("/api/interpretar", "/api/dictado", "/api/confirmar", "/api/conversaciones", "/api/equipo",
                     "/api/asistente/", "/api/plan/importar"):
            assert ruta not in js, f"{fichero} still calls {ruta}"
    for retirado in ("asistente.js", "conversaciones.js", "dictado.js", "cola.js"):
        assert not (ESTATICOS / retirado).exists(), retirado
    assert "Importar plan" not in (ESTATICOS / "plan.js").read_text(encoding="utf-8")  # R4
    assert "en el Asistente" not in (ESTATICOS / "plan.js").read_text(encoding="utf-8")  # R4
    ajustes = (ESTATICOS / "ajustes.html").read_text(encoding="utf-8") + (ESTATICOS / "ajustes.js").read_text(
        encoding="utf-8")
    assert "El equipo" not in ajustes and "tope" not in ajustes.lower()  # R5
    assert 'data-ir="asistente"' not in ajustes  # R1, the bar of Ajustes too


def test_el_movil_suelta_lo_viejo():  # R2: a new cache of the service worker without the old modules
    sw = (ESTATICOS / "sw.js").read_text(encoding="utf-8")
    assert '"tridata-cascaron-v13"' not in sw
    for retirado in ("cola.js", "dictado.js", "asistente.js", "conversaciones.js"):
        assert retirado not in sw


# -- R7: what is done by hand stays -----------------------------------------------------------------------------------

def test_lo_de_mano_sigue(web):  # R7
    r = web.cliente.post("/api/alimento", json={"nombre": "Manzana inventada", "cantidad": 150, "unidad": "g",
                                                "kcal": 78, "dia": HOY.isoformat()})
    assert r.status_code == 200, r.text
    ident = r.json()["alimento"]["id"]
    assert web.cliente.post(f"/api/alimento/{ident}", json={"nombre": "Manzana inventada", "cantidad": 200,
                                                            "unidad": "g", "kcal": 104}).status_code == 200
    assert web.cliente.post(f"/api/alimento/{ident}/borrar", json={"confirmado": True}).status_code == 200
    assert web.cliente.get("/api/dia").json()["alimentos"] == []
    assert web.cliente.get("/api/recetas").status_code == 200
    assert web.cliente.post("/api/plan/nuevo", json={"nombre": "Plan inventado", "carrera": {
        "nombre": "Carrera inventada", "fecha": "2027-03-01", "objetivo": "45:00"}}).status_code == 200


# -- R8 · R-223, C-276, C-277: change a weight already written down ----------------------------------------------------

def _pesos(web: Web) -> None:
    for hace, kg in ((3, 78.0), (2, 77.8), (1, 77.6), (0, 77.4)):
        web.pesos.apuntar(HOY - timedelta(days=hace), kg, "nota inventada" if hace == 0 else None, hoy=HOY)


def test_cambiar_el_peso_de_hoy_recalcula(web):  # R8 · C-276
    _pesos(web)
    antes = web.cliente.get("/api/dia").json()["peso"]["media_7_dias"]["kg"]
    r = web.cliente.post(f"/api/peso/{HOY.isoformat()}", json={"kg": "77,0"})
    assert r.status_code == 200, r.text
    assert r.json()["peso"]["kg"] == 77.0
    assert web.pesos.del_dia(HOY)["kg"] == 77.0
    assert web.pesos.del_dia(HOY)["nota"] == "nota inventada"  # only its value changes
    despues = web.cliente.get("/api/dia").json()["peso"]
    assert despues["media_7_dias"]["kg"] == pytest.approx((78.0 + 77.8 + 77.6 + 77.0) / 4, abs=0.01)
    assert despues["media_7_dias"]["kg"] != antes
    assert r.json()["resumen"]["media_7_dias"]["kg"] == despues["media_7_dias"]["kg"]
    serie = web.cliente.get("/api/peso").json()
    assert [p["kg"] for p in serie["pesos"] if p["dia"] == HOY.isoformat()] == [77.0]  # still one that day


def test_cambiar_el_peso_de_otro_dia(web):  # R8: the one of another day
    _pesos(web)
    ayer = HOY - timedelta(days=1)
    r = web.cliente.post(f"/api/peso/{ayer.isoformat()}", json={"kg": 77.9})
    assert r.status_code == 200, r.text
    assert web.pesos.del_dia(ayer)["kg"] == 77.9
    assert web.pesos.del_dia(HOY)["kg"] == 77.4


@pytest.mark.parametrize("kg", ["abc", "", None, 5, 900, "1e9", True])
def test_un_peso_que_no_vale_no_se_guarda(web, kg):  # R8 · C-277
    _pesos(web)
    r = web.cliente.post(f"/api/peso/{HOY.isoformat()}", json={"kg": kg})
    assert r.status_code in (400, 422), r.text
    assert "No se ha cambiado" in r.json()["error"]
    assert web.pesos.del_dia(HOY)["kg"] == 77.4


def test_no_se_apunta_un_peso_nuevo(web):  # R8: no field to write down a weight on a day without one
    _pesos(web)
    sin_peso = HOY - timedelta(days=5)
    r = web.cliente.post(f"/api/peso/{sin_peso.isoformat()}", json={"kg": 77.0})
    assert r.status_code == 404, r.text
    assert web.pesos.del_dia(sin_peso) is None
    assert web.cliente.post("/api/peso/no-es-fecha", json={"kg": 77.0}).status_code in (400, 404)
    manana = HOY + timedelta(days=1)
    assert web.cliente.post(f"/api/peso/{manana.isoformat()}", json={"kg": 77.0}).status_code == 404
    assert web.pesos.del_dia(manana) is None


# -- R9 · R-224, C-278, C-279: «Lo que ha hecho el coach» --------------------------------------------------------------

def _cambio_del_coach(db, cuando: datetime, papel: str, texto: str) -> None:
    conversaciones = Conversaciones(db)
    conv = conversacion_pc(conversaciones, cuando)
    conversaciones.anadir_mensaje(conv["id"], "asistente", texto, cuando, tarjetas=[], papel=papel)


def test_lo_que_ha_hecho_el_coach_el_mas_nuevo_arriba(web):  # R9 · C-278
    _cambio_del_coach(web.db, datetime(2026, 11, 12, 8, 5), "nutricionista",
                      "Hecho: apuntado en el desayuno de hoy: avena inventada 60 g (228 kcal).")
    _cambio_del_coach(web.db, datetime(2026, 11, 12, 9, 15), "entrenador",
                      "Hecho: series inventadas pasan del miércoles al jueves.")
    otra = Conversaciones(web.db).crear("Otra conversación inventada", AHORA)  # an old one of the Asistente: no
    Conversaciones(web.db).anadir_mensaje(otra["id"], "asistente", "respuesta inventada", AHORA)
    r = web.cliente.get("/api/coach")
    assert r.status_code == 200, r.text
    cambios = r.json()["cambios"]
    assert [c["texto"] for c in cambios] == ["Hecho: series inventadas pasan del miércoles al jueves.",
                                             "Hecho: apuntado en el desayuno de hoy: avena inventada 60 g (228 kcal)."]
    assert [c["hora"] for c in cambios] == ["2026-11-12T09:15:00", "2026-11-12T08:05:00"]
    assert [c["papel"] for c in cambios] == ["entrenador", "nutricionista"]


def test_solo_los_ultimos_veinte(web):  # R9
    for i in range(25):
        _cambio_del_coach(web.db, datetime(2026, 11, 1, 8, 0) + timedelta(hours=i), "fisio", f"Hecho: cambio {i}.")
    cambios = web.cliente.get("/api/coach").json()["cambios"]
    assert len(cambios) == 20
    assert cambios[0]["texto"] == "Hecho: cambio 24." and cambios[-1]["texto"] == "Hecho: cambio 5."


def test_sin_cambios_del_coach_lo_dice(web):  # R9
    assert web.cliente.get("/api/coach").json() == {"cambios": []}
    js = (ESTATICOS / "ajustes.js").read_text(encoding="utf-8")
    assert "Lo que ha hecho el coach" in (ESTATICOS / "ajustes.html").read_text(encoding="utf-8")
    assert "El coach aún no ha hecho ningún cambio" in js


def test_lo_del_coach_solo_se_ve(web):  # R9 · C-279: no way to change or delete them from there
    _cambio_del_coach(web.db, AHORA, "fisio", "Hecho: incidencia inventada abierta.")
    for metodo in ("POST", "PUT", "DELETE"):
        assert web.cliente.request(metodo, "/api/coach", json={}).status_code == 405
    assert len(web.cliente.get("/api/coach").json()["cambios"]) == 1


# -- R10: nothing of the base is deleted ------------------------------------------------------------------------------

def _filas(db) -> dict[str, int]:
    with sqlite3.connect(db) as conn:
        tablas = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                  if not t.startswith("sqlite_")]
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tablas}  # noqa: S608 - names from sqlite_master


def test_una_base_de_hoy_abre_sin_perder_nada(tmp_path, sin_ia):  # R10
    db = tmp_path / "data.sqlite3"
    Ajustes(db).cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    conversaciones = Conversaciones(db)
    conv = conversaciones.crear("Conversación inventada", AHORA)
    conversaciones.anadir_mensaje(conv["id"], "andres", "pregunta inventada", AHORA)
    conversaciones.anadir_mensaje(conv["id"], "asistente", "respuesta inventada", AHORA, papel="fisio",
                                  tarjetas=[{"tipo": "propuesta", "texto": "tarjeta inventada"}])
    from tridata.conversaciones.medicos import Medicos
    from tridata.nutricion.asistente import Tarjetas

    Medicos(db, None)
    Tarjetas(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO transcripcion_medica VALUES ('analitica-inventada.pdf', 'h', 'analitica', "
                     "'Analítica inventada', '2026-01-01', '[]', 'notas inventadas', '2026-01-02T00:00:00')")
        # unit 083: nothing writes the spending any more; a row of before, as the API left it
        conn.execute("INSERT INTO gasto_asistente (hora, mes, que, modelo, tokens_entrada, tokens_salida, "
                     "tokens_cache_escritura, tokens_cache_lectura, dolares) VALUES ('2026-10-01T10:00:00', "
                     "'2026-10', 'conversacion', 'claude-haiku-4-5', 10, 5, 0, 0, 0.0001)")
        conn.execute("INSERT INTO tarjeta_asistente (hora, texto_peticion, fecha_peticion, accion, toca, estado) "
                     "VALUES ('2026-10-01T10:00:00', 'texto inventado', '2026-10-01', 'pasos', 'hoy', 'aplicada')")
    RegistroComidas(db).guardar([Alimento(nombre="Avena inventada", dia=HOY, cantidad=60, unidad="g", kcal=228)],
                                hoy=HOY, texto="avena", sin_cambios=True)
    antes = _filas(db)
    with sqlite3.connect(db) as conn:
        mensajes = conn.execute("SELECT * FROM mensaje ORDER BY id").fetchall()
    app = crear_app(RegistroComidas(db), None, Ajustes(db), hoy=lambda: HOY, ahora=lambda: AHORA)
    with TestClient(app) as cliente:
        for ruta in ("/", "/api/hoy", "/api/dia", "/api/coach", "/api/plan", "/api/recetas", "/api/ajustes"):
            assert cliente.get(ruta).status_code == 200, ruta
    despues = _filas(db)
    assert {t: despues.get(t) for t in antes} == antes  # every table is there, with every row
    for tabla in ("conversacion", "mensaje", "gasto_asistente", "transcripcion_medica", "tarjeta_asistente"):
        assert despues[tabla] == antes[tabla] >= 1, tabla
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM mensaje ORDER BY id").fetchall() == mensajes  # cards included


def test_el_titulo_del_coach_es_el_de_siempre():  # R9 reads the conversation the coach writes (unit 066)
    from tridata.web.conversaciones import DESDE_EL_PC

    assert DESDE_EL_PC == TITULO == "Desde el PC"


# Bug 055 · R1, moved from `test_conversaciones_web.py` (whose path through the Asistente this unit removes) to the
# path the coach still uses: `preparar_cambio` validates the change with the same `CambioInterpretado`
CAMBIO_SESION = {"accion": "plan_cambiar_sesion", "dia": "2026-10-28", "texto": "Rodaje 40' | 40' a 5:00-6:00"}


@pytest.mark.parametrize("sobra", [{"valor": "5:00-6:00"}, {"cantidad": "40'"}, {"kcal_estimadas": "pocas"}])
def test_un_numero_mal_escrito_que_el_cambio_no_usa_no_lo_rechaza(tmp_path, sobra):  # bug 055 · R1
    import asyncio
    import re

    from tridata.conversaciones.desde_pc import montar_equipo
    from tridata.mcp_server import build_server
    from tridata.plan import Planes
    from tridata.storage import DataStore

    db = tmp_path / "data.sqlite3"
    Planes(db).crear({"nombre": "Plan Inventado 26", "apartados": [{"titulo": "Semanas", "parte": "previsto"}],
                      "carreras": [], "semanas": [], "nutricion": [], "incidencias": [], "notas": [],
                      "sesiones": [{"apartado": 0, "fecha": "2026-10-28", "titulo": "Series inventadas",
                                    "bloques": ["6x1000 a 4:00"], "clave": True}]}, datetime(2026, 10, 26, 9, 0))
    store = DataStore(db)
    equipo = montar_equipo(db, store=store, hoy=lambda: date(2026, 10, 26), ahora=lambda: datetime(2026, 10, 26, 9))
    resultado = asyncio.run(build_server(store, equipo=equipo).call_tool(
        "preparar_cambio", {"cambio": {**CAMBIO_SESION, **sobra}}))
    texto = "".join(c.text for c in resultado.content)
    assert not resultado.is_error and "No he entendido" not in texto, texto
    assert texto.startswith("Esto es lo que se haría") and re.search(r"Huella: [0-9a-f]{12}", texto), texto
