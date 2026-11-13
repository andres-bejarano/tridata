"""Bug 078: only the app itself may read and change the data, not any other page open in the browser.

`web/origen.py` over `crear_app`, with a temporary SQLite, the interpreter mocked (no API) and the headers a browser
would send written by hand. Every name here is invented.
"""
import asyncio
import logging
from datetime import date
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, Alimento, RegistroComidas
from tridata.web import crear_app
from tridata.web.calendario import Sincronizacion
from tridata.web.origen import de_env, host_permitido

HOY = date(2026, 3, 4)
OTRA_WEB = "https://otra-web.example"


@pytest.fixture
def registro(tmp_path):
    return RegistroComidas(tmp_path / "data.sqlite3")


def _ajustes(tmp_path):
    ajustes = Ajustes(tmp_path / "data.sqlite3")
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
    return ajustes


@pytest.fixture
def cliente(registro, tmp_path):
    return TestClient(crear_app(registro, MagicMock(), _ajustes(tmp_path), hoy=lambda: HOY))


@pytest.fixture
def yogur(registro):
    alimento = Alimento(nombre="yogur", dia=HOY, cantidad=125.0, unidad="g", kcal=80.0, estimado=True)
    return registro.guardar([alimento], hoy=HOY, texto="un yogur", sin_cambios=True)[0]


def _borrar(cliente, yogur, **cabeceras):
    """The POST of the report: what a form or a simple `fetch` of another page sends, without preflight."""
    return cliente.post(f"/api/alimento/{yogur['id']}/borrar", content='{"confirmado": true}',
                        headers={"Content-Type": "text/plain", **cabeceras})


# -- the bug ---------------------------------------------------------------------------

def test_otra_web_no_borra_un_alimento(cliente, registro, yogur):  # the report: today it deletes
    respuesta = _borrar(cliente, yogur, Origin=OTRA_WEB)
    assert respuesta.status_code == 403
    assert registro.apuntes_del_dia(HOY) == [yogur]


# -- R1: a write another site sends through the browser is rejected before touching anything ---------------

MOVIL = "maquina.tail0000.ts.net"


@pytest.mark.parametrize("cabeceras", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},  # another program of the same machine or tailnet is not the app
    {"Sec-Fetch-Site": "cross-site", "Origin": "http://testserver"},  # what the browser says wins
    {"Origin": "null"},  # a sandboxed frame, a file opened from the disk
    {"Origin": "http://testserver:3000"},  # the same name, another port: another program
    {"Origin": "https://testserver.otra-web.example"},
    {"Origin": OTRA_WEB, "X-Forwarded-Host": MOVIL},
    {"Origin": ""},
], ids=lambda c: " ".join(f"{k}={v}" for k, v in c.items()))
def test_una_escritura_de_otro_sitio_se_rechaza_sin_tocar_nada(cliente, registro, yogur, cabeceras):
    respuesta = _borrar(cliente, yogur, **cabeceras)
    assert respuesta.status_code == 403
    assert respuesta.json() == {"error": "Petición rechazada: no viene de la propia app."}
    assert registro.apuntes_del_dia(HOY) == [yogur]


@pytest.mark.parametrize("metodo", ["POST", "PUT", "PATCH", "DELETE"])
def test_cualquier_metodo_que_no_sea_de_lectura_se_rechaza(cliente, metodo):  # also the routes still to come
    respuesta = cliente.request(metodo, "/api/lo-que-venga", headers={"Origin": OTRA_WEB})
    assert respuesta.status_code == 403


def test_con_json_y_origen_ajeno_tampoco_entra(cliente, registro, yogur):
    respuesta = cliente.post(f"/api/alimento/{yogur['id']}/borrar", json={"confirmado": True},
                             headers={"Origin": OTRA_WEB})
    assert respuesta.status_code == 403
    assert registro.apuntes_del_dia(HOY) == [yogur]


# -- R2: a name that is not this machine's gets nothing, reading included --------------------------------

@pytest.mark.parametrize("ruta", ["/", "/api/dia", "/api/peso", "/static/app.js", "/sw.js", "/no-existe"])
def test_por_un_nombre_ajeno_no_se_lee_nada(cliente, ruta):
    respuesta = cliente.get(ruta, headers={"Host": "malo.example"})
    assert respuesta.status_code == 403
    assert respuesta.json() == {"error": "Petición rechazada: TriData no atiende por ese nombre."}


