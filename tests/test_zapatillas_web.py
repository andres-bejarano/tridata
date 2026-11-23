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


# -- unit 103 (REC-47): the shoes given here, the count with the runs of Garmin and the question ------------------------

ALTA = datetime(2026, 11, 20, 9, 0)  # before the runs of these tests (`HOY` is 22/11)


def _correr(web: Web, ident: str, dia: str, km: float) -> None:
    from tridata.models import Activity
    from tridata.storage import DataStore

    DataStore(web.db).save_activities([Activity(
        activity_id=ident, activity_date=date.fromisoformat(dia), activity_type="running", name="Carrera",
        duration_seconds=3000, distance_meters=km * 1000, start_time_local=f"{dia}T07:30:00")])


def _plan(web: Web, sesiones: dict[str, str]) -> None:
    from tridata.plan import Planes

    Planes(web.db).crear({"nombre": "Plan de prueba", "apartados": [{"titulo": "Sesiones", "parte": "sesiones_clave"}],
                          "carreras": [{"apartado": 0, "nombre": "Maratón de prueba", "fecha": "2026-12-06",
                                        "objetivo": "", "estado": "", "principal": True}],
                          "sesiones": [{"apartado": 0, "fecha": f, "titulo": t, "bloques": [], "clave": False}
                                       for f, t in sesiones.items()]}, datetime(2026, 11, 1, 9, 0))


def _tres(web: Web) -> dict[str, str]:
    ids = {}
    for nombre, km, tope, uso in (("SonicBlast", 310, 700, "calidad"), ("Gel Nimbus 28", 150, 700, "suave"),
                                  ("Meta Speed Sky Tokyo", 90, 400, "competicion")):
        ids[nombre] = web.zapatillas.dar_de_alta(nombre, km, tope, uso, ALTA)["id"]
    return ids


def test_alta_en_la_web(web):  # R1 (103) · C-377
    r = web.cliente.post("/api/zapatillas", json={"nombre": "SonicBlast", "km": "310", "tope": 700, "uso": "calidad"})
    assert r.status_code == 200
    (sonic,) = r.json()["zapatillas"]
    assert sonic["texto"] == "SonicBlast · 310 de 700 km · quedan 390 · contados aquí desde hoy"
    assert sonic["uso"] == "calidad" and sonic["fuente"] == "aqui"


@pytest.mark.parametrize("cuerpo", [{"nombre": "X", "km": 10, "tope": 700, "uso": "montaña"},
                                    {"nombre": "", "km": 10, "tope": 700, "uso": "suave"},
                                    {"nombre": "X", "km": -1, "tope": 700, "uso": "suave"},
                                    {"nombre": "X", "km": 10, "tope": 5, "uso": "suave"},
                                    {"nombre": "X", "km": 10, "tope": 700}, []])
def test_alta_que_no_vale_en_la_web(web, cuerpo):  # R1 (103)
    r = web.cliente.post("/api/zapatillas", json=cuerpo)
    assert r.status_code == 400 and r.json()["error"]
    assert web.zapatillas.todas() == []


def test_retirar_en_la_web(web):  # R1 (103)
    ids = _tres(web)
    r = web.cliente.post(f"/api/zapatillas/{ids['Gel Nimbus 28']}", json={"retirar": True})
    assert r.status_code == 200
    assert [z["nombre"] for z in r.json()["zapatillas"]] == ["Meta Speed Sky Tokyo", "SonicBlast"]
    assert web.cliente.post(f"/api/zapatillas/{ids['Gel Nimbus 28']}", json={"retirar": True}).status_code == 404


def test_la_web_cuenta_y_trae_pendientes_y_ultimas(web):  # R2, R3, R4 (103) · C-371, C-375
    ids = _tres(web)
    _plan(web, {"2026-11-21": "Series 3×2 km a 3:50-4:00"})
    _correr(web, "c21", "2026-11-21", 12.4)
    _correr(web, "c22", "2026-11-22", 8.1)
    datos = web.cliente.get("/api/zapatillas").json()
    sonic = next(z for z in datos["zapatillas"] if z["nombre"] == "SonicBlast")
    assert sonic["km"] == 322.4
    (pregunta,) = datos["pendientes"]
    assert pregunta["texto"] == "¿Con qué zapatilla corriste hoy (8,1 km)?" and pregunta["activity_id"] == "c22"
    assert [o["nombre"] for o in pregunta["opciones"]] == ["Gel Nimbus 28", "Meta Speed Sky Tokyo", "SonicBlast"]
    assert [(u["activity_id"], u["nombre"], u["texto_origen"]) for u in datos["ultimas"]] == [
        ("c22", None, None), ("c21", "SonicBlast", "por el plan")]
    # Hoy: unit 106 (R6, S-104): the runs of today and yesterday are a line with «Cambiar» / «Elegir», not a question
    hoy = web.cliente.get("/api/hoy").json()
    assert [z["texto"] for z in hoy["zapatillas"] if z.get("pregunta")] == []
    assert [z["texto"] for z in hoy["zapatillas"] if z.get("carrera")] == ["21/11: 12,4 km con SonicBlast",
                                                                          "22/11: 8,1 km · Sin zapatilla"]
    # answering it adds the km and the question goes
    r = web.cliente.post("/api/zapatillas/carrera/c22", json={"zapatilla": ids["Gel Nimbus 28"]})
    assert r.status_code == 200
    datos = r.json()
    assert datos["pendientes"] == []
    assert next(z for z in datos["zapatillas"] if z["nombre"] == "Gel Nimbus 28")["km"] == 158.1
    assert datos["ultimas"][0]["texto_origen"] == "elegida por ti"
    assert [z["texto"] for z in web.cliente.get("/api/hoy").json()["zapatillas"]] == [
        "21/11: 12,4 km con SonicBlast", "22/11: 8,1 km con Gel Nimbus 28"]
    # and changing the one of a run moves its km (C-376)
    r = web.cliente.post("/api/zapatillas/carrera/c21", json={"zapatilla": ids["Gel Nimbus 28"]})
    km = {z["nombre"]: z["km"] for z in r.json()["zapatillas"]}
    assert (km["SonicBlast"], km["Gel Nimbus 28"]) == (310.0, 170.5)


