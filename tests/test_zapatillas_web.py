"""Unit 102 (REC-46 of `ver-fitness`): the web of the shoes (`web/zapatillas.py`), Hoy and Ajustes › Strava.

    GET  /api/zapatillas        each shoe in use with its km, cap, what is left and where the number comes from
    POST /api/zapatillas/{id}   {"km"} or {"tope"}; 400 with why, 404 if it is not there
    GET  /api/strava            connected or not, the last read and, without the client, what is missing
    POST /api/strava/conectar   Strava's permission (doubled) and the first read

R2 (R-287, C-364), R3 (R-288, C-365), R4 (C-366, C-367), R5 (C-368), R6 (C-369) and R7 (S-96). Strava is ALWAYS
doubled (`test_strava.StravaDoble`). Every value is invented.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient
from test_strava import AHORA, REFRESCO_NUEVO, SECRETO, SECRETOS, StravaDoble

from tridata.nutricion import DESDE_SIEMPRE, Ajustes, ObjetivosDia, RegistroComidas
from tridata.strava import FALTA_CLIENTE, SIN_CONECTAR, Strava
from tridata.web import crear_app
from tridata.zapatillas import Zapatillas

HOY = date(2026, 11, 22)
AYER = datetime(2026, 11, 21, 8, 0)
DE_HOY = [
    {"id": "g1001", "nombre": "Asics SonicBlast", "km": 303.2, "retirada": False},
    {"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 141.0, "retirada": False},
    {"id": "g1003", "nombre": "Asics Meta Speed Sky Tokyo", "km": 103.4, "retirada": False},
]


class Web:
    def __init__(self, tmp_path, *, configurado: bool = True, conectado: bool = False,
                 conectar: bool = True) -> None:
        self.db = tmp_path / "data.sqlite3"
        ajustes = Ajustes(self.db)
        ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000}, desde=DESDE_SIEMPRE)
        self.token = tmp_path / "strava-token.json"
        cliente = tmp_path / "privado" / "strava-cliente.json"
        cliente.parent.mkdir()
        if configurado:
            cliente.write_text(json.dumps({"client_id": "99999", "client_secret": SECRETO}), encoding="utf-8")
        if conectado:
            self.token.write_text(json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": AHORA + 3600}),
                                  encoding="utf-8")
        self.doble = StravaDoble()
        self.abiertas: list[str] = []
        self.strava = Strava(self.token, str(cliente) if configurado else None, transporte=self.doble,
                             navegador=self._navegador if conectar else self._no_vuelve, reloj=lambda: AHORA,
                             espera_s=1)
        self.zapatillas = Zapatillas(self.db)
        self.ahora = datetime(2026, 11, 22, 10, 0)
        self.app = crear_app(RegistroComidas(self.db), MagicMock(), ajustes, hoy=lambda: HOY,
                             objetivos=ObjetivosDia(self.db), actualizar_garmin=MagicMock(), ahora=lambda: self.ahora,
                             strava=self.strava, zapatillas=self.zapatillas)
        self.cliente = TestClient(self.app)

    def _navegador(self, url: str) -> None:
        """Andrés says yes in Strava: the local server gets the code (`test_strava` checks the real round trip)."""
        import threading
        import urllib.parse
        import urllib.request

        self.abiertas.append(url)
        consulta = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        vuelta = (f"{consulta['redirect_uri'][0]}?state={consulta['state'][0]}&code=codigo-inventado"
                  "&scope=read,profile:read_all")
        threading.Thread(target=lambda: urllib.request.urlopen(vuelta, timeout=5).read()).start()  # noqa: S310

    def _no_vuelve(self, url: str) -> None:
        self.abiertas.append(url)  # Andrés never answers in Strava


@pytest.fixture
def web(tmp_path) -> Web:
    return Web(tmp_path)


# -- R2 (R-287, C-364): GET /api/zapatillas --------------------------------------------------------------------------

def test_api_zapatillas(web):  # R2 · C-364
    web.zapatillas.guardar_lectura(DE_HOY, AYER)
    r = web.cliente.get("/api/zapatillas")
    assert r.status_code == 200
    datos = r.json()
    sonic = next(z for z in datos["zapatillas"] if z["id"] == "g1001")
    assert {k: sonic[k] for k in ("nombre", "km", "tope", "quedan", "estado", "fuente", "fecha_km", "texto_fuente")} \
        == {"nombre": "Asics SonicBlast", "km": 303.2, "tope": 700, "quedan": 397, "estado": "bien",
            "fuente": "strava", "fecha_km": "2026-11-21", "texto_fuente": "de Strava, el 21/11"}
    assert datos["aviso_lectura"] is None
    assert datos["strava"]["conectado"] is False and datos["strava"]["configurado"] is True


def test_api_zapatillas_sin_ninguna(web):  # R2 · C-367
    datos = web.cliente.get("/api/zapatillas").json()
    assert datos["zapatillas"] == []


# -- R3 (R-288, C-365) and R6 (R-291): POST /api/zapatillas/{id} ----------------------------------------------------

def test_cambiar_el_tope_en_la_web(web):  # R3 · C-365
    web.zapatillas.guardar_lectura(DE_HOY, AYER)
    r = web.cliente.post("/api/zapatillas/g1003", json={"tope": 350})
    assert r.status_code == 200
    meta = next(z for z in r.json()["zapatillas"] if z["id"] == "g1003")
    assert meta["tope"] == 350 and meta["quedan"] == 247


@pytest.mark.parametrize("cuerpo", [{"tope": 20}, {"tope": "abc"}, {"km": -5}, {"km": "muchos"}, {},
                                    {"km": 160, "tope": 400}, {"otro": 1}])
def test_lo_que_no_vale_es_400_con_motivo(web, cuerpo):  # R3 · R6
    web.zapatillas.guardar_lectura(DE_HOY, AYER)
    antes = web.zapatillas.todas()
    r = web.cliente.post("/api/zapatillas/g1003", json=cuerpo)
    assert r.status_code == 400 and r.json()["error"]
    assert web.zapatillas.todas() == antes


def test_una_que_no_esta_es_404(web):
    r = web.cliente.post("/api/zapatillas/g-no-existe", json={"tope": 400})
    assert r.status_code == 404 and r.json()["error"]


def test_km_a_mano_en_la_web(web):  # R6 · C-369
    web.zapatillas.guardar_lectura(DE_HOY, AYER)
    r = web.cliente.post("/api/zapatillas/g1002", json={"km": 160})
    assert r.status_code == 200
    nimbus = next(z for z in r.json()["zapatillas"] if z["id"] == "g1002")
    assert (nimbus["km"], nimbus["fuente"], nimbus["texto_fuente"]) == (160.0, "andres", "dicho por ti, hoy")


def test_hoy_no_se_pudo_leer_en_la_web(web):  # R5 · C-368
    web.zapatillas.guardar_lectura(DE_HOY, AYER)
    web.zapatillas.apuntar_fallo("Strava no responde", datetime(2026, 11, 22, 8, 0))
    datos = web.cliente.get("/api/zapatillas").json()
    assert datos["aviso_lectura"] == "Hoy no se pudo leer Strava: Strava no responde."
    nimbus = next(z for z in datos["zapatillas"] if z["id"] == "g1002")
    assert nimbus["texto"].endswith("de Strava, el 21/11 · hoy no se pudo leer")


# -- R4 (R-289, C-366, C-367): Hoy ----------------------------------------------------------------------------------

def test_hoy_sin_zapatillas_cerca_del_tope_no_trae_nada(web):  # R4 · C-367
    web.zapatillas.guardar_lectura(DE_HOY, AYER)
    datos = web.cliente.get("/api/hoy").json()
    assert datos["zapatillas"] == []


def test_hoy_avisa_de_la_que_toca_cambiar(web):  # R4 · C-366
    web.zapatillas.guardar_lectura([*DE_HOY[:2], {**DE_HOY[2], "km": 350.0}], AYER)
    datos = web.cliente.get("/api/hoy").json()
    assert [z["texto"] for z in datos["zapatillas"]] == [
        "Asics Meta Speed Sky Tokyo 350 de 400 km, toca cambiarla pronto"]


# -- R7 (S-96): Ajustes › Strava -------------------------------------------------------------------------------------

def test_strava_sin_cliente_dice_que_falta(tmp_path):  # R7
    web = Web(tmp_path, configurado=False)
    datos = web.cliente.get("/api/strava").json()
    assert datos["conectado"] is False and datos["configurado"] is False
    assert datos["texto"] == SIN_CONECTAR and datos["falta"] == FALTA_CLIENTE
    assert datos["falta"].startswith("Falta TRIDATA_STRAVA_CLIENTE en .env")
    r = web.cliente.post("/api/strava/conectar")
    assert r.status_code == 409 and r.json()["error"] == FALTA_CLIENTE
    assert web.abiertas == [] and web.doble.llamadas == []


def test_conectar_strava_y_primera_lectura(web, caplog):  # R7 · S-96 · C-363
    caplog.set_level(logging.DEBUG)
    assert web.cliente.get("/api/strava").json()["texto"] == SIN_CONECTAR
    r = web.cliente.post("/api/strava/conectar")
    assert r.status_code == 200
    datos = r.json()
    assert datos["conectado"] is True and datos["texto"].startswith("Conectado · última lectura hoy")
    assert [z["nombre"] for z in datos["zapatillas"]["zapatillas"]] == [
        "Asics Gel Nimbus 28", "Asics Meta Speed Sky Tokyo", "Asics SonicBlast"]
    assert json.loads(web.token.read_text(encoding="utf-8"))["refresh_token"] == REFRESCO_NUEVO
    assert {m for m, u, _, _ in web.doble.llamadas if "/api/v3" in u} == {"GET"}
    texto = r.text + web.cliente.get("/api/strava").text + web.cliente.get("/api/zapatillas").text
    for secreto in (*SECRETOS, "codigo-inventado"):
        assert secreto not in texto and secreto not in caplog.text
    assert str(web.token) not in texto


def test_conectar_que_no_vuelve_es_502(tmp_path):  # R7: Andrés never answered in the browser
    web = Web(tmp_path, conectar=False)
    r = web.cliente.post("/api/strava/conectar")
    assert r.status_code == 502 and "Strava" in r.json()["error"]
    assert not web.token.exists()


def test_strava_conectado_con_el_ultimo_fallo(tmp_path):  # R5 · R7: Ajustes says why the last read failed
    web = Web(tmp_path, conectado=True)
    web.zapatillas.guardar_lectura(DE_HOY, AYER)
    web.zapatillas.apuntar_fallo("el permiso de Strava ya no vale: vuelve a conectar en Ajustes",
                                 datetime(2026, 11, 22, 8, 0))
    datos = web.cliente.get("/api/strava").json()
    assert datos["conectado"] is True
    assert datos["texto"] == "Conectado · última lectura el 21/11 a las 8:00"
    assert datos["error"] == "el permiso de Strava ya no vale: vuelve a conectar en Ajustes"