def test_por_un_nombre_ajeno_tampoco_se_escribe(cliente, registro, yogur):
    assert _borrar(cliente, yogur, Host="malo.example:8765").status_code == 403
    assert registro.apuntes_del_dia(HOY) == [yogur]


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.0.0.1:8765", "100.64.0.1:8765", "[::1]", "[::1]:8765", "[fd7a:115c:a1e0::1]:8765",
    "localhost", "localhost:8765", "LocalHost:8765", "testserver", "maquina", "mi-maquina_2:8765",
    "maquina.tail0000.ts.net", "Maquina.Tail0000.TS.net:443", "casa.example", "otra.example:8765",
])
def test_nombres_de_esta_maquina(host):
    assert host_permitido(host, frozenset({"casa.example", "otra.example"}))


@pytest.mark.parametrize("host", [
    None, "", ":8765", "malo.example", "malo.example:8765", "127.0.0.1.malo.example", "localhost.malo.example",
    "maquina.tail0000.ts.net.malo.example", "malots.net", ".ts.net", "ts.net", "localhost.", "maquina.tail0000.ts.net.",
    "999.1.1.1", "[::1", "[malo.example]", "malo.example:80:8765", "local host", "casa.example.malo.example",
    "localhost,malo.example", "malo.example@localhost", "localhost/malo.example",
])
def test_nombres_que_no_son_de_esta_maquina(host):
    assert not host_permitido(host, frozenset({"casa.example"}))


def test_los_nombres_de_env_valen_y_sin_ellos_no(registro, tmp_path, monkeypatch):
    def cliente():
        return TestClient(crear_app(registro, MagicMock(), _ajustes(tmp_path), hoy=lambda: HOY))

    monkeypatch.delenv("TRIDATA_WEB_HOSTS", raising=False)
    assert cliente().get("/sw.js", headers={"Host": "casa.example"}).status_code == 403
    monkeypatch.setenv("TRIDATA_WEB_HOSTS", " Casa.example:8765 , otra.example,, ")
    assert de_env() == frozenset({"casa.example", "otra.example"})
    con_nombres = cliente()
    assert con_nombres.get("/sw.js", headers={"Host": "casa.example"}).status_code == 200
    assert con_nombres.get("/sw.js", headers={"Host": "OTRA.example:8765"}).status_code == 200
    assert con_nombres.get("/sw.js", headers={"Host": "malo.example"}).status_code == 403


def _asgi(app, cabeceras, metodo="GET", ruta="/sw.js"):
    """A request as the server hands it over, to send what `TestClient` does not let (no `Host`, two of them)."""
    enviado = []
    pendientes = [{"type": "http.request", "body": b"", "more_body": False}]

    async def recibir():
        return pendientes.pop() if pendientes else {"type": "http.disconnect"}

    async def enviar(mensaje):
        enviado.append(mensaje)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": metodo, "scheme": "http",
             "path": ruta, "raw_path": ruta.encode(), "query_string": b"", "root_path": "", "headers": cabeceras,
             "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8765)}
    asyncio.run(app(scope, recibir, enviar))
    return enviado[0]["status"]


@pytest.mark.parametrize(("cabeceras", "estado"), [
    ([(b"host", b"127.0.0.1:8765")], 200),
    ([], 403),
    ([(b"host", b"")], 403),
    ([(b"host", b"127.0.0.1:8765"), (b"host", b"malo.example")], 403),
    ([(b"Host", b"malo.example")], 403),
])
def test_sin_host_o_con_dos_no_se_atiende(cliente, cabeceras, estado):
    assert _asgi(cliente.app, cabeceras) == estado


# -- R3: what works today goes on -----------------------------------------------------------------------

def test_sin_cabeceras_de_navegador_se_escribe_como_hoy(cliente, registro, yogur):  # curl, scripts, the tests
    assert _borrar(cliente, yogur).status_code == 200
    assert registro.apuntes_del_dia(HOY) == []