def test_elegir_lo_que_no_vale_en_la_web(web):  # R3 (103)
    ids = _tres(web)
    _correr(web, "c22", "2026-11-22", 8.1)
    web.cliente.get("/api/zapatillas")
    assert web.cliente.post("/api/zapatillas/carrera/c22", json={"zapatilla": "otra"}).status_code == 400
    assert web.cliente.post("/api/zapatillas/carrera/c22", json={}).status_code == 400
    assert web.cliente.post("/api/zapatillas/carrera/no-existe",
                            json={"zapatilla": ids["SonicBlast"]}).status_code == 404
    assert len(web.cliente.get("/api/zapatillas").json()["pendientes"]) == 1


# -- unit 106 (REC-50): the use trail, the line of a run, retire and put back in use -----------------------------------

def test_alta_de_trail_en_la_web(web):  # R1 · R-303, C-385
    r = web.cliente.post("/api/zapatillas", json={"nombre": "Asics Trabuco 13", "km": "200", "tope": 700,
                                                  "uso": "trail"})
    assert r.status_code == 200
    (trabuco,) = r.json()["zapatillas"]
    assert trabuco["texto"].startswith("Asics Trabuco 13 · 200 de 700 km · quedan 500") and trabuco["uso"] == "trail"


def test_la_linea_de_una_carrera_en_la_web(web):  # R7 · R-309, C-392
    ids = _tres(web)
    _plan(web, {"2026-11-21": "Series 3×2 km a 3:50-4:00"})
    _correr(web, "c21", "2026-11-21", 12.4)
    _correr(web, "c22", "2026-11-22", 8.1)
    r = web.cliente.get("/api/zapatillas/carrera/c21")
    assert r.status_code == 200
    linea = r.json()
    assert (linea["texto"], linea["boton"], linea["zapatilla_id"]) == ("21/11: 12,4 km con SonicBlast", "Cambiar",
                                                                      ids["SonicBlast"])
    assert [o["nombre"] for o in linea["opciones"]] == ["Gel Nimbus 28", "Meta Speed Sky Tokyo", "SonicBlast"]
    sin = web.cliente.get("/api/zapatillas/carrera/c22").json()
    assert (sin["texto"], sin["boton"]) == ("22/11: 8,1 km · Sin zapatilla", "Elegir")
    web.cliente.post("/api/zapatillas/carrera/c22", json={"zapatilla": ids["Gel Nimbus 28"]})
    assert web.cliente.get("/api/zapatillas/carrera/c22").json()["texto"] == "22/11: 8,1 km con Gel Nimbus 28"
    no = web.cliente.get("/api/zapatillas/carrera/no-es-una-carrera")
    assert no.status_code == 404 and no.json()["error"]


def test_retirar_y_volver_a_activo_en_la_web(web):  # R3 · R-305, C-388
    ids = _tres(web)
    nimbus = ids["Gel Nimbus 28"]
    assert web.cliente.get("/api/zapatillas").json()["retiradas"] == []
    r = web.cliente.post(f"/api/zapatillas/{nimbus}", json={"retirar": True})
    (retirada,) = r.json()["retiradas"]
    assert (retirada["id"], retirada["nombre"], retirada["km"]) == (nimbus, "Gel Nimbus 28", 150.0)
    r = web.cliente.post(f"/api/zapatillas/{nimbus}", json={"activar": True})
    assert r.status_code == 200
    datos = r.json()
    assert datos["retiradas"] == []
    assert next(z for z in datos["zapatillas"] if z["id"] == nimbus)["km"] == 150.0
    assert web.cliente.post(f"/api/zapatillas/{nimbus}", json={"activar": True}).status_code == 404
    assert web.cliente.post(f"/api/zapatillas/{nimbus}", json={"activar": "sí"}).status_code == 400
