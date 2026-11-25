"""Unit 111 (REC-54 of `pedir-al-asistente`, R-334..R-339, G-74, S-113, S-114, C-426..C-431): the levels or the %
of the objective of several days at once, and where the objective of each day comes from.

With `hasta` the same actions `niveles_dia` and `objetivo_dia` prepare ONE card for the whole range: the days that
change with what they had and where their objective came from, and apart the ones that do not (they already have it,
or they are maintenance for a race). One «sí» saves them all, or none. `eleccion` only takes a whole number.

Integration through the functions of the coach (`preparar_desde_pc` / `hacer_desde_pc`) over a temporary SQLite with
the day fixed; nothing talks to Garmin, Google or a paid service. Every datum is invented.

Today is Wednesday 2026-11-25. Ajustes: plan −20 %, weight target 72 kg for 2027-03-01. The race «Behobia» on
2026-12-14 with no days before (only that day is maintenance).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from tridata.conversaciones.contexto import montar
from tridata.conversaciones.desde_pc import hacer_desde_pc, montar_equipo, preparar_desde_pc
from tridata.nutricion import DESDE_SIEMPRE, Alimento
from tridata.nutricion.margen import origen_del_objetivo
from tridata.nutricion.objetivo_dia import ObjetivosDia

HOY = date(2026, 11, 25)
AHORA = datetime(2026, 11, 25, 9, 0)
D1, D15 = date(2026, 12, 1), date(2026, 12, 15)
RANGO = [D1 + timedelta(days=i) for i in range(15)]
HUELLA = re.compile(r"Huella: ([0-9a-f]{12})")


class Mundo:
    def __init__(self, tmp_path, *, carrera: bool = False) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.equipo = montar_equipo(self.db, store=None, token=tmp_path / "google-token.json", hoy=lambda: HOY,
                                    ahora=lambda: AHORA)
        a = self.equipo.asistente
        self.ajustes, self.objetivos, self.registro = a.ajustes, a.objetivos, a.registro
        self.ajustes.cambiar({"basal_kcal": 1900, "kcal_por_paso": 0.04, "pasos_por_defecto": 10000,
                              "objetivo_pct": -20, "limite_saludable_pct": 25, "altura_cm": 175,
                              "objetivo_peso": {"kg": 72, "fecha": "2027-03-01"}}, desde=DESDE_SIEMPRE)
        if carrera:
            self.ajustes.anadir_carrera("Behobia", "2026-12-14", 0)

    def preparar(self, cambio: dict) -> str:
        return preparar_desde_pc(self.equipo, cambio, "texto de prueba")

    def hacer(self, cambio: dict) -> str:
        texto = self.preparar(cambio)
        encontrada = HUELLA.search(texto)
        assert encontrada, texto
        return hacer_desde_pc(self.equipo, "nutricionista", cambio, encontrada.group(1), "texto de prueba")

    def volcado(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return [x for x in conn.iterdump() if "tarjeta_asistente" not in x and "conversacion" not in x
                    and "mensaje" not in x]
        finally:
            conn.close()

    def niveles(self, dia: date) -> list | None:
        return (self.objetivos.del_dia(dia) or {}).get("niveles")

    def pct(self, dia: date):
        return (self.objetivos.del_dia(dia) or {}).get("porcentaje")


@pytest.fixture
def mundo(tmp_path) -> Mundo:
    return Mundo(tmp_path)


NIVELES = {"accion": "niveles_dia", "valores": [-30, -20, -10], "dia": "2026-12-01", "hasta": "2026-12-15"}
OBJETIVO = {"accion": "objetivo_dia", "valor": -10, "dia": "2026-12-01", "hasta": "2026-12-15"}


# -- R1 · R-334, C-426 (portante): one card, one «sí», 15 days ---------------------------------------------------

def test_rango_de_niveles_una_tarjeta_un_si_quince_dias(mundo):  # R1
    for d in RANGO:
        mundo.objetivos.cambiar(d, niveles=[-20, -10, 0])
    texto = mundo.preparar(NIVELES)
    assert len(HUELLA.findall(texto)) == 1, texto  # ONE card
    assert "del 01-12 al 15-12 (15 días): −20 / −10 / 0 % → −30 / −20 / −10 %" in texto, texto
    assert "de Ajustes" in texto  # R3: where the objective of those days comes from
    assert all(mundo.niveles(d) == [-20, -10, 0] for d in RANGO)  # preparing writes nothing
    hecho = mundo.hacer(NIVELES)
    assert hecho.startswith("Hecho"), hecho
    for d in RANGO:
        assert mundo.niveles(d) == [-30, -20, -10], d
        assert [m["nivel"] for m in mundo.equipo.asistente.resumen(d)["margenes"]] == [-30, -20, -10], d
    assert mundo.niveles(D1 - timedelta(days=1)) is None and mundo.niveles(D15 + timedelta(days=1)) is None


def test_rango_de_niveles_si_un_dia_falla_no_se_guarda_ninguno(mundo, monkeypatch):  # R1
    texto = mundo.preparar(NIVELES)
    huella = HUELLA.search(texto).group(1)
    antes = mundo.volcado()
    original = ObjetivosDia._escribir
    escritos: list[str] = []

    def falla_el_octavo(self, conn, dia, valores, ahora):
        escritos.append(dia.isoformat())
        if len(escritos) == 8:
            raise sqlite3.OperationalError("disco de prueba lleno")
        return original(self, conn, dia, valores, ahora)

    monkeypatch.setattr(ObjetivosDia, "_escribir", falla_el_octavo)
    with pytest.raises(sqlite3.OperationalError):
        hacer_desde_pc(mundo.equipo, "nutricionista", NIVELES, huella, "texto de prueba")
    assert len(escritos) == 8
    assert mundo.volcado() == antes
    assert all(mundo.niveles(d) is None for d in RANGO)


def test_cambiar_varios_es_todo_o_nada_y_valida_como_cambiar(mundo):  # R1 (the repository)
    with pytest.raises(ValueError):
        mundo.objetivos.cambiar_varios(RANGO, niveles=[1, 2, 3, 4, 5, 6])
    assert all(mundo.niveles(d) is None for d in RANGO)
    guardados = mundo.objetivos.cambiar_varios(RANGO, porcentaje=-10)
    assert [g["dia"] for g in guardados] == [d.isoformat() for d in RANGO]
    assert all(mundo.pct(d) == -10 for d in RANGO)


# -- R2 · R-335, C-427: the % of a range ------------------------------------------------------------------------

def test_rango_de_objetivo_a_mano_ajustes_intacto_y_aviso_de_retraso(mundo):  # R2
    texto = mundo.preparar(OBJETIVO)
    assert "del 01-12 al 15-12 (15 días): −20 % → −10 %" in texto, texto
    assert "Aviso: llegas a tu objetivo de peso" in texto and "más tarde" in texto, texto
    hecho = mundo.hacer(OBJETIVO)
    assert hecho.startswith("Hecho"), hecho
    for d in RANGO:
        assert mundo.pct(d) == -10, d
        ajustes = mundo.ajustes.vigentes(d)
        assert ajustes.objetivo_pct == -20  # Ajustes does not change
        assert origen_del_objetivo(mundo.objetivos.del_dia(d), {"objetivo_pct": -20, "motivo": "ajuste"}) \
            == "cambiado a mano"


def test_el_retraso_del_rango_es_el_de_sus_dias(mundo):  # R2: the same computation as one day, for the range
    uno = mundo.preparar({"accion": "objetivo_dia", "valor": -10, "dia": "2026-12-01"})
    dias_uno = float(re.search(r"llegas a tu objetivo de peso ([\d,]+) día", uno).group(1).replace(",", "."))
    dos = mundo.preparar({**OBJETIVO, "hasta": "2026-12-02"})
    dias_dos = float(re.search(r"llegas a tu objetivo de peso ([\d,]+) días?", dos).group(1).replace(",", "."))
    assert dias_uno > 0 and dias_dos == pytest.approx(2 * dias_uno, abs=0.2)


# -- R3 · R-336, C-428: where the objective of each day comes from ----------------------------------------------

def test_origen_del_objetivo_tres_casos():  # R3 (unit)
    assert origen_del_objetivo({"porcentaje": -15}, {"objetivo_pct": -20, "motivo": "ajuste"}) == "cambiado a mano"
    assert origen_del_objetivo(None, {"objetivo_pct": 0, "motivo": "carrera Behobia"}) \
        == "mantenimiento por la carrera Behobia"
    assert origen_del_objetivo({"niveles": [-10]}, {"objetivo_pct": -20, "motivo": "ajuste"}) == "de Ajustes"
    assert origen_del_objetivo(None, None) == "sin plan en Ajustes"


def test_origen_en_la_tarjeta_del_rango_y_en_el_contexto(tmp_path):  # R3 · C-428
    mundo = Mundo(tmp_path, carrera=True)
    mundo.objetivos.cambiar(date(2026, 12, 3), porcentaje=-15)
    texto = mundo.preparar({**OBJETIVO, "valor": -5})
    assert "−15 % (cambiado a mano): el 03-12" in texto, texto
    assert "−20 % (de Ajustes)" in texto, texto
    assert "el 14-12 es mantenimiento por la carrera Behobia" in texto, texto
    # the context: from today to 60 days, the days whose objective does not come from Ajustes
    import json
    a = mundo.equipo.asistente
    ctx = montar(registro=a.registro, pesos=a.pesos, ajustes=a.ajustes, store=None, transcripciones=[],
                 aviso_medico=None, hoy=HOY)
    datos = json.loads(ctx.bloques[-1])
    otros = {x["fecha"]: x for x in datos["objetivos_no_de_ajustes"]}
    assert otros["2026-12-14"]["origen"] == "mantenimiento por la carrera Behobia" and otros["2026-12-14"]["pct"] == 0
    assert otros["2026-12-03"]["origen"] == "cambiado a mano" and otros["2026-12-03"]["pct"] == -15
    assert set(otros) == {"2026-12-03", "2026-12-14"}
    hoy = next(x for x in datos["dias"] if x["fecha"] == HOY.isoformat())  # each day of the detail
    assert hoy["objetivo"] == {"pct": -20, "origen": "de Ajustes"}
    assert '"objetivos_no_de_ajustes"' in ctx.bloques[0]  # the instructions say what it is


def test_origen_en_la_tarjeta_de_un_dia(tmp_path):  # R3 · R7: one day, plus its origin
    mundo = Mundo(tmp_path, carrera=True)
    texto = mundo.preparar({"accion": "niveles_dia", "valores": [-5, 0], "dia": "2026-12-14"})
    assert "mantenimiento por la carrera Behobia" in texto, texto
    texto = mundo.preparar({"accion": "objetivo_dia", "valor": -10, "dia": "2026-12-02"})
    assert "Antes: −20 % (de Ajustes)" in texto, texto


# -- R4 · R-337, G-74, S-113, C-429: what does not change -------------------------------------------------------

def test_los_que_ya_lo_tienen_y_los_de_carrera_no_cambian(tmp_path):  # R4 · C-429
    mundo = Mundo(tmp_path, carrera=True)
    mundo.objetivos.cambiar(date(2026, 12, 7), porcentaje=-10)
    texto = mundo.preparar(OBJETIVO)
    assert "(13 de 15 días)" in texto, texto
    assert "el 07-12 ya lo tiene" in texto and "el 14-12 es mantenimiento por la carrera Behobia" in texto, texto
    hecho = mundo.hacer(OBJETIVO)
    assert hecho.startswith("Hecho"), hecho
    assert mundo.pct(date(2026, 12, 14)) is None  # still maintenance by the race
    assert mundo.ajustes.vigentes(date(2026, 12, 14)).objetivo_pct == 0
    assert all(mundo.pct(d) == -10 for d in RANGO if d != date(2026, 12, 14))  # the 7th had it already


def test_niveles_tampoco_pisan_la_carrera(tmp_path):  # R4 · G-74
    mundo = Mundo(tmp_path, carrera=True)
    assert mundo.hacer(NIVELES).startswith("Hecho")
    assert mundo.niveles(date(2026, 12, 14)) is None
    assert all(mundo.niveles(d) == [-30, -20, -10] for d in RANGO if d != date(2026, 12, 14))


def test_si_no_cambia_ninguno_no_prepara_nada_y_dice_por_que(tmp_path):  # R4
    mundo = Mundo(tmp_path, carrera=True)
    cambio = {**OBJETIVO, "dia": "2026-12-13", "hasta": "2026-12-14"}
    mundo.objetivos.cambiar(date(2026, 12, 13), porcentaje=-10)
    antes = mundo.volcado()
    texto = mundo.preparar(cambio)
    assert texto.startswith("No se puede") and not HUELLA.search(texto), texto
    assert "13-12 ya lo tiene" in texto and "14-12 es mantenimiento por la carrera Behobia" in texto, texto
    assert mundo.volcado() == antes


# -- R5 · R-338, S-114, C-430: ranges that are not ------------------------------------------------------------------

@pytest.mark.parametrize("desde, hasta, motivo", [
    ("2026-11-20", "2026-11-30", "no puede empezar antes de hoy"),
    ("2026-12-01", "2027-03-15", "pasa de 60 días"),
    ("2026-12-15", "2026-12-01", "acaba antes de empezar"),
])
@pytest.mark.parametrize("accion", [NIVELES, OBJETIVO])
def test_rangos_que_no_valen(mundo, accion, desde, hasta, motivo):  # R5 · C-430
    antes = mundo.volcado()
    texto = mundo.preparar({**accion, "dia": desde, "hasta": hasta})
    assert texto.startswith("No se puede") and motivo in texto and not HUELLA.search(texto), texto
    assert mundo.volcado() == antes


def test_sesenta_dias_justos_si(mundo):  # R5: the limit itself is allowed
    texto = mundo.preparar({**OBJETIVO, "dia": "2026-12-01", "hasta": "2027-01-29"})
    assert "(60 días)" in texto and HUELLA.search(texto), texto


def test_hasta_sin_inicio_toma_hoy(mundo):  # R5
    texto = mundo.preparar({"accion": "objetivo_dia", "valor": -10, "hasta": "2026-11-27"})
    assert "del 25-11 al 27-11 (3 días)" in texto, texto


def test_desde_vale_como_inicio(mundo):  # «con dia (o desde) y hasta»
    texto = mundo.preparar({"accion": "objetivo_dia", "valor": -10, "desde": "2026-12-01", "hasta": "2026-12-03"})
    assert "del 01-12 al 03-12 (3 días)" in texto, texto


# -- R6 · R-339, C-431: `eleccion` is a whole number ------------------------------------------------------------

class Platanos(Mundo):
    def __init__(self, tmp_path) -> None:
        super().__init__(tmp_path)

        def guardar(cantidad: float, seccion: str) -> int:
            (fila,) = self.registro.guardar([Alimento(nombre="plátano", dia=HOY, cantidad=cantidad, unidad="g",
                                                      kcal=cantidad)], hoy=HOY, texto="plátano", sin_cambios=True,
                                            seccion=seccion)
            return int(fila["id"])

        self.uno, self.dos = guardar(80, "merienda"), guardar(120, "cena")


@pytest.mark.parametrize("raro", [True, "1", 1.5, "2", 1.0])
@pytest.mark.parametrize("accion", ["quitar_alimento", "seccion_alimento"])
def test_eleccion_rara_no_prepara_y_dice_que_es_un_entero(tmp_path, raro, accion):  # R6 · C-431
    mundo = Platanos(tmp_path)
    cambio = {"accion": accion, "objeto": "plátano", "texto": "desayuno", "eleccion": raro}
    antes = mundo.volcado()
    texto = mundo.preparar(cambio)
    assert not HUELLA.search(texto), texto
    assert "eleccion: tiene que ser un número entero" in texto, texto
    assert mundo.volcado() == antes


@pytest.mark.parametrize("accion", ["quitar_alimento", "seccion_alimento"])
def test_eleccion_entera_sigue_eligiendo(tmp_path, accion):  # R6: as today, also in seccion_alimento
    mundo = Platanos(tmp_path)
    cambio = {"accion": accion, "objeto": "plátano", "texto": "desayuno", "eleccion": mundo.dos}
    hecho = mundo.hacer(cambio)
    assert hecho.startswith("Hecho"), hecho
    uno = mundo.registro.alimento(mundo.uno)
    assert uno["seccion"] == "merienda"
    if accion == "seccion_alimento":
        assert mundo.registro.alimento(mundo.dos)["seccion"] == "desayuno"
    else:
        with pytest.raises(KeyError):
            mundo.registro.alimento(mundo.dos)


def test_seccion_sin_eleccion_con_dos_pregunta_cual(tmp_path):  # R6: seccion_alimento had no test of its own
    mundo = Platanos(tmp_path)
    texto = mundo.preparar({"accion": "seccion_alimento", "objeto": "plátano", "texto": "desayuno"})
    assert "Hay más de uno" in texto and f"número {mundo.uno}" in texto and f"número {mundo.dos}" in texto, texto


# -- R7: one day, as today ------------------------------------------------------------------------------------------

def test_sin_hasta_un_dia_como_hoy(mundo):  # R7
    texto = mundo.preparar({"accion": "niveles_dia", "valores": [-30, -20, -10], "dia": "2026-12-01"})
    assert "«Voy a cambiar los niveles del 01-12: −10 / 0 / +10 % → −30 / −20 / −10 % (déficit)»" in texto, texto
    assert "Dónde: solo ese día, en sus márgenes de Hoy y Nutrición › Día" in texto, texto
    hecho = mundo.hacer({"accion": "niveles_dia", "valores": [-30, -20, -10], "dia": "2026-12-01"})
    assert hecho == ("Hecho: los niveles del 01-12 son −30 / −20 / −10 %. Era: «Voy a cambiar los niveles del 01-12: "
                     "−10 / 0 / +10 % → −30 / −20 / −10 % (déficit)».")
    assert mundo.niveles(date(2026, 12, 2)) is None


def test_hasta_igual_al_dia_es_un_dia(mundo):  # R7: a range of one day is the card of one day
    uno = mundo.preparar({"accion": "objetivo_dia", "valor": -10, "dia": "2026-12-01"})
    mismo = mundo.preparar({"accion": "objetivo_dia", "valor": -10, "dia": "2026-12-01", "hasta": "2026-12-01"})
    assert uno == mismo


def test_las_acciones_explican_hasta():  # the catalogue the coach reads
    from tridata.nutricion.asistente import texto_acciones
    for linea in texto_acciones().splitlines():
        if linea.startswith(("- niveles_dia:", "- objetivo_dia:")):
            assert "hasta" in linea and "dos meses" in linea, linea  # G-38: no figure in the catalogue