@pytest.mark.parametrize("cabeceras", [
    {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"},
    {"Host": "localhost:8765", "Origin": "http://localhost:8765", "Sec-Fetch-Site": "same-origin"},
    {"Host": MOVIL, "Origin": f"https://{MOVIL}", "Sec-Fetch-Site": "same-origin"},  # the proxy keeps the name
    {"Host": "127.0.0.1:8765", "X-Forwarded-Host": MOVIL, "Origin": f"https://{MOVIL}",
     "Sec-Fetch-Site": "same-origin"},  # or it puts its own and says the other one
    {"Host": "localhost:8765", "Sec-Fetch-Site": "none"},
    # a browser that does not send Sec-Fetch-Site: the Origin is where the request came by
    {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"},
    {"Host": "LocalHost:8765", "Origin": "http://localhost:8765"},
    {"Host": MOVIL, "Origin": f"https://{MOVIL}"},
    {"Host": "127.0.0.1:8765", "X-Forwarded-Host": MOVIL, "Origin": f"https://{MOVIL}"},
    {"Host": "[::1]:8765", "Origin": "http://[::1]:8765"},
], ids=lambda c: " ".join(f"{k}={v}" for k, v in c.items()))
def test_la_app_en_el_pc_y_en_el_movil_escribe_igual(cliente, registro, yogur, cabeceras):
    respuesta = cliente.post(f"/api/alimento/{yogur['id']}/borrar", json={"confirmado": True}, headers=cabeceras)
    assert respuesta.status_code == 200, respuesta.text
    assert registro.apuntes_del_dia(HOY) == []


def test_leer_no_mira_de_donde_viene(cliente):  # a link in another page that opens the app is not a write
    for ruta in ("/", "/api/dia", "/static/app.js"):
        respuesta = cliente.get(ruta, headers={"Origin": OTRA_WEB, "Sec-Fetch-Site": "cross-site"})
        assert respuesta.status_code == 200, ruta
    assert cliente.head("/sw.js", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    assert cliente.options("/api/dia", headers={"Origin": OTRA_WEB}).status_code == 405  # the route answers, not a 403


def test_el_calendario_se_despierta_tras_un_2xx_y_no_tras_un_403(registro, tmp_path, monkeypatch):
    despertado = []
    monkeypatch.setattr(Sincronizacion, "pronto", lambda self: despertado.append(1))
    cliente = TestClient(crear_app(registro, MagicMock(), _ajustes(tmp_path), hoy=lambda: HOY,
                                   actualizar_garmin=lambda dia: None))
    rechazada = cliente.post("/api/garmin/actualizar", headers={"Origin": OTRA_WEB})
    assert (rechazada.status_code, despertado) == (403, [])
    aceptada = cliente.post("/api/garmin/actualizar", headers={"Origin": "http://testserver"})
    assert (aceptada.status_code, despertado) == (200, [1])


# -- R4: the 403 gives nothing away, and leaves a line in the log ------------------------------------------

def test_el_403_no_lleva_datos_ni_lo_que_decian_las_cabeceras(cliente, yogur, caplog):
    caplog.set_level(logging.WARNING, logger="tridata.web.origen")
    secreto = "valor-de-cabecera-inventado"
    de_otra_web = cliente.post(f"/api/alimento/{yogur['id']}/borrar", content='{"confirmado": true, "cuerpo": "x-y-z"}',
                               headers={"Content-Type": "text/plain", "Origin": f"https://{secreto}.example",
                                        "X-Forwarded-Host": f"otro-{secreto}.example",
                                        "Referer": f"https://{secreto}/"})
    por_otro_nombre = cliente.get("/api/dia", headers={"Host": f"{secreto}.example"})
    for respuesta in (de_otra_web, por_otro_nombre):
        assert respuesta.status_code == 403
        assert list(respuesta.json()) == ["error"]
        assert secreto not in respuesta.text and "yogur" not in respuesta.text
        assert secreto not in str(respuesta.headers)
    lineas = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "tridata.web.origen"]
    assert lineas == [
        ("WARNING", f"Request rejected (write from another site): POST '/api/alimento/{yogur['id']}/borrar'"),
        ("WARNING", "Request rejected (host not allowed): GET '/api/dia'"),
    ]
    assert secreto not in caplog.text and "x-y-z" not in caplog.text


def test_lo_que_pasa_no_deja_linea_en_el_log(cliente, yogur, caplog):
    caplog.set_level(logging.WARNING, logger="tridata.web.origen")
    assert _borrar(cliente, yogur).status_code == 200
    assert [r for r in caplog.records if r.name == "tridata.web.origen"] == []
