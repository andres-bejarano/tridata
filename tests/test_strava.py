"""Unit 102 (REC-46 of `ver-fitness`): the Strava client, only to read the shoes in use (`strava.py`).

R1 (R-286, C-363, G-66, the criterion that carries the unit), R5 (R-290, C-368) and R7 (S-96). Strava is ALWAYS
doubled (`StravaDoble`, an injected transport): no test talks to Strava. Every value, id and token is invented.
"""
from __future__ import annotations

import json
import logging
import stat
import threading
import urllib.parse
import urllib.request

import pytest

from tridata import cli
from tridata.strava import (
    ALCANCE, AUTORIZAR, BASE_API, FALTA_CLIENTE, LIMITANDO, NO_RESPONDE, PERMISO, SIN_PERFIL, TOKEN_URL, ErrorStrava,
    Limitando, NoResponde, PermisoNoVale, SinConectar, Strava,
)
from tridata.zapatillas import VACIA, Zapatillas, leer_de_strava

AHORA = 1_790_000_000  # an invented «now», in seconds
ID_CLIENTE = "99999"
SECRETO = "secreto-inventado-del-cliente-0123456789abcdef"
ACCESO_VIEJO = "acceso-inventado-viejo-aaaaaaaaaaaaaaaa"
REFRESCO_VIEJO = "refresco-inventado-viejo-bbbbbbbbbbbbbbbb"
ACCESO_NUEVO = "acceso-inventado-nuevo-cccccccccccccccc"
REFRESCO_NUEVO = "refresco-inventado-nuevo-dddddddddddddddd"
CODIGO = "codigo-inventado-eeeeeeeeeeeeeeee"
SECRETOS = (SECRETO, ACCESO_VIEJO, REFRESCO_VIEJO, ACCESO_NUEVO, REFRESCO_NUEVO, CODIGO)

# what `GET /athlete` gives with `profile:read_all` (invented): 3 in use, one with `retired`, the others in metres
ZAPATILLAS = [
    {"id": "g1001", "primary": True, "name": "Asics SonicBlast", "distance": 303216.0, "resource_state": 2},
    {"id": "g1002", "primary": False, "name": "Asics Gel Nimbus 28", "distance": 141049.9, "resource_state": 2},
    {"id": "g1003", "primary": False, "name": "Asics Meta Speed Sky Tokyo", "distance": 103420.0,
     "resource_state": 2, "retired": False},
    {"id": "g1004", "primary": False, "name": "Nike Vomero 18", "distance": 812000.0, "resource_state": 2,
     "retired": True},
]


class StravaDoble:
    """Strava's two URLs, doubled: `/oauth/token` and `GET /athlete`. Writes down every call."""

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, str, dict, dict]] = []
        self.atleta: dict = {"id": 1, "firstname": "Inventado", "shoes": [dict(z) for z in ZAPATILLAS]}
        self.estado_atleta = 200
        self.estado_token = 200
        self.fallo_red: BaseException | None = None

    def __call__(self, metodo: str, url: str, cabeceras: dict, formulario: dict | None) -> tuple[int, object]:
        self.llamadas.append((metodo, url, dict(cabeceras), dict(formulario or {})))
        if self.fallo_red is not None:
            raise self.fallo_red
        if url == TOKEN_URL:
            if self.estado_token != 200:
                return self.estado_token, {"message": "Bad Request"}
            return 200, {"token_type": "Bearer", "access_token": ACCESO_NUEVO, "refresh_token": REFRESCO_NUEVO,
                         "expires_at": AHORA + 21600, "expires_in": 21600,
                         "athlete": {"id": 1, "firstname": "Inventado"}}
        if url == f"{BASE_API}/athlete":
            return self.estado_atleta, (self.atleta if self.estado_atleta == 200 else {"message": "Error"})
        raise AssertionError(f"a call to Strava nobody expected: {metodo} {url}")

    def a_la_api(self) -> list[tuple[str, str]]:
        return [(m, u) for m, u, _, _ in self.llamadas if u.startswith(BASE_API)]


