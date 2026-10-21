"""E2E of the phone screen (unit 010): the real app in a real Chromium, with every outside service doubled.

`crear_app` runs with uvicorn in a thread, on a free port, over a temporary SQLite; the Claude
interpreter, the Whisper transcriber and «Actualizar Garmin» are `MagicMock`s and the Garmin data
are a temporary `DataStore` with one invented activity. «Today» is fixed on the server and on the
phone's clock. Every value is invented.

Optional: needs the `e2e` extra (`python -m pip install -e ".[dev,e2e]"` and, once,
`python -m playwright install chromium`). Without Playwright these tests are skipped by
`test_interfaz_e2e.py`. With `TRIDATA_CAPTURAS=<folder>` the screenshots are saved there.
"""
from __future__ import annotations

import os
import pwd
import socket
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tridata.ia import AlimentoInterpretado, Interpretacion, ObjetivoDiaInterpretado, PesoInterpretado
from tridata.models import Activity
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, ObjetivosDia, RegistroComidas, RegistroPeso
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 10, 21)  # a Wednesday
AYER = HOY - timedelta(days=1)
RELOJ_DEL_MOVIL = datetime(2026, 10, 21, 10, 0, 0)
ANCHO, ALTO = 390, 844
AJUSTES_DE_PRUEBA = {"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000, "objetivo_pct": -10}
TEXTO_DICTADO = "avena 60 g, peso 72,4, hoy mantenimiento y algo raro"

# `scripts/ci/full-suite` runs with a throwaway HOME: the browsers stay in the real user's cache.
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    _cache = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".cache" / "ms-playwright"
    if _cache.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_cache)


def interpretacion_de_prueba(texto: str, hoy: date) -> Interpretacion:
    """The interpreter double: «avena 60 g, peso 72,4, hoy mantenimiento y algo raro», every piece."""
    return Interpretacion(
        alimentos=[AlimentoInterpretado(nombre="avena", cantidad=60, unidad="g", dia=hoy, kcal=228, proteina_g=None,
                                        hidratos_g=None, grasa_g=None, estimado=True, dudoso=False, supuestos=[])],
        peso=PesoInterpretado(kg=72.4, dia=hoy, nota=None),
        objetivo_dia=ObjetivoDiaInterpretado(tipo="descanso", porcentaje=0, niveles=None, entreno=None, pasos=None),
        dudosas=["algo raro"],
    )


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Servidor:
    """The app over a temporary SQLite, served on 127.0.0.1:<free port>."""

    def __init__(self, carpeta: Path, ajustes: dict | None) -> None:
        import uvicorn

        carpeta.mkdir(parents=True, exist_ok=True)
        self.db = carpeta / "data.sqlite3"
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.ajustes = Ajustes(self.db)
        if ajustes:
            self.ajustes.cambiar(ajustes, desde=DESDE_SIEMPRE)
        self.store = DataStore(self.db)
        self.store.save_activities([Activity(
            activity_id="e2e-natacion", activity_date=HOY - timedelta(days=3), activity_type="lap_swimming",
            name="Natación técnica", duration_seconds=2400, calories=310)])
        self.interprete = MagicMock()
        self.interprete.interpretar.side_effect = interpretacion_de_prueba
        self.transcriptor = MagicMock(spec=["transcribir"])
        self.transcriptor.transcribir.return_value = TEXTO_DICTADO
        self.actualizar_garmin = MagicMock()
        app = crear_app(self.registro, self.interprete, self.ajustes, hoy=lambda: HOY, pesos=self.pesos,
                        objetivos=self.objetivos, store=self.store, actualizar_garmin=self.actualizar_garmin,
                        transcriptor=self.transcriptor)
        self.puerto = _puerto_libre()
        self.url = f"http://127.0.0.1:{self.puerto}"
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.puerto, log_level="warning"))
        self._hilo = threading.Thread(target=self._server.run, name="e2e-uvicorn", daemon=True)
        self._hilo.start()
        limite = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > limite:
                raise RuntimeError("the e2e server did not start")
            time.sleep(0.02)

    def parar(self) -> None:
        self._server.should_exit = True
        self._hilo.join(timeout=10)

    # -- invented data ----------------------------------------------------------------------

    def apuntar(self, nombre: str, dia: date, cantidad: float | None, kcal: float | None, *, hoy: date = HOY,
                unidad: str = "g", estimado: bool = False) -> dict:
        alimento = Alimento(nombre=nombre, dia=dia, cantidad=cantidad, unidad=unidad if cantidad else None,
                            kcal=kcal, estimado=estimado)
        return self.registro.guardar([alimento], hoy=hoy, texto=nombre, sin_cambios=True)[0]

    def dia_de_prueba(self) -> None:
        """760 kcal eaten today; salmon (374) and fruit (no quantity) planned yesterday for today."""
        self.apuntar("Avena", HOY, 60, 228, estimado=True)
        self.apuntar("Arroz con pollo", HOY, 350, 532)
        self.apuntar("Salmón al horno", HOY, 180, 374, hoy=AYER)
        self.apuntar("Fruta", HOY, None, None, hoy=AYER)

    def pesos_de_prueba(self) -> None:
        """72,4 today, 72,6 yesterday, 73,0 ten days ago and 74,1 forty days ago."""
        for hace, kg in ((40, 74.1), (10, 73.0), (1, 72.6), (0, 72.4)):
            self.pesos.apuntar(HOY - timedelta(days=hace), kg, hoy=HOY)


