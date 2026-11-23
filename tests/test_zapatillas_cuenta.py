"""Unit 103 (REC-47 of `ver-fitness`): the km of the shoes counted here, with the runs that come from Garmin.

R1 (R-292, C-377) the shoes given here and retired; R2 (R-293, C-371..C-374, G-67) the rule and the count; R3 (R-294,
C-375, S-100) the runs without a shoe and the question; R4 (R-295, C-376) the last runs and changing their shoe; R5
the list of the 102 with these km and the km by hand as a new start; R6 the count never fails in silence nor counts
twice. Garmin is never called: the runs are put in the base as `tridata sync` leaves them. Every km is invented.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from tridata.conversaciones.contexto import INSTRUCCIONES
from tridata.conversaciones.desde_pc import (apuntar_zapatilla_desde_pc, montar_equipo, preparar_zapatilla_desde_pc,
                                             texto_contexto)
from tridata.garmin_client import ActividadGarmin
from tridata.models import Activity
from tridata.nutricion import DESDE_SIEMPRE, Ajustes
from tridata.plan import Planes
from tridata.storage import DataStore
from tridata.sync import RUNNING_TYPES
from tridata.zapatillas import (TIPOS_CARRERA, ErrorZapatilla, Zapatillas, aviso_hoy, leer_sin_crear,
                                pendientes_sin_crear, regla_g67)

ALTA = datetime(2026, 11, 23, 10, 0)  # the moment of the start: the shoes are given that morning
HOY = date(2026, 11, 30)
SONIC, NIMBUS, META = "SonicBlast", "Gel Nimbus 28", "Meta Speed Sky Tokyo"
# invented km of the start (not those of any real shoe)
KM_SONIC, KM_NIMBUS, KM_META = 310.0, 150.0, 90.0

ACTIVAS = [{"id": "s", "uso": "calidad"}, {"id": "n", "uso": "suave"}, {"id": "m", "uso": "competicion"}]


# -- G-67, the rule alone (unit) -------------------------------------------------------------------------------------

@pytest.mark.parametrize(("titulo", "esperado"), [
    ("Series 3×2 km a 3:50-4:00", "s"),
    ("Rodaje en grupo", "n"),
    ("Rodaje suave 30'", "n"),
    ("Shake out 25'", "n"),
    ("Tirada 1h25 con 20' a ritmo", "s"),
    ("Ritmo maratón 2×8'", "s"),
    ("2×15' a 4:00-4:15", "s"),
    ("Liebre maratón de prueba", None),
    ("Natación como la del 3/11", None),
    ("3x2 km a ritmo de diez mil", "s"),
    ("RODAJE SUAVE", "n"),
    ("Trote suelto", "n"),
    ("Séries cortas", "s"),
])
def test_regla_g67_con_los_titulos_del_plan(titulo, esperado):  # R2 · G-67
    assert regla_g67([titulo], False, ACTIVAS) == esperado


def test_regla_g67_competicion_gana():  # R2 · G-67 (a)
    assert regla_g67(["Rodaje suave 30'"], True, ACTIVAS) == "m"
    assert regla_g67([], True, ACTIVAS) == "m"


@pytest.mark.parametrize(("titulos", "activas"), [
    ([], ACTIVAS),  # no session that day
    (["Rodaje suave 30'", "Series 3×2 km"], ACTIVAS),  # two uses
    (["Rodaje suave 30'"], [{"id": "s", "uso": "calidad"}, {"id": "m", "uso": "competicion"}]),  # none of that use
    (["Rodaje suave 30'"], [*ACTIVAS, {"id": "n2", "uso": "suave"}]),  # two of that use
    (["Natación como la del 3/11"], ACTIVAS),  # nothing fits
])
def test_regla_g67_sin_zapatilla(titulos, activas):  # R3 · C-375
    assert regla_g67(titulos, False, activas) is None


def test_regla_g67_dos_de_competicion_pregunta():  # R3
    assert regla_g67([], True, [*ACTIVAS, {"id": "m2", "uso": "competicion"}]) is None


def test_los_tipos_de_carrera_son_los_de_garmin():  # R2: running, trail, treadmill, indoor
    assert set(TIPOS_CARRERA) == set(RUNNING_TYPES)


# -- the world of the tests: a base with the shoes, the plan and the runs ---------------------------------------------

class Mundo:
    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.store = DataStore(self.db)
        self.planes = Planes(self.db)
        self.repo = Zapatillas(self.db)
        self.ids: dict[str, str] = {}

    def alta_tres(self, cuando: datetime = ALTA) -> None:
        for nombre, km, tope, uso in ((SONIC, KM_SONIC, 700, "calidad"), (NIMBUS, KM_NIMBUS, 700, "suave"),
                                      (META, KM_META, 400, "competicion")):
            self.ids[nombre] = self.repo.dar_de_alta(nombre, km, tope, uso, cuando)["id"]

    def plan(self, sesiones: dict[str, list[str]], carreras: tuple[tuple[str, str], ...] = ()) -> int:
        return self.planes.crear({
            "nombre": "Plan de prueba", "apartados": [{"titulo": "Sesiones", "parte": "sesiones_clave"}],
            "carreras": [{"apartado": 0, "nombre": nombre, "fecha": fecha, "objetivo": "", "estado": "",
                          "principal": i == 0} for i, (nombre, fecha) in enumerate(carreras or
                                                                                   (("Maratón de prueba",
                                                                                     "2026-12-06"),))],
            "sesiones": [{"apartado": 0, "fecha": fecha, "titulo": t, "bloques": [], "clave": False}
                         for fecha, titulos in sesiones.items() for t in titulos],
        }, datetime(2026, 11, 1, 9, 0))

    def correr(self, ident: str, dia: str, km: float, hora: str = "07:30:00", tipo: str = "running",
               evento: str | None = None) -> None:
        clase = ActividadGarmin if evento else Activity
        extra = {"event_type": evento} if evento else {}
        self.store.save_activities([clase(
            activity_id=ident, activity_date=date.fromisoformat(dia), activity_type=tipo, name="Carrera",
            duration_seconds=3600, distance_meters=km * 1000, start_time_local=f"{dia}T{hora}", **extra)])

    def km(self, nombre: str) -> float:
        return next(z for z in self.repo.todas() if z["id"] == self.ids[nombre])["km"]

    def fila(self, activity_id: str) -> dict:
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM zapatilla_carrera WHERE activity_id = ?", (activity_id,))
                        .fetchone())


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    m = Mundo(tmp_path)
    m.alta_tres()
    return m


# -- R2 (R-293, C-371..C-374): the count --------------------------------------------------------------------------

def test_contar_suma_por_el_plan(mundo):  # R2 · C-371, C-372, C-373
    mundo.plan({"2026-11-24": ["Rodaje en grupo"], "2026-11-25": ["Series 3×2 km a 3:50-4:00"]})
    mundo.correr("c24", "2026-11-24", 10.2)
    mundo.correr("c25", "2026-11-25", 12.4)
    mundo.correr("c06", "2026-12-06", 42.2, hora="08:30:00")
    resultado = mundo.repo.contar()
    assert resultado == {"nuevas": 3, "sin_zapatilla": 0}
    assert (mundo.km(SONIC), mundo.km(NIMBUS), mundo.km(META)) == (322.4, 160.2, 132.2)
    assert mundo.fila("c25")["zapatilla_id"] == mundo.ids[SONIC] and mundo.fila("c25")["origen"] == "regla"
    # counting again the same runs leaves the same km (R6)
    assert mundo.repo.contar() == {"nuevas": 0, "sin_zapatilla": 0}
    assert (mundo.km(SONIC), mundo.km(NIMBUS), mundo.km(META)) == (322.4, 160.2, 132.2)


def test_la_marca_de_competicion_de_garmin(mundo):  # R2 · G-67 (a): marked in Garmin, whatever the plan says
    mundo.plan({"2026-11-29": ["Rodaje suave 30'"]})
    mundo.correr("c29", "2026-11-29", 21.1, evento="race")
    mundo.repo.contar()
    assert mundo.km(META) == 111.1 and mundo.km(NIMBUS) == KM_NIMBUS


def test_trail_y_cinta_tambien_cuentan_y_la_bici_no(mundo):  # R2: only runs
    mundo.plan({"2026-11-24": ["Rodaje suave 30'"], "2026-11-25": ["Rodaje suave 30'"],
                "2026-11-26": ["Rodaje suave 30'"]})
    mundo.correr("t", "2026-11-24", 8.0, tipo="trail_running")
    mundo.correr("c", "2026-11-25", 6.0, tipo="treadmill_running")
    mundo.correr("b", "2026-11-26", 40.0, tipo="cycling")
    mundo.repo.contar()
    assert mundo.km(NIMBUS) == 164.0


def test_carrera_de_antes_del_alta_no_suma(mundo):  # R2 · C-374
    mundo.plan({"2026-11-22": ["Tirada 1h25 con 20' a ritmo"], "2026-11-23": ["Series 3×2 km"]})
    mundo.correr("c22", "2026-11-22", 25.9)
    mundo.correr("c23", "2026-11-23", 9.0, hora="07:00:00")  # the same morning, before the shoes were given
    mundo.repo.contar()
    assert mundo.km(SONIC) == KM_SONIC
    assert mundo.repo.pendientes(HOY) == [] and mundo.repo.ultimas(HOY) == []


def test_una_carrera_se_asigna_una_sola_vez(mundo):  # R2: the rule does not change what it already counted
    plan = mundo.plan({"2026-11-24": ["Rodaje en grupo"]})
    mundo.correr("c24", "2026-11-24", 10.2)
    mundo.repo.contar()
    with sqlite3.connect(mundo.db) as conn:
        conn.execute("UPDATE plan_sesion SET titulo = 'Series 3×2 km' WHERE plan_id = ?", (plan,))
    mundo.repo.contar()
    assert mundo.km(NIMBUS) == 160.2 and mundo.km(SONIC) == KM_SONIC


# -- R3 (R-294, C-375): no shoe, and the question ------------------------------------------------------------------

def test_sin_sesion_queda_sin_zapatilla_y_pregunta(mundo):  # R3 · C-375
    mundo.plan({"2026-11-24": ["Rodaje en grupo"]})
    mundo.correr("c26", "2026-11-26", 8.1)
    assert mundo.repo.contar() == {"nuevas": 1, "sin_zapatilla": 1}
    assert (mundo.km(SONIC), mundo.km(NIMBUS), mundo.km(META)) == (KM_SONIC, KM_NIMBUS, KM_META)
    (pregunta,) = mundo.repo.pendientes(HOY)
    assert pregunta["activity_id"] == "c26" and pregunta["km"] == 8.1 and pregunta["fecha"] == "2026-11-26"
    assert pregunta["texto"] == "¿Con qué zapatilla corriste el 26/11 (8,1 km)?"
    assert [o["nombre"] for o in pregunta["opciones"]] == [NIMBUS, META, SONIC]
    mundo.repo.elegir("c26", mundo.ids[NIMBUS])
    assert mundo.km(NIMBUS) == 158.1
    assert mundo.repo.pendientes(HOY) == []
    assert mundo.fila("c26")["origen"] == "andres"


def test_sin_plan_ninguna_suma(mundo):  # R3 · R6: without the plan nothing is added
    mundo.correr("c26", "2026-11-26", 8.1)
    assert mundo.repo.contar() == {"nuevas": 1, "sin_zapatilla": 1}
    assert mundo.km(NIMBUS) == KM_NIMBUS


def test_dos_usos_el_mismo_dia_pregunta(mundo):  # R3
    mundo.plan({"2026-11-26": ["Rodaje suave 30'", "Series 3×2 km"]})
    mundo.correr("c26", "2026-11-26", 8.1)
    mundo.repo.contar()
    assert [p["activity_id"] for p in mundo.repo.pendientes(HOY)] == ["c26"]


def test_dos_en_activo_del_mismo_uso_pregunta(mundo):  # R3
    mundo.repo.dar_de_alta("Otra de rodaje", 20, 700, "suave", ALTA)
    mundo.plan({"2026-11-26": ["Rodaje suave 30'"]})
    mundo.correr("c26", "2026-11-26", 8.1)
    mundo.repo.contar()
    assert [p["activity_id"] for p in mundo.repo.pendientes(HOY)] == ["c26"]


def test_la_pregunta_de_hoy_dice_hoy(mundo):  # R3
    mundo.correr("c30", "2026-11-30", 5.0)
    mundo.repo.contar()
    assert mundo.repo.pendientes(HOY)[0]["texto"] == "¿Con qué zapatilla corriste hoy (5 km)?"


# -- R4 (R-295, C-376): the last runs and changing their shoe -----------------------------------------------------

def test_cambiar_la_zapatilla_de_una_carrera(mundo):  # R4 · C-376
    mundo.plan({"2026-11-24": ["Rodaje en grupo"]})
    mundo.correr("c24", "2026-11-24", 10.2)
    mundo.repo.contar()
    assert mundo.km(NIMBUS) == 160.2
    mundo.repo.elegir("c24", mundo.ids[SONIC])
    assert (mundo.km(NIMBUS), mundo.km(SONIC)) == (KM_NIMBUS, 320.2)
    assert mundo.fila("c24")["origen"] == "andres"
    mundo.repo.contar()  # the rule never changes it again
    assert (mundo.km(NIMBUS), mundo.km(SONIC)) == (KM_NIMBUS, 320.2)
    (ultima,) = mundo.repo.ultimas(HOY)
    assert ultima["nombre"] == SONIC and ultima["texto_origen"] == "elegida por ti"


def test_ultimas_diez_y_por_el_plan(mundo):  # R4
    mundo.plan({f"2026-11-{d:02d}": ["Rodaje suave 30'"] for d in range(24, 31)}
               | {f"2026-12-{d:02d}": ["Rodaje suave 30'"] for d in range(1, 6)})
    for i in range(12):
        dia = date(2026, 11, 24) + timedelta(days=i)
        mundo.correr(f"c{i:02d}", dia.isoformat(), 5.0)
    mundo.repo.contar()
    ultimas = mundo.repo.ultimas(HOY)
    assert len(ultimas) == 10
    assert ultimas[0]["activity_id"] == "c11" and ultimas[-1]["activity_id"] == "c02"
    assert ultimas[0]["texto_origen"] == "por el plan" and ultimas[0]["nombre"] == NIMBUS
    assert ultimas[0]["texto"] == "5/12 · 5 km"


def test_elegir_lo_que_no_esta(mundo):  # R4
    mundo.correr("c26", "2026-11-26", 8.1)
    mundo.repo.contar()
    with pytest.raises(KeyError):
        mundo.repo.elegir("no-existe", mundo.ids[NIMBUS])
    with pytest.raises(ErrorZapatilla):
        mundo.repo.elegir("c26", "tampoco")
    assert mundo.km(NIMBUS) == KM_NIMBUS


# -- R1 (R-292, C-377): giving and retiring a shoe ----------------------------------------------------------------

def test_alta_y_lo_que_dice(tmp_path):  # R1 · C-377
    repo = Zapatillas(tmp_path / "data.sqlite3")
    z = repo.dar_de_alta(SONIC, "310", 700, "calidad", ALTA)
    assert (z["km"], z["tope"], z["uso"], z["fuente"], z["partida"]) == (310.0, 700, "calidad", "aqui",
                                                                       "2026-11-23T10:00")
    (vista,) = repo.lista(ALTA.date())
    assert vista["texto"] == "SonicBlast · 310 de 700 km · quedan 390 · contados aquí desde hoy"
    (vista,) = repo.lista(HOY)
    assert vista["texto_fuente"] == "contados aquí desde el 23/11" and vista["uso"] == "calidad"


@pytest.mark.parametrize(("nombre", "km", "tope", "uso"), [
    ("", 10, 700, "suave"), ("   ", 10, 700, "suave"), ("X", -1, 700, "suave"), ("X", 10001, 700, "suave"),
    ("X", 10, 20, "suave"), ("X", 10, 3001, "suave"), ("X", 10, 700, "montaña"), ("X", 10, 700, None),
])
def test_alta_que_no_vale(tmp_path, nombre, km, tope, uso):  # R1
    repo = Zapatillas(tmp_path / "data.sqlite3")
    with pytest.raises(ErrorZapatilla) as exc:
        repo.dar_de_alta(nombre, km, tope, uso, ALTA)
    assert str(exc.value)
    assert repo.todas() == []


def test_alta_con_un_nombre_repetido(mundo):  # R1: two in use with the same name could not be told apart
    with pytest.raises(ErrorZapatilla):
        mundo.repo.dar_de_alta("sonicblast", 1, 700, "calidad", ALTA)


def test_retirar(mundo):  # R1
    mundo.plan({"2026-11-24": ["Rodaje en grupo"], "2026-11-26": ["Rodaje suave 30'"]})
    mundo.correr("c24", "2026-11-24", 10.2)
    mundo.repo.contar()
    mundo.repo.retirar(mundo.ids[NIMBUS])
    assert [z["nombre"] for z in mundo.repo.lista(HOY)] == [META, SONIC]
    mundo.correr("c26", "2026-11-26", 7.0)
    mundo.repo.contar()  # a «suave» day without a «suave» shoe in use: it asks, and not with the retired one
    (pregunta,) = mundo.repo.pendientes(HOY)
    assert [o["nombre"] for o in pregunta["opciones"]] == [META, SONIC]
    assert mundo.km(NIMBUS) == 160.2 and mundo.fila("c24")["zapatilla_id"] == mundo.ids[NIMBUS]
    with pytest.raises(ErrorZapatilla):
        mundo.repo.elegir("c26", mundo.ids[NIMBUS])
    with pytest.raises(KeyError):
        mundo.repo.retirar(mundo.ids[NIMBUS])


# -- R5 (R-287, R-289): the list of the 102 with these km, and the km by hand as a new start -------------------------

def test_km_a_mano_es_un_punto_de_partida_nuevo(mundo):  # R5
    mundo.plan({"2026-11-25": ["Series 3×2 km"], "2026-11-28": ["Series 3×2 km"]})
    mundo.correr("c25", "2026-11-25", 12.4)
    mundo.repo.contar()
    assert mundo.km(SONIC) == 322.4
    mundo.repo.poner_km(mundo.ids[SONIC], 330, datetime(2026, 11, 27, 12, 0))
    assert mundo.km(SONIC) == 330.0
    mundo.correr("c28", "2026-11-28", 10.0)
    mundo.repo.contar()
    assert mundo.km(SONIC) == 340.0
    sonic = next(z for z in mundo.repo.lista(HOY) if z["nombre"] == SONIC)
    assert sonic["fuente"] == "aqui" and sonic["texto_fuente"] == "contados aquí desde el 27/11"
    mundo.repo.elegir("c25", mundo.ids[SONIC])  # a run before the new start does not add again
    assert mundo.km(SONIC) == 340.0


def test_el_aviso_de_hoy_con_los_km_contados(mundo):  # R5 · R-289
    mundo.plan({"2026-11-29": ["Rodaje suave 30'"]}, carreras=(("Maratón de prueba", "2026-12-06"),
                                                               ("Media de prueba", "2026-11-28")))
    mundo.correr("c28", "2026-11-28", 270.0)  # a race of the plan that day: the competition shoe
    mundo.repo.contar()
    assert mundo.km(META) == 360.0
    assert [a["texto"] for a in aviso_hoy(mundo.repo.lista(HOY))] == [
        "Meta Speed Sky Tokyo 360 de 400 km, toca cambiarla pronto"]


def test_strava_no_toca_las_de_aqui(mundo):  # R1: the rules of the 102 are not touched
    mundo.repo.guardar_lectura([{"id": "g1", "nombre": "Nike Pegasus 41", "km": 12.0, "retirada": False}], ALTA)
    assert [z["nombre"] for z in mundo.repo.lista(HOY)] == [NIMBUS, META, "Nike Pegasus 41", SONIC]
    assert mundo.km(SONIC) == KM_SONIC


# -- R6: the count never fails in silence nor counts twice --------------------------------------------------------

def test_contar_sin_base_de_garmin(tmp_path):  # R6
    repo = Zapatillas(tmp_path / "data.sqlite3")
    repo.dar_de_alta(SONIC, 10, 700, "calidad", ALTA)
    assert repo.contar() == {"nuevas": 0, "sin_zapatilla": 0}


def test_contar_sin_zapatillas_no_apunta_nada(tmp_path):  # R6
    m = Mundo(tmp_path)
    m.correr("c26", "2026-11-26", 8.1)
    assert m.repo.contar() == {"nuevas": 0, "sin_zapatilla": 0}
    with sqlite3.connect(m.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM zapatilla_carrera").fetchone()[0] == 0


def test_solo_crea_sus_tablas(tmp_path):  # the other tables are not touched
    db = tmp_path / "data.sqlite3"
    Ajustes(db)
    with sqlite3.connect(db) as conn:
        antes = {t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    Zapatillas(db).contar()
    with sqlite3.connect(db) as conn:
        despues = {t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert despues - antes == {"zapatilla", "zapatilla_lectura", "zapatilla_carrera"}


def test_una_base_de_la_102_se_amplia_sin_tocar_sus_filas(tmp_path):  # the columns added, idempotent
    db = tmp_path / "data.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            "CREATE TABLE zapatilla (id TEXT PRIMARY KEY, nombre TEXT NOT NULL, km REAL NOT NULL, fuente TEXT NOT NULL,"
            " fecha_km TEXT NOT NULL, tope INTEGER NOT NULL, en_activo INTEGER NOT NULL DEFAULT 1, intento TEXT, "
            "motivo TEXT);"
            "INSERT INTO zapatilla (id, nombre, km, fuente, fecha_km, tope) VALUES "
            "('g1', 'Nike Pegasus 41', 12.0, 'strava', '2026-11-21T08:00', 700);")
    assert [z["nombre"] for z in leer_sin_crear(db, HOY)] == ["Nike Pegasus 41"]  # read only, before migrating
    Zapatillas(db)
    Zapatillas(db)
    (fila,) = Zapatillas(db).todas()
    assert (fila["km"], fila["fuente"], fila["uso"], fila["partida"]) == (12.0, "strava", None, None)


# -- the routine: `tridata zapatillas` ------------------------------------------------------------------------------

def test_el_comando_cuenta(mundo, capsys):  # R6
    from tridata.cli import _cmd_zapatillas

    mundo.correr("c26", "2026-11-26", 8.1)
    assert _cmd_zapatillas(mundo.db) == 0
    assert "zapatillas OK: 1 carreras nuevas, 1 sin zapatilla" in capsys.readouterr().out
    assert _cmd_zapatillas(mundo.db) == 0
    assert "zapatillas OK: 0 carreras nuevas" in capsys.readouterr().out


def test_el_comando_que_falla_sale_distinto_de_cero(tmp_path, capsys):  # R6: not in silence
    from tridata.cli import _cmd_zapatillas

    carpeta = tmp_path / "no-es-una-base"
    carpeta.mkdir()
    assert _cmd_zapatillas(carpeta) != 0
    assert "zapatillas FAILED" in capsys.readouterr().err


# -- R1, R3 with the coach: preparar shows and writes nothing, apuntar saves with the fingerprint ------------------------

@pytest.fixture
def equipo(tmp_path):
    db = tmp_path / "data.sqlite3"
    Ajustes(db).cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000},
                        desde=DESDE_SIEMPRE)
    eq = montar_equipo(db, store=None, token=tmp_path / "google-token.json", hoy=lambda: HOY,
                       ahora=lambda: datetime(2026, 11, 30, 12, 30))
    return eq, db


def _volcado(db) -> list[str]:
    with sqlite3.connect(db) as conn:
        return list(conn.iterdump())


def _huella(texto: str) -> str:
    return texto.rsplit("Huella: ", 1)[1].strip()


def test_el_coach_da_de_alta_con_su_si(equipo):  # R1 · C-377
    eq, db = equipo
    antes = _volcado(db)
    texto = preparar_zapatilla_desde_pc(eq, SONIC, 310, accion="alta", tope=700, uso="calidad")
    assert _volcado(db) == antes
    assert SONIC in texto and "310" in texto and "700" in texto and "Huella: " in texto and "«sí»" in texto
    assert "No se ha hecho nada" in apuntar_zapatilla_desde_pc(eq, "entrenador", SONIC, 310, "000000000000",
                                                               accion="alta", tope=700, uso="calidad")
    assert _volcado(db) == antes
    hecho = apuntar_zapatilla_desde_pc(eq, "entrenador", SONIC, 310, _huella(texto), accion="alta", tope=700,
                                       uso="calidad")
    assert hecho.startswith("Hecho")
    (vista,) = Zapatillas(db).lista(HOY)
    assert vista["texto"].startswith("SonicBlast · 310 de 700 km · quedan 390")


def test_el_coach_alta_que_no_vale(equipo):  # R1
    eq, db = equipo
    antes = _volcado(db)
    texto = preparar_zapatilla_desde_pc(eq, SONIC, 310, accion="alta", tope=700, uso="montaña")
    assert "No se ha hecho nada" in texto and "Huella" not in texto
    assert _volcado(db) == antes


def test_el_coach_contesta_la_pregunta(equipo):  # R3 · S-100
    eq, db = equipo
    m = Mundo.__new__(Mundo)
    m.db, m.store, m.repo, m.ids = db, DataStore(db), Zapatillas(db), {}
    m.alta_tres()
    m.correr("c26", "2026-11-26", 8.1)
    m.repo.contar()
    datos = json.loads(texto_contexto(eq).split("\n\n")[-1])
    assert datos["carreras_sin_zapatilla"] == [{"carrera": "c26", "fecha": "2026-11-26", "dia": "jueves",
                                                "km": 8.1}]
    assert {z["nombre"]: z["uso"] for z in datos["zapatillas"]} == {SONIC: "calidad", NIMBUS: "suave",
                                                                    META: "competicion"}
    assert "carreras_sin_zapatilla" in INSTRUCCIONES
    antes = _volcado(db)
    texto = preparar_zapatilla_desde_pc(eq, "Nimbus", 8.1, accion="carrera", carrera="2026-11-26")
    assert _volcado(db) == antes
    assert NIMBUS in texto and "26/11" in texto and "8,1" in texto
    hecho = apuntar_zapatilla_desde_pc(eq, "entrenador", "Nimbus", 8.1, _huella(texto), accion="carrera",
                                       carrera="2026-11-26")
    assert hecho.startswith("Hecho")
    assert m.km(NIMBUS) == 158.1
    assert pendientes_sin_crear(db, HOY) == []


def test_el_coach_no_encuentra_la_carrera(equipo):  # R3
    eq, db = equipo
    Zapatillas(db).dar_de_alta(NIMBUS, 150, 700, "suave", ALTA)
    texto = preparar_zapatilla_desde_pc(eq, "Nimbus", 8.1, accion="carrera", carrera="2026-11-27")
    assert "No se ha hecho nada" in texto and "Huella" not in texto


def test_el_coach_accion_que_no_existe(equipo):
    eq, db = equipo
    antes = _volcado(db)
    assert "No se ha hecho nada" in preparar_zapatilla_desde_pc(eq, "Nimbus", 8.1, accion="borrar")
    assert _volcado(db) == antes
