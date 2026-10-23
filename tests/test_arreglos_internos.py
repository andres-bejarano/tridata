"""Unit 028 · internal fixes: dry runs that do not touch the store, safe backups, the rules on the server and
the race between the sync and the app.

R1-R4 drive `cli` over a temporary SQLite; R6 and R8 the Starlette app (R8 straight through ASGI, so both
requests share one event loop like uvicorn's); R7 two `Cierres` over the same SQLite in two threads. R5 lives
in the Recetas screen: `tests/e2e/test_arreglos_internos_e2e.py`. Every value is invented.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path

from starlette.testclient import TestClient

from tridata import cli
from tridata.models import DailyStats
from tridata.nutricion import (
    DESDE_SIEMPRE, Ajustes, Alimento, Cierres, ObjetivosDia, RegistroComidas, RegistroPeso,
)
from tridata.nutricion import cierre as cierre_mod
from tridata.nutricion import importar_deficit as imp
from tridata.nutricion.recetas import leer_cargas
from tridata.storage import DataStore
from tridata.web import crear_app

FIXTURES = Path(__file__).parent / "fixtures"
RECETAS = FIXTURES / "recetas_inventado.md"
DEFICIT = FIXTURES / "deficit_inventado.md"
GRAFICA = FIXTURES / "grafica_inventada.html"
HOY_IMPORTAR = date(2026, 3, 12)  # the "today" of the invented deficit.md (unit 009)
HOY = date(2026, 10, 21)  # a Wednesday
AYER = date(2026, 10, 20)


def _huella(db: Path) -> str:
    return hashlib.sha256(db.read_bytes()).hexdigest()


def _tablas(db: Path) -> list[str]:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        return [r[0] for r in conn.execute("SELECT name FROM sqlite_master ORDER BY name")]


def _filas(db: Path, tabla: str) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]  # noqa: S608 - fixed names


def _respuestas(*valores):
    cola = list(valores)

    def preguntar(texto=""):
        if not cola:
            raise EOFError
        return cola.pop(0)

    return preguntar


# -- R1: «importar-recetas --simular» leaves the store as it was ---------------------------------------------

def _cargas():
    filas = imp.filas_de_recetas(RECETAS.read_text(encoding="utf-8"))
    return leer_cargas(filas.referencias, filas.taras)


def test_simular_recetas_no_toca_la_base(tmp_path, capsys):  # R1
    db = tmp_path / "data.sqlite3"
    RegistroComidas(db)  # Andrés's store exists, without the recipe tables yet
    antes, tablas = _huella(db), _tablas(db)

    codigo = cli._cmd_importar_recetas(RECETAS, True, db_path=db, backup_dir=tmp_path / "copias", hoy=HOY)

    assert codigo == 0
    assert (_huella(db), _tablas(db)) == (antes, tablas)
    assert not (tmp_path / "copias").exists()
    esperado = [f"  {c.describir()} · nueva" for c in _cargas()] + ["Simulación: no se ha guardado nada."]
    assert capsys.readouterr().out.splitlines() == esperado


def test_simular_recetas_ya_importadas_dice_ya_estaba_sin_tocar_la_base(tmp_path, monkeypatch, capsys):  # R1
    db = tmp_path / "data.sqlite3"
    RegistroComidas(db)
    monkeypatch.setattr(cli, "_cmd_backup", lambda *a, **k: 0)
    assert cli._cmd_importar_recetas(RECETAS, False, db_path=db, backup_dir=tmp_path / "copias", hoy=HOY) == 0
    antes, tablas = _huella(db), _tablas(db)
    capsys.readouterr()

    assert cli._cmd_importar_recetas(RECETAS, True, db_path=db, backup_dir=tmp_path / "copias", hoy=HOY) == 0

    assert (_huella(db), _tablas(db)) == (antes, tablas)
    esperado = [f"  {c.describir()} · ya estaba" for c in _cargas()] + ["Simulación: no se ha guardado nada."]
    assert capsys.readouterr().out.splitlines() == esperado


# -- R2: a backup without a store warns, copies no file of it, and the import goes on --------------------------

def test_copia_sin_base_avisa_y_no_crea_ningun_fichero(tmp_path, capsys):  # R2
    db = tmp_path / "data.sqlite3"
    md = tmp_path / "deficit.md"
    md.write_text("# inventado\n", encoding="utf-8")

    assert cli._cmd_backup(cli._SoloCopia(db), tmp_path / "copias", 14, [md]) == 0

    assert "no hay base que copiar" in capsys.readouterr().out.lower()
    assert not db.exists()
    copias = list((tmp_path / "copias").iterdir())
    assert len(copias) == 1
    assert sorted(p.name for p in copias[0].iterdir()) == ["deficit.md"]


def test_importar_sin_base_avisa_y_sigue(tmp_path, capsys):  # R2 (the restore path of C-78)
    db = tmp_path / "data.sqlite3"
    copias = tmp_path / "copias"

    codigo = cli._cmd_importar(DEFICIT, GRAFICA, False, db_path=db, backup_dir=copias,
                               preguntar=_respuestas("s"), hoy=HOY_IMPORTAR)

    salida = capsys.readouterr()
    assert codigo == 0, salida.err
    assert "no hay base que copiar" in (salida.out + salida.err).lower()
    copia = next(copias.iterdir())
    assert "data.sqlite3" not in {p.name for p in copia.iterdir()}
    assert _filas(db, "peso_dia") == 10
    assert imp.Importaciones(db).ultima() is not None


# -- R3: two backups in the same second -------------------------------------------------------------------

class _Reloj:
    instante = datetime(2026, 10, 23, 8, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.instante


def test_dos_copias_en_el_mismo_segundo_y_la_rotacion(tmp_path, monkeypatch):  # R3
    store = DataStore(tmp_path / "data.sqlite3")
    raiz = tmp_path / "copias"
    monkeypatch.setattr(cli, "datetime", _Reloj)

    assert cli._cmd_backup(store, raiz, 2, []) == 0
    assert cli._cmd_backup(store, raiz, 2, []) == 0
    carpetas = sorted(p.name for p in raiz.iterdir())
    assert len(carpetas) == 2 and carpetas[0] == "2026-10-23T080000"
    assert all((raiz / c / "data.sqlite3").is_file() for c in carpetas)

    segunda = carpetas[1]
    _Reloj.instante = datetime(2026, 10, 23, 8, 0, 1)
    try:
        assert cli._cmd_backup(store, raiz, 2, []) == 0
    finally:
        _Reloj.instante = datetime(2026, 10, 23, 8, 0, 0)
    # the two most recent stay: the second of 08:00:00 and the one of 08:00:01
    assert sorted(p.name for p in raiz.iterdir()) == [segunda, "2026-10-23T080001"]


# -- R4: an import cut halfway says where it stopped, fails, and is recorded as partial -------------------------

def test_importacion_cortada_dice_donde_se_quedo_y_repetir_completa(tmp_path, monkeypatch, capsys):  # R4
    db = tmp_path / "data.sqlite3"
    original = RegistroComidas.importar_dia
    dias_vistos: list[date] = []

    def falla_al_tercero(self, dia, alimentos, hoy):
        dias_vistos.append(dia)
        if len(dias_vistos) == 3:
            raise sqlite3.OperationalError("disk I/O error (inventado)")
        return original(self, dia, alimentos, hoy)

    monkeypatch.setattr(RegistroComidas, "importar_dia", falla_al_tercero)
    codigo = cli._cmd_importar(DEFICIT, GRAFICA, False, db_path=db, backup_dir=tmp_path / "copias",
                               preguntar=_respuestas("s"), hoy=HOY_IMPORTAR)

    salida = capsys.readouterr()
    assert codigo != 0
    corte = dias_vistos[2].isoformat()
    assert corte in salida.err
    assert "10 pesos" in salida.err and "2 días" in salida.err
    assert _filas(db, "peso_dia") == 10
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(DISTINCT dia) FROM alimento_apuntado").fetchone()[0] == 2
    ultima = imp.Importaciones(db).ultima()
    assert ultima is not None
    resumen = ultima["resumen"]["resumen"]
    assert resumen["parcial"] is True and resumen["cortado_en"] == corte
    assert (resumen["pesos_nuevos"], resumen["dias_nuevos"]) == (10, 2)

    # again, without the failure: «s» to importing again, «s» to save; only what is missing is added
    monkeypatch.setattr(RegistroComidas, "importar_dia", original)
    codigo = cli._cmd_importar(DEFICIT, GRAFICA, False, db_path=db, backup_dir=tmp_path / "copias2",
                               preguntar=_respuestas("s", "s"), hoy=HOY_IMPORTAR)
    salida = capsys.readouterr()
    assert codigo == 0, salida.err
    assert "Guardado: 0 pesos nuevos, 5 días nuevos; 10 y 2 ya estaban" in salida.out
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(DISTINCT dia) FROM alimento_apuntado").fetchone()[0] == 7
    assert _filas(db, "peso_dia") == 10


# -- R6: estimated kcal without a quantity do not count, whatever the way in -----------------------------------

def test_alimento_estimado_sin_cantidad_queda_pendiente(tmp_path):  # R6 · R-8
    db = tmp_path / "data.sqlite3"
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    cliente = TestClient(crear_app(RegistroComidas(db), None, ajustes, hoy=lambda: HOY))

    r = cliente.post("/api/alimento", json={"nombre": "leche en el café", "dia": HOY.isoformat(), "kcal": 40,
                                            "estimado": True})

    assert r.status_code == 200, r.text
    a = r.json()["alimento"]
    assert (a["cantidad"], a["kcal"], a["pendiente_cantidad"]) == (None, None, True)
    assert cliente.get(f"/api/dia?fecha={HOY.isoformat()}").json()["total_kcal"] == 0


# -- R7 (bearing): two simultaneous corrections of the same closed day leave ONE trail row --------------------

def _cierres(db: Path) -> Cierres:
    ajustes = Ajustes(db)
    pesos = RegistroPeso(db)
    return Cierres(db, ajustes=ajustes, pesos=pesos, objetivos=ObjetivosDia(db), store=DataStore(db))


def test_dos_correcciones_a_la_vez_dejan_un_solo_rastro(tmp_path, monkeypatch):  # R7
    db = tmp_path / "data.sqlite3"
    Ajustes(db).cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                        desde=DESDE_SIEMPRE)
    store = DataStore(db)
    lunes = date(2026, 10, 19)
    RegistroComidas(db).guardar([Alimento(nombre="Comida", dia=lunes, cantidad=1, unidad="ración", kcal=2000)],
                                hoy=lunes, texto="Comida", sin_cambios=True)
    store.save_daily_stats(DailyStats(stat_date=lunes, steps=10000, total_calories=2500))
    _cierres(db).al_dia(HOY)
    assert _cierres(db).del_dia(lunes)["gasto_real"] == 2500
    store.save_daily_stats(DailyStats(stat_date=lunes, steps=10000, total_calories=2800))  # Garmin corrects it

    # both read the same «gasto_real» before either writes: the sync and the open app
    barrera = threading.Barrier(2, timeout=10)
    relevante = cierre_mod.es_relevante

    def a_la_vez(*args, **kwargs):
        barrera.wait()
        return relevante(*args, **kwargs)

    monkeypatch.setattr(cierre_mod, "es_relevante", a_la_vez)
    resultados: list = []
    errores: list = []

    def corregir():
        try:
            resultados.append(_cierres(db).al_dia(HOY)["correcciones"])
        except Exception as exc:  # noqa: BLE001 - reported below
            errores.append(exc)

    hilos = [threading.Thread(target=corregir) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)

    assert errores == []
    assert sorted(resultados) == [0, 1]
    with sqlite3.connect(db) as conn:
        rastro = conn.execute("SELECT gasto_antes, gasto_despues FROM cierre_rastro WHERE dia = ?",
                              (lunes.isoformat(),)).fetchall()
    assert rastro == [(2500, 2800)]
    assert _cierres(db).del_dia(lunes)["gasto_real"] == 2800
    assert len(_cierres(db).avisos_pendientes()) == 1


# -- R8: «Cerrar el día» writes outside the app's loop ------------------------------------------------------

async def _llamar(app, metodo: str, ruta: str, cuerpo: dict | None = None) -> dict:
    """One request straight through ASGI; returns the status and when its last byte was sent."""
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else b""
    camino, _, consulta = ruta.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": metodo, "scheme": "http",
             "path": camino, "raw_path": camino.encode(), "query_string": consulta.encode(), "root_path": "",
             "headers": [(b"host", b"prueba"), (b"content-type", b"application/json"),
                         (b"content-length", str(len(datos)).encode())],
             "client": ("127.0.0.1", 50000), "server": ("prueba", 80)}
    leido = False

    async def receive():
        nonlocal leido
        if not leido:
            leido = True
            return {"type": "http.request", "body": datos, "more_body": False}
        await asyncio.Event().wait()  # the client never disconnects

    respuesta: dict = {}

    async def send(mensaje):
        if mensaje["type"] == "http.response.start":
            respuesta["estado"] = mensaje["status"]
        elif mensaje["type"] == "http.response.body" and not mensaje.get("more_body"):
            respuesta["fin"] = time.monotonic()

    await app(scope, receive, send)
    return respuesta


def test_cerrar_el_dia_no_bloquea_otras_peticiones(tmp_path):  # R8
    db = tmp_path / "data.sqlite3"
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    registro = RegistroComidas(db)
    registro.anadir(Alimento(nombre="comida de prueba", dia=AYER, cantidad=200, unidad="g", kcal=400), hoy=AYER)
    app = crear_app(registro, None, ajustes, hoy=lambda: HOY)

    otra = sqlite3.connect(db, check_same_thread=False)  # another connection holds the write lock
    otra.execute("BEGIN IMMEDIATE")
    liberado: dict = {}

    def liberar():
        liberado["en"] = time.monotonic()
        otra.rollback()

    reloj = threading.Timer(1.5, liberar)

    async def escena():
        reloj.start()
        cerrar = asyncio.create_task(_llamar(app, "POST", "/api/dia/cerrar", {"fecha": AYER.isoformat()}))
        await asyncio.sleep(0.2)  # the close is already waiting for the lock
        otra_peticion = await _llamar(app, "GET", "/")
        return await cerrar, otra_peticion

    try:
        cierre, pagina = asyncio.run(asyncio.wait_for(escena(), 20))
    finally:
        reloj.cancel()
        otra.close()

    assert pagina["estado"] == 200
    assert pagina["fin"] < liberado["en"], "la otra petición esperó a que se liberase la base"
    assert cierre["estado"] == 200
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT estado FROM cierre_dia WHERE dia = ?", (AYER.isoformat(),)).fetchone() == (
            "comida_cerrada",)