@pytest.fixture
def cliente(tmp_path):
    ruta = tmp_path / "privado" / "strava-cliente.json"
    ruta.parent.mkdir()
    ruta.write_text(json.dumps({"client_id": ID_CLIENTE, "client_secret": SECRETO}), encoding="utf-8")
    return ruta


def _token(tmp_path, *, caduca: int, acceso: str = ACCESO_VIEJO):
    ruta = tmp_path / "datos" / "strava-token.json"
    ruta.parent.mkdir(exist_ok=True)
    ruta.write_text(json.dumps({"access_token": acceso, "refresh_token": REFRESCO_VIEJO, "expires_at": caduca}),
                    encoding="utf-8")
    ruta.chmod(0o600)
    return ruta


def _strava(tmp_path, cliente, doble, *, caduca: int = AHORA + 3600, **k) -> Strava:
    return Strava(_token(tmp_path, caduca=caduca), str(cliente), transporte=doble, reloj=lambda: AHORA, **k)


# -- R1 (R-286, C-363, G-66): the shoes in use, in km, and only GET ---------------------------------------------------

def test_lee_las_zapatillas_en_activo_en_km(tmp_path, cliente):  # R1 · R-286 · C-363 (portante)
    doble = StravaDoble()
    leidas = _strava(tmp_path, cliente, doble).zapatillas()
    assert leidas == [
        {"id": "g1001", "nombre": "Asics SonicBlast", "km": 303.2, "retirada": False},
        {"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 141.0, "retirada": False},
        {"id": "g1003", "nombre": "Asics Meta Speed Sky Tokyo", "km": 103.4, "retirada": False},
        {"id": "g1004", "nombre": "Nike Vomero 18", "km": 812.0, "retirada": True},
    ]
    assert doble.a_la_api() == [("GET", f"{BASE_API}/athlete")]
    (_, _, cabeceras, formulario) = doble.llamadas[0]
    assert cabeceras["Authorization"] == f"Bearer {ACCESO_VIEJO}"  # in the header, never in the URL
    assert ACCESO_VIEJO not in doble.llamadas[0][1] and formulario == {}


def test_la_lectura_del_dia_guarda_solo_las_en_activo(tmp_path, cliente):  # R1 · R-286 · C-363 (portante)
    doble = StravaDoble()
    repo = Zapatillas(tmp_path / "data.sqlite3")
    from datetime import datetime
    ayer = datetime(2026, 11, 21, 8, 0)
    # yesterday there was a fifth one that Strava no longer gives (sold, deleted): it stops being in use
    repo.guardar_lectura([{"id": "g1005", "nombre": "Trabuco 13", "km": 500.0, "retirada": False}], ayer)
    leer_de_strava(_strava(tmp_path, cliente, doble), repo, datetime(2026, 11, 22, 8, 0))
    activas = {z["id"]: z for z in repo.activas()}
    assert sorted(activas) == ["g1001", "g1002", "g1003"]
    assert [activas[i]["km"] for i in ("g1001", "g1002", "g1003")] == [303.2, 141.0, 103.4]
    assert all(z["fuente"] == "strava" for z in activas.values())
    todas = {z["id"]: z for z in repo.todas()}
    assert not todas["g1005"]["en_activo"]  # gone from Strava: not in use, not shown
    assert "g1004" not in activas  # retired in Strava
    # G-66: nothing but GET ever reaches Strava's API
    assert {m for m, _ in doble.a_la_api()} == {"GET"}


def test_sin_campo_retired_se_confia_en_la_lista(tmp_path, cliente):  # R1: `retired` is not confirmed by the docs
    doble = StravaDoble()
    for z in doble.atleta["shoes"]:
        z.pop("retired", None)
    leidas = _strava(tmp_path, cliente, doble).zapatillas()
    assert [z["retirada"] for z in leidas] == [False, False, False, False]


def test_sin_shoes_en_la_respuesta_es_que_falta_el_alcance(tmp_path, cliente):  # R1: without profile:read_all
    doble = StravaDoble()
    del doble.atleta["shoes"]
    with pytest.raises(PermisoNoVale) as exc:
        _strava(tmp_path, cliente, doble).zapatillas()
    assert str(exc.value) == SIN_PERFIL


# -- R5 (R-290, C-368): Strava fails, the km stay -----------------------------------------------------------------

@pytest.mark.parametrize(("preparar", "tipo", "motivo"), [
    (lambda d: setattr(d, "fallo_red", OSError("sin red")), NoResponde, NO_RESPONDE),
    (lambda d: setattr(d, "fallo_red", TimeoutError()), NoResponde, NO_RESPONDE),
    (lambda d: setattr(d, "estado_atleta", 503), NoResponde, NO_RESPONDE),
    (lambda d: setattr(d, "estado_atleta", 401), PermisoNoVale, PERMISO),
    (lambda d: setattr(d, "estado_atleta", 403), PermisoNoVale, PERMISO),
    (lambda d: setattr(d, "estado_atleta", 429), Limitando, LIMITANDO),
], ids=["sin-red", "timeout", "503", "401", "403", "429"])
def test_los_fallos_de_strava_dicen_por_que(tmp_path, cliente, preparar, tipo, motivo):  # R5 · C-368
    doble = StravaDoble()
    preparar(doble)
    with pytest.raises(tipo) as exc:
        _strava(tmp_path, cliente, doble).zapatillas()
    assert str(exc.value) == motivo
    assert isinstance(exc.value, ErrorStrava)


def test_el_permiso_caducado_que_no_se_renueva(tmp_path, cliente):  # R5: the refresh token no longer valid
    doble = StravaDoble()
    doble.estado_token = 400
    with pytest.raises(PermisoNoVale) as exc:
        _strava(tmp_path, cliente, doble, caduca=AHORA - 10).zapatillas()
    assert str(exc.value) == PERMISO
    assert doble.a_la_api() == []


@pytest.mark.parametrize("estado", [401, 429, 503])
def test_un_fallo_no_toca_ningun_km_y_guarda_el_motivo(tmp_path, cliente, estado):  # R5 · C-368
    from datetime import datetime
    repo = Zapatillas(tmp_path / "data.sqlite3")
    repo.guardar_lectura([{"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 141.0, "retirada": False}],
                         datetime(2026, 11, 21, 8, 0))
    antes = repo.todas()
    doble = StravaDoble()
    doble.estado_atleta = estado
    with pytest.raises(ErrorStrava) as exc:
        leer_de_strava(_strava(tmp_path, cliente, doble), repo, datetime(2026, 11, 22, 8, 0))
    despues = repo.todas()
    assert [(z["id"], z["km"], z["fuente"], z["fecha_km"]) for z in despues] == \
        [(z["id"], z["km"], z["fuente"], z["fecha_km"]) for z in antes]
    lectura = repo.estado_lectura()
    assert lectura["motivo"] == str(exc.value)
    assert lectura["intento"] == "2026-11-22T08:00" and lectura["ultima"] == "2026-11-21T08:00"


def test_shoes_vacio_teniendo_guardadas_no_borra_nada(tmp_path, cliente):  # R5: the anecdote of the forum
    from datetime import datetime
    repo = Zapatillas(tmp_path / "data.sqlite3")
    repo.guardar_lectura([{"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 141.0, "retirada": False}],
                         datetime(2026, 11, 21, 8, 0))
    doble = StravaDoble()
    doble.atleta["shoes"] = []
    with pytest.raises(ErrorStrava) as exc:
        leer_de_strava(_strava(tmp_path, cliente, doble), repo, datetime(2026, 11, 22, 8, 0))
    assert str(exc.value) == VACIA
    (nimbus,) = repo.activas()
    assert nimbus["km"] == 141.0 and nimbus["en_activo"]
    assert repo.estado_lectura()["motivo"] == VACIA


def test_sin_token_no_habla_con_strava(tmp_path, cliente):  # R7: without a token nothing talks to Strava
    doble = StravaDoble()
    strava = Strava(tmp_path / "no-hay-token.json", str(cliente), transporte=doble, reloj=lambda: AHORA)
    assert not strava.conectado and strava.configurado
    with pytest.raises(SinConectar):
        strava.zapatillas()
    assert doble.llamadas == []


# -- R7 (S-96): the token, renewed and saved at once, 600; nothing secret in a log --------------------------------

def test_renueva_el_token_caducado_y_guarda_el_refresco_nuevo(tmp_path, cliente, caplog):  # R7
    caplog.set_level(logging.DEBUG)
    doble = StravaDoble()
    strava = _strava(tmp_path, cliente, doble, caduca=AHORA - 10)
    assert len(strava.zapatillas()) == 4
    metodo, url, _, formulario = doble.llamadas[0]
    assert (metodo, url) == ("POST", TOKEN_URL)
    assert formulario == {"client_id": ID_CLIENTE, "client_secret": SECRETO, "grant_type": "refresh_token",
                          "refresh_token": REFRESCO_VIEJO}
    assert doble.llamadas[1][2]["Authorization"] == f"Bearer {ACCESO_NUEVO}"
    token = tmp_path / "datos" / "strava-token.json"
    guardado = json.loads(token.read_text(encoding="utf-8"))
    assert guardado == {"access_token": ACCESO_NUEVO, "refresh_token": REFRESCO_NUEVO, "expires_at": AHORA + 21600}
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert list(token.parent.iterdir()) == [token]  # written atomically: no temporary file left behind
    for secreto in SECRETOS:
        assert secreto not in caplog.text


def test_el_token_vigente_no_se_renueva(tmp_path, cliente):  # R7: 2 calls a day at most
    doble = StravaDoble()
    _strava(tmp_path, cliente, doble).zapatillas()
    assert [u for _, u, _, _ in doble.llamadas] == [f"{BASE_API}/athlete"]


def test_un_fallo_nunca_lleva_secretos(tmp_path, cliente, caplog):  # R7
    caplog.set_level(logging.DEBUG)
    doble = StravaDoble()
    doble.fallo_red = OSError(f"falló con {ACCESO_VIEJO} y {SECRETO}")  # an exception that carries a secret
    with pytest.raises(ErrorStrava) as exc:
        _strava(tmp_path, cliente, doble, caduca=AHORA - 10).zapatillas()
    for secreto in SECRETOS:
        assert secreto not in str(exc.value) and secreto not in caplog.text


def _navegador_que_acepta(*, alcance: str = "read,profile:read_all", estado: str | None = None,
                          error: str | None = None):
    """The browser of this PC: Andrés says yes in Strava, which sends him back to the local address with the code."""
    visto: dict = {}

    def abrir(url: str) -> None:
        visto["url"] = url
        consulta = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        vuelta = consulta["redirect_uri"][0]
        datos = {"state": estado if estado is not None else consulta["state"][0], "scope": alcance}
        if error:
            datos["error"] = error
        else:
            datos["code"] = CODIGO

        def ir() -> None:
            with urllib.request.urlopen(f"{vuelta}?{urllib.parse.urlencode(datos)}", timeout=5) as r:  # noqa: S310
                visto["pagina"] = r.read().decode("utf-8")

        hilo = threading.Thread(target=ir)
        hilo.start()
        visto["hilo"] = hilo

    return abrir, visto


def test_conectar_abre_el_permiso_y_guarda_el_token(tmp_path, cliente, caplog, capsys):  # R7 · S-96
    caplog.set_level(logging.DEBUG)
    doble = StravaDoble()
    abrir, visto = _navegador_que_acepta()
    token = tmp_path / "datos" / "strava-token.json"
    strava = Strava(token, str(cliente), transporte=doble, navegador=abrir, reloj=lambda: AHORA, espera_s=10)
    assert not strava.conectado
    strava.conectar()
    visto["hilo"].join(5)
    url = urllib.parse.urlsplit(visto["url"])
    consulta = urllib.parse.parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == AUTORIZAR
    assert consulta["client_id"] == [ID_CLIENTE] and consulta["scope"] == [ALCANCE]
    assert consulta["response_type"] == ["code"]
    assert consulta["redirect_uri"][0].startswith("http://127.0.0.1:")
    assert len(consulta["state"][0]) >= 16
    assert SECRETO not in visto["url"]
    _, url_token, _, formulario = doble.llamadas[0]
    assert url_token == TOKEN_URL
    assert formulario == {"client_id": ID_CLIENTE, "client_secret": SECRETO, "code": CODIGO,
                          "grant_type": "authorization_code"}
    assert strava.conectado and stat.S_IMODE(token.stat().st_mode) == 0o600
    assert json.loads(token.read_text(encoding="utf-8"))["refresh_token"] == REFRESCO_NUEVO
    assert "athlete" not in json.loads(token.read_text(encoding="utf-8"))  # nothing of the athlete kept
    assert "TriData" in visto["pagina"]
    salida = capsys.readouterr()
    for secreto in SECRETOS:
        assert secreto not in caplog.text and secreto not in salida.err and secreto not in salida.out
        assert secreto not in visto["pagina"]


def test_conectar_con_otro_state_no_guarda_nada(tmp_path, cliente):  # R7: the `state` is checked
    doble = StravaDoble()
    abrir, visto = _navegador_que_acepta(estado="otro-state-inventado")
    token = tmp_path / "datos" / "strava-token.json"
    strava = Strava(token, str(cliente), transporte=doble, navegador=abrir, reloj=lambda: AHORA, espera_s=2)
    with pytest.raises(ErrorStrava):
        strava.conectar()
    visto["hilo"].join(5)
    assert not token.exists() and doble.llamadas == []


def test_conectar_sin_ver_el_perfil_no_vale(tmp_path, cliente):  # R1: without profile:read_all no shoes come
    doble = StravaDoble()
    abrir, visto = _navegador_que_acepta(alcance="read")
    token = tmp_path / "datos" / "strava-token.json"
    strava = Strava(token, str(cliente), transporte=doble, navegador=abrir, reloj=lambda: AHORA, espera_s=10)
    with pytest.raises(PermisoNoVale) as exc:
        strava.conectar()
    visto["hilo"].join(5)
    assert str(exc.value) == SIN_PERFIL
    assert not token.exists()


def test_conectar_sin_cliente_dice_que_falta(tmp_path):  # R7
    strava = Strava(tmp_path / "strava-token.json", None, transporte=StravaDoble())
    assert not strava.configurado
    with pytest.raises(ErrorStrava) as exc:
        strava.conectar()
    assert str(exc.value) == FALTA_CLIENTE
    assert "TRIDATA_STRAVA_CLIENTE" in FALTA_CLIENTE


# -- R5: `tridata strava`, the step of the routine ----------------------------------------------------------------

def test_tridata_strava_bien(tmp_path, cliente, capsys):  # R1 · R5
    doble = StravaDoble()
    token = _token(tmp_path, caduca=AHORA + 3600)
    codigo = cli._cmd_strava(tmp_path / "data.sqlite3", token, str(cliente), transporte=doble, reloj=lambda: AHORA)
    assert codigo == 0
    assert "strava OK: 3 zapatillas en activo" in capsys.readouterr().out
    assert len(Zapatillas(tmp_path / "data.sqlite3").activas()) == 3


def test_tridata_strava_que_falla_sale_distinto_de_cero(tmp_path, cliente, capsys):  # R5
    doble = StravaDoble()
    doble.estado_atleta = 429
    token = _token(tmp_path, caduca=AHORA + 3600)
    codigo = cli._cmd_strava(tmp_path / "data.sqlite3", token, str(cliente), transporte=doble, reloj=lambda: AHORA)
    assert codigo != 0
    salida = capsys.readouterr()
    assert f"strava FAILED: {LIMITANDO}" in salida.err
    for secreto in SECRETOS:
        assert secreto not in salida.err and secreto not in salida.out


@pytest.mark.parametrize("que_falta", ["cliente", "token"])
def test_tridata_strava_sin_conectar_se_salta(tmp_path, cliente, capsys, que_falta):  # R5: one line, exit 0
    doble = StravaDoble()
    token = tmp_path / "strava-token.json" if que_falta == "token" else _token(tmp_path, caduca=AHORA + 3600)
    codigo = cli._cmd_strava(tmp_path / "data.sqlite3", token, None if que_falta == "cliente" else str(cliente),
                             transporte=doble, reloj=lambda: AHORA)
    assert codigo == 0
    assert "strava: sin conectar" in capsys.readouterr().out
    assert doble.llamadas == []
