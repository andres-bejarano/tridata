"""Unit 102 (REC-46 of `ver-fitness`): the shoes and their km (`zapatillas.py`), the coach and its context.

R3 (R-288, C-365, S-98), R4 (R-289, C-366, C-367, S-97), R6 (R-291, C-369, C-370, G-66) and what the coach sees and
does with Andrés's «sí». Every name, id and km is invented (the names are the models of the plans).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

import pytest

from tridata.conversaciones.contexto import INSTRUCCIONES
from tridata.conversaciones.desde_pc import (apuntar_zapatilla_desde_pc, montar_equipo, preparar_zapatilla_desde_pc,
                                             texto_contexto)
from tridata.nutricion import DESDE_SIEMPRE, Ajustes
from tridata.zapatillas import (ErrorZapatilla, Zapatillas, aviso_hoy, estado, leer_sin_crear, tope_sembrado)

HOY = date(2026, 11, 22)
AYER = datetime(2026, 11, 21, 8, 0)
ESTA_MANANA = datetime(2026, 11, 22, 8, 0)
DE_HOY = [
    {"id": "g1001", "nombre": "Asics SonicBlast", "km": 303.2, "retirada": False},
    {"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 141.0, "retirada": False},
    {"id": "g1003", "nombre": "Asics Meta Speed Sky Tokyo", "km": 103.4, "retirada": False},
]


@pytest.fixture
def repo(tmp_path) -> Zapatillas:
    return Zapatillas(tmp_path / "data.sqlite3")


def _una(repo: Zapatillas, ident: str) -> dict:
    return next(z for z in repo.todas() if z["id"] == ident)


# -- R3 (R-288, C-365, S-98): the caps ------------------------------------------------------------------------------

def test_topes_sembrados(repo):  # R3 · R-288 · S-98
    repo.guardar_lectura([*DE_HOY, {"id": "g1009", "nombre": "Nike Pegasus 41", "km": 12.0, "retirada": False}],
                         ESTA_MANANA)
    assert [_una(repo, i)["tope"] for i in ("g1001", "g1002", "g1003", "g1009")] == [700, 700, 400, 700]
    assert tope_sembrado("asics sonicblast 2") == 700 and tope_sembrado("Meta Speed Sky Tokyo") == 400


def test_el_tope_sembrado_no_pisa_el_de_andres(repo):  # R3: a new read never changes a cap he set
    repo.guardar_lectura(DE_HOY, AYER)
    repo.cambiar_tope("g1003", 350)
    repo.guardar_lectura(DE_HOY, ESTA_MANANA)
    assert _una(repo, "g1003")["tope"] == 350


def test_cambiar_el_tope(repo):  # R3 · C-365
    repo.guardar_lectura(DE_HOY, ESTA_MANANA)
    repo.cambiar_tope("g1003", 350)
    meta = next(z for z in repo.lista(HOY) if z["id"] == "g1003")
    assert meta["tope"] == 350 and meta["quedan"] == 247
    assert "quedan 247" in meta["texto"]


@pytest.mark.parametrize("malo", [20, 49, 3001, "abc", 350.5, True, None, -400])
def test_tope_que_no_vale(repo, malo):  # R3: a whole number from 50 to 3000
    repo.guardar_lectura(DE_HOY, ESTA_MANANA)
    with pytest.raises(ErrorZapatilla) as exc:
        repo.cambiar_tope("g1003", malo)
    assert "50" in str(exc.value) and "3000" in str(exc.value)
    assert _una(repo, "g1003")["tope"] == 400


@pytest.mark.parametrize("bueno", [50, 3000, "350"])
def test_tope_en_los_bordes(repo, bueno):  # R3
    repo.guardar_lectura(DE_HOY, ESTA_MANANA)
    repo.cambiar_tope("g1003", bueno)
    assert _una(repo, "g1003")["tope"] == int(bueno)


def test_tope_de_una_que_no_esta(repo):  # R3
    with pytest.raises(KeyError):
        repo.cambiar_tope("g-no-existe", 400)


# -- R4 (R-289, C-366, C-367, S-97): the mark and the warning of Hoy -----------------------------------------------

@pytest.mark.parametrize(("km", "esperado"), [(349, "bien"), (349.9, "bien"), (350, "pronto"), (399.9, "pronto"),
                                               (400, "pasada"), (431, "pasada")])
def test_estado_en_el_borde_de_50_km(km, esperado):  # R4 · S-97
    assert estado(km, 400) == esperado


def _con_km(repo, km: float, tope: int = 400) -> None:
    repo.guardar_lectura([{"id": "g1003", "nombre": "Asics Meta Speed Sky Tokyo", "km": km, "retirada": False},
                          {"id": "g1001", "nombre": "Asics SonicBlast", "km": 303.2, "retirada": False}], ESTA_MANANA)
    repo.cambiar_tope("g1003", tope)


def test_349_de_400_no_avisa(repo):  # R4 · C-366, C-367
    _con_km(repo, 349)
    lista = repo.lista(HOY)
    assert aviso_hoy(lista) == []
    assert all(z["estado"] == "bien" and z["texto_estado"] is None for z in lista)


def test_350_de_400_toca_cambiarla_pronto(repo):  # R4 · C-366
    _con_km(repo, 350)
    meta = next(z for z in repo.lista(HOY) if z["id"] == "g1003")
    assert meta["estado"] == "pronto" and meta["texto_estado"] == "toca cambiarla pronto"
    assert aviso_hoy(repo.lista(HOY)) == [
        {"id": "g1003", "nombre": "Asics Meta Speed Sky Tokyo", "km": 350.0, "tope": 400, "estado": "pronto",
         "texto": "Asics Meta Speed Sky Tokyo 350 de 400 km, toca cambiarla pronto"}]


def test_400_de_400_pasada(repo):  # R4 · S-97
    _con_km(repo, 400)
    (aviso,) = aviso_hoy(repo.lista(HOY))
    assert aviso["estado"] == "pasada"
    assert aviso["texto"] == "Asics Meta Speed Sky Tokyo 400 de 400 km, pasada"
    meta = next(z for z in repo.lista(HOY) if z["id"] == "g1003")
    assert meta["texto_estado"] == "pasada"


def test_el_aviso_usa_el_tope_nuevo(repo):  # R3 + R4 · C-365: the warning uses the new cap
    _con_km(repo, 103.4)
    assert aviso_hoy(repo.lista(HOY)) == []
    repo.cambiar_tope("g1003", 120)
    (aviso,) = aviso_hoy(repo.lista(HOY))
    assert aviso["estado"] == "pronto" and "103 de 120 km" in aviso["texto"]
    repo.cambiar_tope("g1003", 100)
    assert aviso_hoy(repo.lista(HOY))[0]["estado"] == "pasada"
    repo.cambiar_tope("g1003", 400)
    assert aviso_hoy(repo.lista(HOY)) == []


def test_sin_zapatillas_sin_aviso(repo):  # R4 · C-367
    assert repo.lista(HOY) == [] and aviso_hoy([]) == []


# -- R2 (R-287, C-364): what each row says ---------------------------------------------------------------------------

def test_lo_que_dice_cada_una(repo):  # R2 · C-364
    repo.guardar_lectura(DE_HOY, AYER)
    sonic = next(z for z in repo.lista(HOY) if z["id"] == "g1001")
    assert sonic["km"] == 303.2 and sonic["tope"] == 700 and sonic["quedan"] == 397
    assert sonic["fuente"] == "strava" and sonic["fecha_km"] == "2026-11-21"
    assert sonic["texto_fuente"] == "de Strava, el 21/11"
    assert sonic["texto"] == "Asics SonicBlast · 303 de 700 km · quedan 397 · de Strava, el 21/11"
    repo.guardar_lectura(DE_HOY, ESTA_MANANA)
    assert next(z for z in repo.lista(HOY) if z["id"] == "g1001")["texto_fuente"] == "de Strava, hoy"


def test_la_que_se_ha_pasado_dice_cuanto(repo):  # R2: what is left, or what has been gone over
    _con_km(repo, 431)
    meta = next(z for z in repo.lista(HOY) if z["id"] == "g1003")
    assert meta["quedan"] == -31 and "te has pasado 31" in meta["texto"]


def test_la_lista_va_en_orden_y_sin_las_retiradas(repo):  # R1 + R2
    repo.guardar_lectura([*DE_HOY, {"id": "g1004", "nombre": "Nike Vomero 18", "km": 812.0, "retirada": True}],
                         ESTA_MANANA)
    assert [z["nombre"] for z in repo.lista(HOY)] == ["Asics Gel Nimbus 28", "Asics Meta Speed Sky Tokyo",
                                                       "Asics SonicBlast"]


# -- R6 (R-291, C-369, C-370): the km by hand and the rule «newer and not lower» --------------------------------------

def test_km_a_mano_y_la_regla_de_fusion(repo):  # R6 · C-369, C-370
    repo.guardar_lectura(DE_HOY, AYER)
    a_mano = datetime(2026, 11, 22, 12, 30)
    repo.poner_km("g1002", 160, a_mano)
    nimbus = next(z for z in repo.lista(HOY) if z["id"] == "g1002")
    assert (nimbus["km"], nimbus["fuente"], nimbus["texto_fuente"]) == (160.0, "andres", "dicho por ti, hoy")
    # Strava comes back with 150 (km of before he said 160): it stays 160 «dicho por ti»
    repo.guardar_lectura([{"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 150.0, "retirada": False}],
                         datetime(2026, 11, 23, 8, 0))
    nimbus = _una(repo, "g1002")
    assert (nimbus["km"], nimbus["fuente"], nimbus["fecha_km"]) == (160.0, "andres", "2026-11-22T12:30")
    # then Strava gives 165: it is 165 «de Strava»
    repo.guardar_lectura([{"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 165.0, "retirada": False}],
                         datetime(2026, 11, 24, 8, 0))
    nimbus = next(z for z in repo.lista(date(2026, 11, 24)) if z["id"] == "g1002")
    assert (nimbus["km"], nimbus["fuente"], nimbus["texto_fuente"]) == (165.0, "strava", "de Strava, hoy")


def test_strava_con_los_mismos_km_vuelve_a_ser_la_fuente(repo):  # R6: «iguales o mayores»
    repo.guardar_lectura(DE_HOY, AYER)
    repo.poner_km("g1002", 160, datetime(2026, 11, 22, 12, 30))
    repo.guardar_lectura([{"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 160.0, "retirada": False}],
                         datetime(2026, 11, 23, 8, 0))
    assert _una(repo, "g1002")["fuente"] == "strava"


def test_una_lectura_anterior_no_pisa_lo_dicho(repo):  # R6: only a LATER read replaces it
    repo.guardar_lectura(DE_HOY, AYER)
    repo.poner_km("g1002", 160, datetime(2026, 11, 22, 12, 30))
    repo.guardar_lectura([{"id": "g1002", "nombre": "Asics Gel Nimbus 28", "km": 170.0, "retirada": False}],
                         datetime(2026, 11, 22, 12, 0))
    assert _una(repo, "g1002")["fuente"] == "andres"


@pytest.mark.parametrize("malo", [-1, "abc", True, None, 20000, float("nan")])
def test_km_que_no_valen(repo, malo):  # R6
    repo.guardar_lectura(DE_HOY, AYER)
    with pytest.raises(ErrorZapatilla):
        repo.poner_km("g1002", malo, ESTA_MANANA)
    assert _una(repo, "g1002")["km"] == 141.0


def test_km_a_mano_con_coma(repo):  # R6: «160,5» as Andrés writes it
    repo.guardar_lectura(DE_HOY, AYER)
    repo.poner_km("g1002", "160,5", ESTA_MANANA)
    assert _una(repo, "g1002")["km"] == 160.5


def test_hoy_no_se_pudo_leer(repo):  # R5 · C-368: the date of the last km and why not today
    repo.guardar_lectura(DE_HOY, AYER)
    repo.apuntar_fallo("Strava no responde", ESTA_MANANA)
    vista = repo.vista(HOY)
    nimbus = next(z for z in vista["zapatillas"] if z["id"] == "g1002")
    assert nimbus["texto"] == "Asics Gel Nimbus 28 · 141 de 700 km · quedan 559 · de Strava, el 21/11 · hoy no se pudo leer"
    assert vista["aviso_lectura"] == "Hoy no se pudo leer Strava: Strava no responde."
    repo.guardar_lectura(DE_HOY, datetime(2026, 11, 22, 9, 0))
    vista = repo.vista(HOY)
    assert vista["aviso_lectura"] is None and "no se pudo leer" not in vista["zapatillas"][0]["texto"]


def test_solo_crea_su_tabla(tmp_path):  # the other tables are not touched
    db = tmp_path / "data.sqlite3"
    Ajustes(db).cambiar({"basal_kcal": 1600}, desde=DESDE_SIEMPRE)
    with sqlite3.connect(db) as conn:
        antes = {t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    Zapatillas(db)
    with sqlite3.connect(db) as conn:
        despues = {t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert despues - antes == {"zapatilla", "zapatilla_lectura"} and antes <= despues


def test_leer_sin_crear_no_crea_nada(tmp_path):  # the context reads without creating (trap of the 082)
    db = tmp_path / "data.sqlite3"
    Ajustes(db)
    assert leer_sin_crear(db, HOY) == []
    with sqlite3.connect(db) as conn:
        tablas = {t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "zapatilla" not in tablas
    Zapatillas(db).guardar_lectura(DE_HOY, AYER)
    assert [z["nombre"] for z in leer_sin_crear(db, HOY)] == ["Asics Gel Nimbus 28", "Asics Meta Speed Sky Tokyo",
                                                              "Asics SonicBlast"]


# -- R6 with the coach: preparar shows and writes nothing, apuntar saves with the fingerprint -------------------------

@pytest.fixture
def equipo(tmp_path):
    db = tmp_path / "data.sqlite3"
    Ajustes(db).cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                        desde=DESDE_SIEMPRE)
    eq = montar_equipo(db, store=None, token=tmp_path / "google-token.json", hoy=lambda: HOY,
                       ahora=lambda: datetime(2026, 11, 22, 12, 30))
    Zapatillas(db).guardar_lectura(DE_HOY, AYER)
    return eq, db


def _volcado(db) -> list[str]:
    with sqlite3.connect(db) as conn:
        return list(conn.iterdump())


def _huella(texto: str) -> str:
    return texto.rsplit("Huella: ", 1)[1].strip()


def test_preparar_zapatilla_no_escribe(equipo):  # R6 · R-291
    eq, db = equipo
    antes = _volcado(db)
    texto = preparar_zapatilla_desde_pc(eq, "nimbus", 160)
    assert _volcado(db) == antes
    assert "Asics Gel Nimbus 28" in texto and "141" in texto and "160" in texto and "dicho por ti" in texto
    assert "«sí»" in texto and "apuntar_zapatilla" in texto and "Huella: " in texto


def test_apuntar_zapatilla_con_su_huella(equipo):  # R6 · C-369
    eq, db = equipo
    huella = _huella(preparar_zapatilla_desde_pc(eq, "Nimbus", 160))
    hecho = apuntar_zapatilla_desde_pc(eq, "entrenador", "Nimbus", 160, huella)
    assert hecho.startswith("Hecho")
    nimbus = next(z for z in Zapatillas(db).lista(HOY) if z["id"] == "g1002")
    assert (nimbus["km"], nimbus["fuente"], nimbus["texto_fuente"]) == (160.0, "andres", "dicho por ti, hoy")
    mensajes = [m for c in eq.conversaciones.lista() if c["titulo"] == "Desde el PC"
                for m in eq.conversaciones.mensajes(c["id"])]
    assert any("Nimbus" in m["texto"] and m.get("papel") == "entrenador" for m in mensajes)


def test_apuntar_zapatilla_sin_huella_buena_no_hace_nada(equipo):  # R6: without his «sí» nothing is written
    eq, db = equipo
    antes = _volcado(db)
    texto = apuntar_zapatilla_desde_pc(eq, "entrenador", "Nimbus", 160, "000000000000")
    assert not texto.startswith("Hecho") and "No se ha hecho nada" in texto
    assert _volcado(db) == antes


def test_apuntar_zapatilla_con_otro_papel_no_hace_nada(equipo):  # R6
    eq, db = equipo
    huella = _huella(preparar_zapatilla_desde_pc(eq, "Nimbus", 160))
    antes = _volcado(db)
    assert "No se ha hecho nada" in apuntar_zapatilla_desde_pc(eq, "fontanero", "Nimbus", 160, huella)
    assert _volcado(db) == antes


@pytest.mark.parametrize(("que", "esperado"), [("Pegasus", "Asics SonicBlast"), ("asics", "Hay más de una"),
                                               ("Nimbus", "Asics Gel Nimbus 28")])
def test_preparar_zapatilla_busca_por_nombre(equipo, que, esperado):  # R6: a name not there lists those in use
    eq, _ = equipo
    assert esperado in preparar_zapatilla_desde_pc(eq, que, 160)


def test_preparar_zapatilla_con_km_que_no_valen(equipo):  # R6
    eq, db = equipo
    antes = _volcado(db)
    texto = preparar_zapatilla_desde_pc(eq, "Nimbus", -3)
    assert "No se ha hecho nada" in texto and "Huella" not in texto
    assert _volcado(db) == antes


def test_el_contexto_lleva_las_zapatillas(equipo):  # R-291: the coach sees km, cap and state
    eq, db = equipo
    Zapatillas(db).cambiar_tope("g1003", 120)
    datos = json.loads(texto_contexto(eq).split("\n\n")[-1])
    zapatillas = {z["nombre"]: z for z in datos["zapatillas"]}
    assert set(zapatillas) == {"Asics SonicBlast", "Asics Gel Nimbus 28", "Asics Meta Speed Sky Tokyo"}
    meta = zapatillas["Asics Meta Speed Sky Tokyo"]
    assert (meta["km"], meta["tope"], meta["quedan"], meta["estado"]) == (103.4, 120, 17, "pronto")
    assert meta["fuente"] == "strava" and meta["fecha_km"] == "2026-11-21" and meta["dia_km"] == "sábado"
    assert '"zapatillas"' in INSTRUCCIONES and "preparar_zapatilla" in INSTRUCCIONES


def test_sin_zapatillas_el_contexto_no_cambia(tmp_path):  # nothing new for who has none
    db = tmp_path / "data.sqlite3"
    Ajustes(db).cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                        desde=DESDE_SIEMPRE)
    eq = montar_equipo(db, store=None, token=tmp_path / "google-token.json", hoy=lambda: HOY)
    assert "zapatillas" not in json.loads(texto_contexto(eq).split("\n\n")[-1])