@pytest.fixture
def arrancar(tmp_path):
    """`arrancar(ajustes=...)` -> a running `Servidor`; the settings of the test day by default."""
    servidores = []

    def _arrancar(ajustes: dict | None = None, *, sin_ajustes: bool = False) -> Servidor:
        datos = None if sin_ajustes else {**AJUSTES_DE_PRUEBA, **(ajustes or {})}
        servidor = Servidor(tmp_path / f"s{len(servidores)}", datos)
        servidores.append(servidor)
        return servidor

    yield _arrancar
    for s in servidores:
        s.parar()


@pytest.fixture
def servidor(arrancar) -> Servidor:
    return arrancar()


@pytest.fixture(scope="module")
def navegador():
    # module scope, not session: the sync API keeps an event loop running while it lives, and the
    # tests that run after tests/e2e/ (test_mcp_server.py uses asyncio.run) need it stopped
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
        yield chromium
        chromium.close()


@pytest.fixture
def abrir(navegador):
    """`abrir(servidor, ruta="/", oscuro=False)` -> a page of a new phone (390 × 844, touch, es-ES).

    The page fails the test if it throws a JavaScript error.
    """
    contextos, errores = [], []

    def _abrir(servidor: Servidor, ruta: str = "/", *, oscuro: bool = False, service_worker: bool = False):
        contexto = navegador.new_context(
            viewport={"width": ANCHO, "height": ALTO}, device_scale_factor=2, has_touch=True, is_mobile=True,
            locale="es-ES", timezone_id="Europe/Madrid", color_scheme="dark" if oscuro else "light",
            permissions=["microphone"], service_workers="allow" if service_worker else "block",
        )
        contextos.append(contexto)
        pagina = contexto.new_page()
        pagina.on("pageerror", lambda e: errores.append(str(e)))
        pagina.clock.install(time=RELOJ_DEL_MOVIL)
        pagina.goto(servidor.url + ruta)
        return pagina

    yield _abrir
    for c in contextos:
        c.close()
    assert errores == [], f"JavaScript errors on the page: {errores}"


@pytest.fixture
def capturar():
    """`capturar(pagina, nombre)` saves a PNG in $TRIDATA_CAPTURAS (nothing if it is not set)."""
    carpeta = os.environ.get("TRIDATA_CAPTURAS")

    def _capturar(pagina, nombre: str, *, completa: bool = False) -> None:
        if not carpeta:
            return
        Path(carpeta).mkdir(parents=True, exist_ok=True)
        pagina.wait_for_timeout(350)  # transitions end
        pagina.screenshot(path=str(Path(carpeta) / f"{nombre}.png"), full_page=completa)

    return _capturar
