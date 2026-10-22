"""Bug 020: the past days imported from deficit.md as one «total del día (importado)» line get their foods back.

`tridata importar-detalle --md RUTA [--simular]` re-reads the «Registro diario» and, ONLY on the days that
today have the single imported total line, puts one line per food plus, if needed, an «ajuste hasta el total
del día» so the day adds up exactly the same as before. Contract: docs/bugs/020-importar-detalle-dias.md
(R0-R9). Everything is INVENTED (tests/fixtures/deficit_detalle_inventado.md). «Today» is 2026-03-12.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from tridata import cli
from tridata.nutricion import (
    DESDE_SIEMPRE, Ajustes, Alimento, Cierres, ObjetivosDia, RegistroComidas, RegistroPeso, total_del_dia,
)
from tridata.nutricion import historial_md
from tridata.nutricion import importar_deficit as imp
from tridata.nutricion.balance import semana as semana_balance
from tridata.nutricion.dia import resumen_completo
from tridata.storage import DataStore

HOY = date(2026, 3, 12)
FIXTURE = Path(__file__).parent / "fixtures" / "deficit_detalle_inventado.md"
MD = FIXTURE.read_text(encoding="utf-8")
R = "ración"
AJ = "ajuste hasta el total del día"
RESUMEN_PRIMERA = ("Detalle: 5 días cambiados (25 alimentos, 4 ajustes) · 2 sin detalle siguen igual · no se tocan: "
                   "1 con algo de la app, 1 que ya tenía sus alimentos, 1 cuyo total no coincide")
TABLAS_QUE_NO_CAMBIAN = ("cierre_dia", "cierre_rastro", "calibracion", "peso_dia", "importacion", "interpretacion",
                         "ajuste")


def d(n: int) -> date:
    return date(2026, 3, n)


def _celda(n: int) -> str:
    """The «Kcal consumidas» cell of the fixture row of day `n`."""
    for linea in MD.splitlines():
        if linea.startswith(f"| {d(n).isoformat()} "):
            return linea.strip().strip("|").split("|")[3].strip()
    raise AssertionError(n)


# -- R2: what each day must end up with (name, quantity, unit, kcal) -------------------------------------

ESPERADO = {
    2: [("lentejas de prueba", 200, "g", 600), ("cebolla de prueba", 100, "g", 40), ("½ plato de prueba", 1, R, 320),
        ("½ plato de prueba", 1, R, 320), ("fruta de prueba", 1, R, 100), (AJ, 1, R, -480)],
    6: [("yogur de prueba", 125, "g", 50), ("avena de prueba", 40, "g", 150), ("banana de prueba", 90, "g", 80),
        ("guiso de prueba", 300, "g", 420), ("pan de prueba", 60, "g", 150), ("fruta de prueba", 1, R, 150)],
    7: [("avena de prueba + banana de prueba + miel de prueba", 1, R, 250), ("guiso de la tía", 450, "g", 585),
        ("yogur de prueba 125g/cereales de prueba 18g", 1, R, 120), ("tostada de prueba", 30, "g", 80),
        ("crema de prueba", 10, "g", 55), ("pizza de prueba 7/8 sin borde", 1, R, 600), (AJ, 1, R, 110)],
    8: [("avena de prueba", 30, "g", 110), ("banana de prueba", 100, "g", 90), ("leche de prueba", 300, "ml", 105),
        ("lentejas de prueba", 300, "g", 360), ("pan de prueba", 40, "g", 100), ("tortilla de prueba", 150, "g", 250),
        ("ensalada de prueba", 1, R, 60), (AJ, 1, R, 125)],
    9: [("cena de prueba", 1, R, 400), (AJ, 1, R, 1700)],
}
SIN_TOCAR = {
    3: [(imp.TOTAL_IMPORTADO, 1, "día", 2000)],
    4: [(imp.TOTAL_IMPORTADO, 1, "día", 2150)],
    5: [(imp.TOTAL_IMPORTADO, 1, "día", 2000)],
    10: [("tostada de prueba", 30, "g", 80)],
    11: [("avena de prueba", 60, "g", 230), ("banana de prueba", 120, "g", 105), ("guiso de prueba", 350, "g", 420)],
}
TOTALES_BASE = {2: 900, 3: 2000, 4: 2150, 5: 2000, 6: 1000, 7: 1800, 8: 1200, 9: 2100}
EXTRAS_BASE = {5: ["incl. +40kcal café de prueba no contado"],
               8: [f"revisado al importar: {imp.SIN_TOTAL}"], 9: [f"revisado al importar: {imp.RANGO}"]}


class BaseDetalle:
    """R1: the base «as unit 009 left it», built directly (without `_cmd_importar`)."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.ajustes = Ajustes(self.db)
        self.objetivos = ObjetivosDia(self.db)
        self.store = DataStore(self.db)
        self.ajustes.cambiar({"basal_kcal": 1800, "pasos_por_defecto": 10000, "kcal_por_paso": 0.04,
                              "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        self.pesos.apuntar(d(1), 80.9, "Inicio", hoy=HOY)
        for n, kcal in TOTALES_BASE.items():
            linea = Alimento(nombre=imp.TOTAL_IMPORTADO, dia=d(n), cantidad=1, unidad="día", kcal=kcal, estimado=True,
                             supuestos=[imp.SUPUESTO, *EXTRAS_BASE.get(n, [])])
            assert self.registro.importar_dia(d(n), [linea], HOY) == 1
        self.registro.guardar([Alimento(nombre="tostada de prueba", dia=d(10), cantidad=30, unidad="g", kcal=80)],
                              hoy=d(10), texto="tostada de prueba", sin_cambios=True)
        self.registro.importar_dia(d(11), [
            Alimento(nombre=nombre, dia=d(11), cantidad=g, unidad="g", kcal=k, estimado=True, supuestos=[imp.SUPUESTO])
            for nombre, g, k in (("avena de prueba", 60, 230), ("banana de prueba", 120, 105),
                                 ("guiso de prueba", 350, 420))], HOY)
        imp.Importaciones(self.db).registrar("md:inventada html:-", {
            "resumen": {}, "referencias": [], "taras": [], "dudosos": [
                {"apartado": imp.REGISTRO, "texto": "en curso (…)", "motivo": imp.SIN_TOTAL, "propuesta": None,
                 "dia": "2026-03-08", "decision": imp.CORREGIDO, "valor": 1200},
                {"apartado": imp.REGISTRO, "texto": "~2.000–2.200 (…)", "motivo": imp.RANGO, "propuesta": 2100,
                 "dia": "2026-03-09", "decision": imp.ACEPTADO, "valor": 2100},
            ]})
        self.cierres = Cierres(self.db, ajustes=self.ajustes, pesos=self.pesos, objetivos=self.objetivos,
                               store=self.store, registro=self.registro)
        with sqlite3.connect(self.db) as conn:
            for n, gasto in ((6, 2700), (7, 3500)):
                conn.execute(
                    "INSERT INTO cierre_dia (dia, estado, comida_cerrada_en, motivo_cierre, gasto_estimado, desglose, "
                    "gasto_real, cerrado_garmin_en, espera_motivo) VALUES (?, 'cerrado_garmin', ?, 'dia_siguiente', ?, "
                    "NULL, ?, ?, NULL)",
                    (d(n).isoformat(), f"{d(n + 1).isoformat()}T08:00:00", gasto - 100, gasto,
                     f"{d(n + 1).isoformat()}T09:00:00"))
            conn.execute(
                "INSERT INTO cierre_rastro (dia, fecha, gasto_antes, gasto_despues, balance_antes, balance_despues, "
                "relevante, visto) VALUES ('2026-03-07', '2026-03-08T09:00:00', 3400, 3500, -1600, -1700, 0, 0)")

    def lineas(self, n: int) -> list[tuple]:
        return [(a["nombre"], a["cantidad"], a["unidad"], a["kcal"]) for a in self.registro.apuntes_del_dia(d(n))]

    def apuntes(self, n: int) -> list[dict]:
        return self.registro.apuntes_del_dia(d(n))

    def suma(self, n: int) -> float:
        return total_del_dia(self.registro.apuntes_del_dia(d(n)), d(n))

    def resumen(self, dia: date) -> dict:
        return resumen_completo(dia, registro=self.registro, pesos=self.pesos, ajustes=self.ajustes,
                                objetivos=self.objetivos, store=self.store, cierres=self.cierres, hoy=HOY)


def _volcar(db: Path, tablas: tuple[str, ...] | None = None) -> dict | None:
    """The rows of every table (or of `tablas`), to prove nothing changed (None if the file does not exist)."""
    if not db.exists():
        return None
    with sqlite3.connect(db) as conn:
        todas = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: sorted(map(repr, conn.execute(f'SELECT * FROM "{t}"').fetchall()))  # noqa: S608
                for t in todas if tablas is None or t in tablas}


def _interpretaciones(db: Path, n: int) -> list:
    with sqlite3.connect(db) as conn:
        return [r[0] for r in conn.execute("SELECT interpretacion_id FROM alimento_apuntado WHERE dia = ? ORDER BY id",
                                           (d(n).isoformat(),))]


def _respuestas(*valores):
    """An injected `input`: answers in order; when they run out, end of input (EOFError)."""
    cola = list(valores)
    preguntas: list[str] = []

    def preguntar(texto=""):
        preguntas.append(texto)
        if not cola:
            raise EOFError
        valor = cola.pop(0)
        if isinstance(valor, BaseException):
            raise valor
        return valor

    preguntar.preguntas = preguntas  # type: ignore[attr-defined]
    return preguntar


@pytest.fixture
def base(tmp_path):
    return BaseDetalle(tmp_path)


@pytest.fixture
def md(tmp_path):
    ruta = tmp_path / "deficit.md"
    ruta.write_text(MD, encoding="utf-8")
    return ruta


def _lanzar(base, md, tmp_path, *respuestas, simular=False):
    return cli._cmd_importar_detalle(md, simular, db_path=base.db, backup_dir=tmp_path / "backups",
                                     preguntar=_respuestas(*respuestas))


def _esperado(lineas):
    return [(n, float(c), u, float(k)) for n, c, u, k in lineas]


# -- section 2: the end-to-end reproduction ---------------------------------------------------------------

def test_importar_detalle_rellena_los_dias_con_alimentos(base, md, tmp_path, monkeypatch, capsys):  # R2
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", base.db)
    monkeypatch.setattr(cli, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr("builtins.input", _respuestas("s"))

    assert cli.main(["importar-detalle", "--md", str(md)]) == 0

    for n, lineas in ESPERADO.items():
        assert base.lineas(n) == _esperado(lineas), n
        assert base.suma(n) == TOTALES_BASE[n], n
    for n, lineas in SIN_TOCAR.items():
        assert base.lineas(n) == _esperado(lineas), n


def test_las_lineas_nuevas_son_importadas_y_sus_supuestos(base, md, tmp_path):  # R2
    assert _lanzar(base, md, tmp_path, "s") == 0
    for n in ESPERADO:
        assert _interpretaciones(base.db, n) == [None] * len(ESPERADO[n]), n
        for a in base.apuntes(n):
            assert a["estado"] == "comido" and a["estimado"] is True, (n, a["nombre"])
            assert imp.SUPUESTO in a["supuestos"], (n, a["nombre"])
            for prohibido in ("~", "[", "(", "**", ":", "desayuno", "comida:", "cena:"):
                assert prohibido not in a["nombre"], (n, a["nombre"])
    siete = base.apuntes(7)
    assert "avena de prueba 40g + banana de prueba 90g + miel de prueba 9g" in siete[0]["supuestos"]
    assert "130kcal/100g" in siete[1]["supuestos"]
    assert "60g de borde no comido, ~150 kcal restadas" in siete[5]["supuestos"]
    assert "día de prueba con superávit" in siete[-1]["supuestos"]
    assert f"revisado al importar: {imp.SIN_TOTAL}" in base.apuntes(8)[-1]["supuestos"]
    nueve = base.apuntes(9)[-1]["supuestos"]
    assert "sin kcal en el registro: con amigos de prueba · sandía de prueba 600g · 1 cda yogur de prueba" in nueve
    assert f"revisado al importar: {imp.RANGO}" in nueve


# -- R3: reading one cell (pure functions) -------------------------------------------------------------

@pytest.mark.parametrize("n", sorted(ESPERADO))
def test_lineas_del_detalle(n):  # R3
    lineas = imp.lineas_del_detalle(_celda(n), d(n), TOTALES_BASE[n], [imp.SUPUESTO, *EXTRAS_BASE.get(n, [])])
    assert lineas is not None
    assert [(a.nombre, a.cantidad, a.unidad, a.kcal) for a in lineas] == _esperado(ESPERADO[n])
    assert all(a.dia == d(n) and a.estimado for a in lineas)
    assert sum(a.kcal or 0 for a in lineas) == TOTALES_BASE[n]


@pytest.mark.parametrize("n", [4, 5])
def test_sin_detalle_no_hay_lineas(n):  # R3.1: no list, or one piece only (a comment)
    assert imp.lineas_del_detalle(_celda(n), d(n), TOTALES_BASE[n], [imp.SUPUESTO]) is None


@pytest.mark.parametrize("celda", [
    "~2.000", "~2.000 (con amigos de prueba + cena larga de prueba)", "en curso", "sin apuntar",
])
def test_lista_sin_ninguna_kcal_no_es_detalle(celda):  # R3.1
    assert imp.lineas_del_detalle(celda, d(4), 2000, [imp.SUPUESTO]) is None


def test_sin_ajuste_el_comentario_va_a_la_primera_linea():  # R3.5
    lineas = imp.lineas_del_detalle("~300 (pan de prueba 40g ~100 + guiso de prueba ~200) — día de prueba", d(4), 300,
                                    [imp.SUPUESTO, "revisado al importar: de prueba"])
    assert [(a.nombre, a.kcal) for a in lineas] == [("pan de prueba", 100), ("guiso de prueba", 200)]
    assert lineas[0].supuestos == [imp.SUPUESTO, "revisado al importar: de prueba", "día de prueba"]
    assert lineas[1].supuestos == [imp.SUPUESTO]


@pytest.mark.parametrize("celda, nombres", [
    ("~600 (pan/aceite de prueba ~180 + guiso de prueba ~420)", ["pan/aceite de prueba", "guiso de prueba"]),
    ("~600 (tarta de prueba 7/8 ~180 + guiso de prueba ~420)", ["tarta de prueba 7/8", "guiso de prueba"]),
    ("~600 (tostada de prueba ~180/guiso de prueba ~420)", ["tostada de prueba", "guiso de prueba"]),
    ("~600 (**1ª comida (14:30):** tostada de prueba ~180; plato de prueba (comida/cena): guiso de prueba 400kcal "
     "+ 20)", ["tostada de prueba", "guiso de prueba"]),
])
def test_partir_en_trozos(celda, nombres):  # R3.2, R3.3: «/» only after kcal; labels out; a bare number is not kcal
    lineas = imp.lineas_del_detalle(celda, d(4), 600, [imp.SUPUESTO])
    assert [a.nombre for a in lineas if a.nombre != AJ] == nombres


def test_total_del_fichero():  # R4: the number at the start, or the «TOTAL ~N»
    assert imp.total_del_fichero(_celda(3)) == 1900
    assert imp.total_del_fichero(_celda(8)) == 1075
    assert imp.total_del_fichero(_celda(11)) == 755
    assert imp.total_del_fichero("con amigos de prueba") is None


# -- R4: which days are touched -------------------------------------------------------------------------

def test_solo_se_tocan_los_dias_con_la_linea_del_total(base, md, tmp_path, capsys):  # R4
    _lanzar(base, md, tmp_path, simular=True)
    salida = capsys.readouterr().out
    assert "2026-03-03 · no se toca: el total del fichero (1.900) no es el de la base (2.000)" in salida
    assert "2026-03-04 · sigue igual: sin detalle" in salida
    assert "2026-03-05 · sigue igual: sin detalle" in salida
    assert "2026-03-10 · no se toca: algo apuntado en la app" in salida
    assert "2026-03-11 · no se toca: ya tiene sus alimentos" in salida
    for n in ESPERADO:
        assert f"{d(n).isoformat()} · cambia" in salida, n


def test_un_dudoso_decidido_manda_sobre_el_fichero(base, md, tmp_path):  # R4: 08 uses 1.200, not 1.075
    with sqlite3.connect(base.db) as conn:
        conn.execute("DELETE FROM importacion")
    _lanzar(base, md, tmp_path, "s")
    assert base.lineas(8) == [(imp.TOTAL_IMPORTADO, 1.0, "día", 1200.0)]  # without the decision: totals differ
    assert base.lineas(9) == [(imp.TOTAL_IMPORTADO, 1.0, "día", 2100.0)]  # 2.000 at the start ≠ 2.100
    assert base.lineas(6) == _esperado(ESPERADO[6])


def test_no_se_anade_ningun_dia_que_no_este_en_la_base(base, md, tmp_path):  # R4
    with sqlite3.connect(base.db) as conn:
        conn.execute("DELETE FROM alimento_apuntado WHERE dia = '2026-03-06'")
    _lanzar(base, md, tmp_path, "s")
    assert base.lineas(6) == []


def test_sustituir_comprueba_otra_vez_dentro_de_la_transaccion(base):  # R4, R5
    nuevas = [Alimento(nombre="pan de prueba", dia=d(10), cantidad=1, unidad=R, kcal=80, estimado=True,
                       supuestos=[imp.SUPUESTO])]
    antes = _volcar(base.db)
    assert base.registro.sustituir_total_importado(d(10), nuevas, 80, imp.TOTAL_IMPORTADO) is False  # app food
    assert base.registro.sustituir_total_importado(d(11), nuevas, 755, imp.TOTAL_IMPORTADO) is False  # its foods
    otro_total = [Alimento(nombre="pan de prueba", dia=d(4), cantidad=1, unidad=R, kcal=2000, estimado=True)]
    assert base.registro.sustituir_total_importado(d(4), otro_total, 2000, imp.TOTAL_IMPORTADO) is False  # ≠ 2.150
    assert _volcar(base.db) == antes


def test_si_las_lineas_no_suman_lo_mismo_el_dia_queda_como_estaba(base):  # R5
    antes = _volcar(base.db)
    mal = [Alimento(nombre="pan de prueba", dia=d(4), cantidad=1, unidad=R, kcal=2000, estimado=True)]
    with pytest.raises(ValueError):
        base.registro.sustituir_total_importado(d(4), mal, 2150, imp.TOTAL_IMPORTADO)
    assert _volcar(base.db) == antes


# -- R5: the balance does not move -------------------------------------------------------------------------

def test_el_balance_no_se_mueve(base, md, tmp_path):  # R5
    sumas = {n: base.suma(n) for n in range(1, 13)}
    tablas = _volcar(base.db, TABLAS_QUE_NO_CAMBIAN)
    semana_014 = base.cierres.semana(d(7))
    semana_017 = semana_balance(d(7), resumen=base.resumen)
    resumenes = {n: (base.resumen(d(n))["total_kcal"], base.resumen(d(n))["cierre"]) for n in range(2, 12)}

    assert _lanzar(base, md, tmp_path, "s") == 0

    assert {n: base.suma(n) for n in range(1, 13)} == sumas
    assert _volcar(base.db, TABLAS_QUE_NO_CAMBIAN) == tablas
    assert set(tablas) == set(TABLAS_QUE_NO_CAMBIAN)
    assert base.cierres.del_dia(d(6))["estado"] == base.cierres.del_dia(d(7))["estado"] == "cerrado_garmin"
    assert base.cierres.semana(d(7)) == semana_014
    assert semana_balance(d(7), resumen=base.resumen) == semana_017
    assert {n: (base.resumen(d(n))["total_kcal"], base.resumen(d(n))["cierre"]) for n in range(2, 12)} == resumenes


# -- R6: simulate, confirm and the copy -------------------------------------------------------------------------

def test_simular_lista_y_no_guarda_nada(base, md, tmp_path, capsys):  # R6
    antes = _volcar(base.db)
    assert _lanzar(base, md, tmp_path, simular=True) == 0
    salida = capsys.readouterr().out
    assert RESUMEN_PRIMERA in salida
    assert salida.rstrip().endswith("Simulación: no se ha guardado nada.")
    assert "ajuste hasta el total del día · 1 ración · −480 kcal" in salida
    assert "lentejas de prueba · 200 g · 600 kcal" in salida
    assert "leche de prueba · 300 ml · 105 kcal" in salida
    assert "ajuste hasta el total del día · 1 ración · +1.700 kcal" in salida
    assert _volcar(base.db) == antes
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("respuestas", [("n",), ("",), (KeyboardInterrupt(),), ()])
def test_sin_confirmar_no_se_guarda_nada(base, md, tmp_path, capsys, respuestas):  # R6
    antes = _volcar(base.db)
    _lanzar(base, md, tmp_path, *respuestas)
    salida = capsys.readouterr().out
    assert "No se ha guardado nada." in salida
    assert _volcar(base.db) == antes
    assert not (tmp_path / "backups").exists()


def test_copia_antes_de_escribir(base, md, tmp_path, capsys):  # R6
    antes = _volcar(base.db)
    preguntar = _respuestas("s")
    assert cli._cmd_importar_detalle(md, False, db_path=base.db, backup_dir=tmp_path / "backups",
                                     preguntar=preguntar) == 0
    assert preguntar.preguntas == ["¿Guardar? [s/N] "]
    [copia] = list((tmp_path / "backups").iterdir())
    assert (copia / "deficit.md").read_text(encoding="utf-8") == MD
    assert _volcar(copia / "data.sqlite3") == antes
    salida = capsys.readouterr().out
    assert salida.count(RESUMEN_PRIMERA) == 2  # the listing before asking and the summary once written


@pytest.mark.parametrize("falla", ["excepcion", "codigo"])
def test_si_la_copia_falla_no_se_escribe_nada(base, md, tmp_path, monkeypatch, capsys, falla):  # R6
    def copia_rota(*args, **kwargs):
        if falla == "excepcion":
            raise OSError("disco de prueba lleno")
        return 1

    monkeypatch.setattr(cli, "_cmd_backup", copia_rota)
    antes = _volcar(base.db)
    assert _lanzar(base, md, tmp_path, "s") == 1
    assert "no se ha guardado nada" in capsys.readouterr().err
    assert _volcar(base.db) == antes


def test_sin_base_dice_que_falta_y_no_la_crea(md, tmp_path, capsys):  # R6
    db = tmp_path / "no-existe" / "data.sqlite3"
    assert cli._cmd_importar_detalle(md, True, db_path=db, backup_dir=tmp_path / "backups",
                                     preguntar=_respuestas()) == 1
    assert "base" in capsys.readouterr().err
    assert not db.exists()


def test_sin_registro_diario_dice_que_falta(base, tmp_path, capsys):  # R6
    sin = tmp_path / "sin.md"
    sin.write_text(MD.replace("## Registro diario", "## Otra cosa"), encoding="utf-8")
    antes = _volcar(base.db)
    assert _lanzar(base, sin, tmp_path, "s") == 1
    assert "Registro diario" in capsys.readouterr().err
    assert _volcar(base.db) == antes


def test_md_que_no_existe(base, tmp_path, capsys):  # R6
    assert _lanzar(base, tmp_path / "no.md", tmp_path, "s") == 1
    assert "no.md" in capsys.readouterr().err


# -- R7: idempotent ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("simular", [False, True])
def test_otra_vez_no_cambia_nada(base, md, tmp_path, capsys, simular):  # R7
    assert _lanzar(base, md, tmp_path, "s") == 0
    capsys.readouterr()
    reparada = _volcar(base.db)
    assert _lanzar(base, md, tmp_path, "s", simular=simular) == 0
    salida = capsys.readouterr().out
    assert "Detalle: 0 días cambiados" in salida
    for n in ESPERADO:
        assert f"{d(n).isoformat()} · no se toca: ya tiene sus alimentos" in salida, n
    assert _volcar(base.db) == reparada


# -- R8: restoring from nutricion.md does not break (C-78) ------------------------------------------------------

def test_restaurar_desde_nutricion_md(base, md, tmp_path):  # R8
    assert _lanzar(base, md, tmp_path, "s") == 0
    texto = historial_md.componer(base.db, base.store, HOY).texto
    lectura = imp.leer(texto, None, HOY)
    dudosos = {x.dia for x in lectura.dudosos}
    dias = {x.dia: x for x in lectura.dias}
    for n in (2, 7, 9):
        assert d(n) not in dudosos, n
        leidas = [(a.nombre, a.kcal) for a in dias[d(n)].alimentos]
        assert leidas == [(historial_md.sanear(nombre), float(k)) for nombre, _, _, k in ESPERADO[n]], n
        assert sum(k for _, k in leidas) == TOTALES_BASE[n]


@pytest.mark.parametrize("signo", ["−", "-"])
def test_el_lector_de_la_009_entiende_kcal_negativas(signo):  # R8
    celda = f"pan de prueba 40 g 100 / ajuste hasta el total del día {signo}40 → TOTAL 60"
    lineas = imp.leer_kcal(celda, d(4))
    assert [(a.nombre, a.kcal) for a in lineas] == [("pan de prueba", 100), (AJ, -40)]


# -- the order is in the terminal ---------------------------------------------------------------------------------

def test_la_orden_esta_en_la_terminal(monkeypatch, tmp_path):
    llamadas = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", tmp_path / "data.sqlite3")
    monkeypatch.setattr(cli, "_cmd_importar_detalle",
                        lambda md, simular, **kw: llamadas.append((md, simular, kw["db_path"])) or 0)
    assert cli.main(["importar-detalle", "--md", "a.md", "--simular"]) == 0
    assert cli.main(["importar-detalle", "--md", "a.md"]) == 0
    assert llamadas == [(Path("a.md"), True, tmp_path / "data.sqlite3"), (Path("a.md"), False, tmp_path / "data.sqlite3")]
    assert not (tmp_path / "data.sqlite3").exists()
