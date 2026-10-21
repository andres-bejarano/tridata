"""One-off import of deficit.md and its weight chart (unit 009, REC-6).

All data is INVENTED (tests/fixtures/deficit_inventado.md and grafica_inventada.html).
"Today" is 2026-03-12. The dubious cases are built in each test by changing that text.
"""
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from tridata import cli
from tridata.nutricion import Alimento, RegistroComidas, RegistroPeso
from tridata.nutricion.importar_deficit import (
    TOTAL_IMPORTADO, Duda, ErrorImportacion, Importaciones, leer, leer_grafica, leer_kcal,
)

HOY = date(2026, 3, 12)
FIXTURES = Path(__file__).parent / "fixtures"
MD = (FIXTURES / "deficit_inventado.md").read_text(encoding="utf-8")
HTML = (FIXTURES / "grafica_inventada.html").read_text(encoding="utf-8")

RESUMEN_LIMPIO = (
    "10 pesos · 7 días (6 con total, 1 con 4 alimentos) · 1 sin pesar · 0 dudosos · "
    "encontradas y NO importadas: 3 recetas o alimentos de referencia, 2 taras"
)
DETALLE = (
    "desayuno: avena 60g ~230 / plátano 120g ~105; comida: guiso de prueba 350g ~420 + "
    "pan/aceite ~180 = ~600 → TOTAL ~935"
)


def d(dia, mes=3):
    return date(2026, mes, dia)


def _cambiar(texto, viejo, nuevo):
    assert viejo in texto, viejo
    return texto.replace(viejo, nuevo, 1)


def _volcar(db):
    """Every table and its rows, to prove nothing changed (None if the file does not exist)."""
    if not Path(db).exists():
        return None
    with sqlite3.connect(db) as conn:
        tablas = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: sorted(map(repr, conn.execute(f'SELECT * FROM "{t}"').fetchall())) for t in tablas}  # noqa: S608


def _filas(db, tabla):
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]  # noqa: S608 - fixed names


def _respuestas(*valores):
    """An injected `input`: answers in order; when they run out, end of input (EOFError)."""
    cola = list(valores)
    preguntas = []

    def preguntar(texto=""):
        preguntas.append(texto)
        if not cola:
            raise EOFError
        valor = cola.pop(0)
        if isinstance(valor, BaseException):
            raise valor
        return valor

    preguntar.preguntas = preguntas
    return preguntar


@pytest.fixture
def entorno(tmp_path):
    """Temporary SQLite, backup folder and copies of the invented files."""
    md = tmp_path / "deficit.md"
    html = tmp_path / "grafica.html"
    md.write_text(MD, encoding="utf-8")
    html.write_text(HTML, encoding="utf-8")
    return {"db": tmp_path / "data.sqlite3", "copias": tmp_path / "backups", "md": md, "html": html,
            "tmp": tmp_path}


def _importar(e, *respuestas, simular=False, md=None, html="fixture", copias=None):
    preguntar = _respuestas(*respuestas)
    codigo = cli._cmd_importar(
        md or e["md"], e["html"] if html == "fixture" else html, simular,
        db_path=e["db"], backup_dir=copias or e["copias"], preguntar=preguntar, hoy=HOY,
    )
    return codigo, preguntar


def _dudoso(lectura):
    assert len(lectura.dudosos) == 1, [(x.apartado, x.motivo) for x in lectura.dudosos]
    return lectura.dudosos[0]


# -- Reading the clean fixture (R1, R2, R10) ----------------------------------------------

def test_lectura_del_fixture_limpio():
    lectura = leer(MD, HTML, HOY)

    assert lectura.resumen() == RESUMEN_LIMPIO
    assert len(lectura.pesos) == 10 and lectura.sin_pesar == 1 and lectura.dudosos == []
    primero = min(lectura.pesos, key=lambda p: p.dia)
    assert (primero.dia, primero.kg, primero.nota) == (d(1), 80.9, "Inicio")
    assert {p.dia: p.kg for p in lectura.pesos}[d(5)] == 80.6  # decimal comma
    assert [x.dia for x in lectura.dias] == [d(n) for n in range(4, 11)]
    assert lectura.referencias == ["Guiso de prueba", "Crema de prueba", "Yogur de prueba"]
    assert lectura.taras == ["Olla de prueba pequeña", "Sartén de prueba"]


# -- The kcal column (R2, R3) ---------------------------------------------------------------

def test_total_simple_con_comentario_es_una_linea_total():
    lineas = leer_kcal("~2.150 (incl. café 40 kcal)", d(4))
    assert isinstance(lineas, list) and len(lineas) == 1
    a = lineas[0]
    assert (a.nombre, a.kcal, a.cantidad, a.unidad, a.estimado) == (TOTAL_IMPORTADO, 2150, 1, "día", True)
    assert a.nombre == "total del día (importado)"
    assert a.supuestos == ["importado de deficit.md", "incl. café 40 kcal"]


@pytest.mark.parametrize("celda, kcal", [("**~2.300**", 2300), ("1980", 1980), ("~2.050", 2050),
                                         ("2.150 kcal", 2150)])
def test_otras_formas_de_total(celda, kcal):
    lineas = leer_kcal(celda, d(4))
    assert isinstance(lineas, list) and [a.kcal for a in lineas] == [kcal]
    assert lineas[0].supuestos == ["importado de deficit.md"]


def test_detalle_que_cuadra_es_una_linea_por_alimento():
    lineas = leer_kcal(DETALLE, d(9))
    assert isinstance(lineas, list)
    assert [(a.nombre, a.cantidad, a.unidad, a.kcal) for a in lineas] == [
        ("avena", 60, "g", 230), ("plátano", 120, "g", 105), ("guiso de prueba", 350, "g", 420),
        ("pan/aceite", 1, "ración", 180),
    ]
    assert all(a.estimado and a.supuestos == ["importado de deficit.md"] and a.dia == d(9) for a in lineas)


def test_detalle_sin_total_y_con_etiquetas_y_corchetes():
    celda = "1ª comida (13:30): arroz 200g ~260 [sobras] + pollo 150 g ~250; REAL: fruta ~60"
    lineas = leer_kcal(celda, d(9))
    assert isinstance(lineas, list)
    assert [(a.nombre, a.cantidad, a.unidad, a.kcal) for a in lineas] == [
        ("arroz", 200, "g", 260), ("pollo", 150, "g", 250), ("fruta", 1, "ración", 60),
    ]


@pytest.mark.parametrize("celda, propuesta", [
    (DETALLE.replace("TOTAL ~935", "TOTAL ~1.400"), 1400),     # 935 is more than 5 % away
    (DETALLE.replace("plátano 120g ~105", "plátano 120g"), 935),  # a piece without kcal
    ("cena: tortilla ~300 + ensalada", None),                    # no kcal and no TOTAL
])
def test_detalle_que_no_cuadra_es_dudoso(celda, propuesta):
    duda = leer_kcal(celda, d(9))
    assert isinstance(duda, Duda)
    assert duda.motivo == "el detalle no cuadra con el total" and duda.propuesta == propuesta


def test_detalle_dentro_del_cinco_por_ciento_cuadra():
    lineas = leer_kcal(DETALLE.replace("TOTAL ~935", "TOTAL ~970"), d(9))  # 935 is 3.6 % away
    assert isinstance(lineas, list) and len(lineas) == 4


@pytest.mark.parametrize("celda, propuesta", [
    ("~2.000–2.200", 2100), ("~2.000-2.200 (cumpleaños)", 2100), ("en curso (falta la cena)", None),
    ("—", None), ("pendiente", None),
])
def test_rangos_en_curso_y_sin_numero_son_dudosos(celda, propuesta):
    duda = leer_kcal(celda, d(4))
    assert isinstance(duda, Duda) and duda.propuesta == propuesta


# -- Dubious items (R4) ---------------------------------------------------------------------

FILA_04 = "~2.150 (incl. café 40 kcal)"


@pytest.mark.parametrize("cambio, apartado, texto, motivo, propuesta, pesos, dias", [
    ((FILA_04, "~2.000–2.200"), "Registro diario", "~2.000–2.200", "rango", 2100, 10, 6),
    ((FILA_04, "en curso (falta la cena)"), "Registro diario", "en curso", "sin un total", None, 10, 6),
    ((FILA_04, "sin apuntar"), "Registro diario", "sin apuntar", "sin un total", None, 10, 6),
    ((FILA_04, "~9.500"), "Registro diario", "~9.500", "500 y 8.000", None, 10, 6),
    (("| 2026-03-07 |", "| 2026-02-30 |"), "Registro diario", "2026-02-30", "fecha imposible", None, 10, 6),
    (("| 2026-03-10 |", "| 2026-03-13 |"), "Registro diario", "2026-03-13", "posterior a hoy", None, 10, 6),
    (("| Jueves 5/3/2026 |", "| Jueves 31/2/2026 |"), "Seguimiento de peso", "31/2/2026", "fecha imposible",
     None, 9, 7),
    (("| Martes 3/3/2026 | 80.8 kg", "| Martes 3/3/2026 | 300 kg"), "Seguimiento de peso", "300 kg",
     "entre 30 y 250", None, 9, 7),
    (("| 11 y 12/3/2026 | sin pesar | viaje de prueba |", "| Viernes 13/3/2026 | 79.0 kg | |"),
     "Seguimiento de peso", "13/3/2026", "aún no ha llegado", None, 10, 7),
])
def test_dudosos_del_deficit(cambio, apartado, texto, motivo, propuesta, pesos, dias):
    lectura = leer(_cambiar(MD, *cambio), None, HOY)

    dudoso = _dudoso(lectura)
    assert dudoso.apartado == apartado
    assert texto in dudoso.texto
    assert motivo in dudoso.motivo
    assert dudoso.propuesta == propuesta
    assert (len(lectura.pesos), len(lectura.dias)) == (pesos, dias)  # it does not count until decided
    assert "1 dudosos" in lectura.resumen()


def test_dia_repetido_es_una_sola_entrada_con_las_dos_filas():
    md = _cambiar(MD, "| 2026-03-06 | Viernes | Descanso | 1980 |", "| 2026-03-05 | Viernes | Descanso | 1980 |")
    lectura = leer(md, None, HOY)

    dudoso = _dudoso(lectura)
    assert dudoso.apartado == "Registro diario" and dudoso.dia == d(5)
    assert "~2.300" in dudoso.texto and "1980" in dudoso.texto
    assert "repetido" in dudoso.motivo
    assert dudoso.propuesta == 1980  # the last row
    assert len(lectura.dias) == 5


def test_peso_distinto_en_la_grafica_propone_el_de_la_tabla():
    html = _cambiar(HTML, "const weight = [80.9,", "const weight = [80.4,")
    lectura = leer(MD, html, HOY)

    dudoso = _dudoso(lectura)
    assert dudoso.dia == d(1) and "80,4" in dudoso.texto and "80,9" in dudoso.texto
    assert "gráfica" in dudoso.motivo
    assert dudoso.propuesta == 80.9
    assert len(lectura.pesos) == 9


def test_peso_que_solo_esta_en_la_grafica_propone_el_de_la_grafica():
    md = _cambiar(MD, "| Domingo 1/3/2026 | 80.9 kg | Inicio |\n", "")
    lectura = leer(md, HTML, HOY)

    dudoso = _dudoso(lectura)
    assert dudoso.dia == d(1) and "solo" in dudoso.motivo and dudoso.propuesta == 80.9
    assert len(lectura.pesos) == 9


# -- The chart (R11) --------------------------------------------------------------------------

def test_grafica_nulls_retoques_y_dias_futuros():
    pesos = leer_grafica(HTML, 2026)
    assert len(pesos) == 10  # null days (also the future ones) are ignored
    assert pesos[d(1)] == 80.9
    assert pesos[d(10)] == 79.9  # `weight[9] = 79.9;` after the arrays wins over 79.7


def test_grafica_cambio_de_anio():
    html = ("const days = ['30/12','31/12','1/1','2/1'];\nconst weight = [81.0, null, 80.5, 80.4];\n"
            "weight[1] = 80.8;\nweight[3] = null;\n")
    assert leer_grafica(html, 2025) == {date(2025, 12, 30): 81.0, date(2025, 12, 31): 80.8,
                                        date(2026, 1, 1): 80.5}


@pytest.mark.parametrize("html, falta", [
    ("const weight = [80.0];", "days"), ("const days = ['1/3'];", "weight"),
])
def test_grafica_incompleta_dice_que_falta(html, falta):
    with pytest.raises(ErrorImportacion, match=falta):
        leer_grafica(html, 2026)


@pytest.mark.parametrize("seccion", ["Seguimiento de peso", "Registro diario"])
def test_md_sin_seccion_dice_cual_falta(seccion):
    md = MD.replace(f"## {seccion}", "## Otra cosa")
    with pytest.raises(ErrorImportacion, match=seccion):
        leer(md, None, HOY)


# -- importar_dia: all or nothing per day (R8) --------------------------------------------

def test_importar_dia_todo_o_nada(tmp_path):
    db = tmp_path / "data.sqlite3"
    registro = RegistroComidas(db)
    lineas = leer_kcal(DETALLE, d(9))
    assert isinstance(lineas, list)

    assert registro.importar_dia(d(9), lineas, HOY) == 4
    guardadas = registro.apuntes_del_dia(d(9))
    assert [a["nombre"] for a in guardadas] == ["avena", "plátano", "guiso de prueba", "pan/aceite"]
    assert all(a["estado"] == "comido" and a["estimado"] for a in guardadas)
    assert guardadas[0]["supuestos"] == ["importado de deficit.md"]

    otra = [Alimento(nombre="otra cosa", dia=d(9), cantidad=1, unidad="ración", kcal=100)]
    assert registro.importar_dia(d(9), otra, HOY) == 0
    assert len(registro.apuntes_del_dia(d(9))) == 4
    assert registro.interpretaciones() == []


# -- The command: simulate (R1) -------------------------------------------------------------

def test_simular_no_escribe_nada(entorno, capsys):
    RegistroPeso(entorno["db"]).apuntar(d(2), 81.3, hoy=HOY)
    antes = _volcar(entorno["db"])

    codigo, preguntar = _importar(entorno, simular=True)

    salida = capsys.readouterr().out
    assert codigo == 0
    assert RESUMEN_LIMPIO in salida
    assert "Guiso de prueba" in salida and "Olla de prueba pequeña" in salida
    assert _volcar(entorno["db"]) == antes
    assert "importacion" not in antes
    assert not entorno["copias"].exists()
    assert preguntar.preguntas == []


def test_simular_sobre_base_que_no_existe_no_la_crea(entorno):
    codigo, _ = _importar(entorno, simular=True)
    assert codigo == 0 and not entorno["db"].exists()


# -- The command: confirm and save (R2, R6, R10) ---------------------------------------------

def test_guardar_el_fixture_limpio(entorno, capsys):
    codigo, preguntar = _importar(entorno, "s")

    salida = capsys.readouterr().out
    assert codigo == 0, salida
    assert any("¿Guardar? [s/N]" in p for p in preguntar.preguntas)
    db = entorno["db"]
    pesos = RegistroPeso(db)
    assert _filas(db, "peso_dia") == 10
    primero = pesos.del_dia(d(1))
    assert primero is not None and (primero["kg"], primero["nota"]) == (80.9, "Inicio")
    assert pesos.del_dia(d(11)) is None and pesos.del_dia(d(12)) is None  # «sin pesar»

    registro = RegistroComidas(db)
    dia4 = registro.apuntes_del_dia(d(4))
    assert len(dia4) == 1
    a = dia4[0]
    assert (a["nombre"], a["kcal"], a["cantidad"], a["unidad"], a["estado"], a["estimado"]) == (
        "total del día (importado)", 2150, 1, "día", "comido", True)
    assert a["supuestos"] == ["importado de deficit.md", "incl. café 40 kcal"]
    assert [x["kcal"] for x in registro.apuntes_del_dia(d(9))] == [230, 105, 420, 180]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(DISTINCT dia) FROM alimento_apuntado").fetchone()[0] == 7
    assert registro.interpretaciones() == []

    # R10: the import record
    ultima = Importaciones(db).ultima()
    assert ultima is not None
    assert ultima["resumen"]["referencias"] == ["Guiso de prueba", "Crema de prueba", "Yogur de prueba"]
    assert ultima["resumen"]["taras"] == ["Olla de prueba pequeña", "Sartén de prueba"]
    assert ultima["resumen"]["dudosos"] == []
    assert "md:" in ultima["huella"] and "html:" in ultima["huella"]
    assert re.match(r"\d{4}-\d{2}-\d{2}", ultima["fecha"])

    # R6: the backup was made before writing, with both source files, and its path printed
    copias = [p for p in entorno["copias"].iterdir() if p.is_dir()]
    assert len(copias) == 1
    copia = copias[0]
    assert str(copia) in salida
    assert (copia / "deficit.md").read_text(encoding="utf-8") == MD
    assert (copia / "grafica.html").read_text(encoding="utf-8") == HTML
    with sqlite3.connect(copia / "data.sqlite3") as conn:
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "peso_dia" in tablas:
            assert conn.execute("SELECT COUNT(*) FROM peso_dia").fetchone()[0] == 0
        assert "importacion" not in tablas


def test_sin_html_no_se_cruzan_pesos_y_la_copia_solo_lleva_el_md(entorno):
    codigo, _ = _importar(entorno, "s", html=None)
    assert codigo == 0
    assert _filas(entorno["db"], "peso_dia") == 10
    copia = next(p for p in entorno["copias"].iterdir() if p.is_dir())
    assert sorted(p.name for p in copia.iterdir()) == ["data.sqlite3", "deficit.md"]


# -- The command: review the dubious ones (R3, R4, R5, R9, R10) ---------------------------

def _md_con_dudosos():
    md = _cambiar(MD, FILA_04, "~2.000–2.200")                       # 1: range → Intro (2100)
    md = _cambiar(md, "**~2.300**", "~9.500")                         # 2: out of range → 1800
    md = _cambiar(md, "| Martes 3/3/2026 | 80.8 kg", "| Martes 3/3/2026 | 300 kg")  # 3: → «o»
    md = _cambiar(md, "~2.600 (fiesta de prueba)", "en curso (falta la cena)")   # 4: Intro, then «o»
    return _cambiar(md, "TOTAL ~935", "TOTAL ~1.400")                 # 5: detail ≠ total → Intro (1400)


def test_revisar_dudosos_y_guardar(entorno, capsys):
    entorno["md"].write_text(_md_con_dudosos(), encoding="utf-8")
    lectura = leer(_md_con_dudosos(), HTML, HOY)
    orden = [(x.apartado, x.dia) for x in lectura.dudosos]
    respuestas = {d(3): ["o"], d(4): [""], d(5): ["1800"], d(7): ["", "o"], d(9): [""]}
    seguidas = [r for _, dia in orden for r in respuestas[dia]]

    codigo, preguntar = _importar(entorno, *seguidas, "s")

    salida = capsys.readouterr().out
    assert codigo == 0, salida
    assert "~2.000–2.200" in salida and "el detalle no cuadra con el total" in salida
    db = entorno["db"]
    registro = RegistroComidas(db)
    assert [(a["nombre"], a["kcal"]) for a in registro.apuntes_del_dia(d(4))] == [(TOTAL_IMPORTADO, 2100)]
    assert [(a["nombre"], a["kcal"]) for a in registro.apuntes_del_dia(d(5))] == [(TOTAL_IMPORTADO, 1800)]
    assert registro.apuntes_del_dia(d(7)) == []
    assert [(a["nombre"], a["kcal"]) for a in registro.apuntes_del_dia(d(9))] == [(TOTAL_IMPORTADO, 1400)]
    assert RegistroPeso(db).del_dia(d(3)) is None
    assert _filas(db, "peso_dia") == 9

    # the «en curso» one had no proposal: Intro asked again
    preguntas_dia7 = [p for p in preguntar.preguntas if "Intro" in p or "número" in p]
    assert len(preguntas_dia7) == len(seguidas)

    # R9: every row is accounted for
    assert "9 pesos nuevos" in salida and "6 días nuevos" in salida
    ultima = Importaciones(db).ultima()
    assert ultima is not None
    r = ultima["resumen"]["resumen"]
    assert r["pesos_nuevos"] + r["pesos_ya"] + r["pesos_omitidos"] + r["sin_pesar"] == r["filas_pesos"] == 11
    assert r["dias_nuevos"] + r["dias_ya"] + r["dias_omitidos"] == r["filas_registro"] == 7

    # R10: each dubious one with its section, original text, reason and decision
    decisiones = {(x["apartado"], x["texto"]): x for x in ultima["resumen"]["dudosos"]}
    assert len(decisiones) == 5
    por_decision = sorted((x["decision"], x["valor"]) for x in decisiones.values())
    assert por_decision == [("aceptado", 1400), ("aceptado", 2100), ("corregido", 1800),
                            ("omitido", None), ("omitido", None)]
    assert all(x["motivo"] and x["texto"] and x["apartado"] for x in decisiones.values())


def test_un_numero_fuera_de_rango_no_vale_como_correccion(entorno):
    entorno["md"].write_text(_cambiar(MD, FILA_04, "~9.500"), encoding="utf-8")
    codigo, preguntar = _importar(entorno, "9500", "abc", "2000", "s")
    assert codigo == 0
    registro = RegistroComidas(entorno["db"])
    assert [a["kcal"] for a in registro.apuntes_del_dia(d(4))] == [2000]
    assert len([p for p in preguntar.preguntas if "Guardar" not in p]) == 3


def test_un_dia_futuro_solo_se_puede_omitir(entorno):
    entorno["md"].write_text(_cambiar(MD, "| 2026-03-10 |", "| 2026-03-13 |"), encoding="utf-8")
    codigo, preguntar = _importar(entorno, "", "2000", "o", "s")
    assert codigo == 0
    assert RegistroComidas(entorno["db"]).apuntes_del_dia(d(13)) == []
    assert len([p for p in preguntar.preguntas if "Guardar" not in p]) == 3


@pytest.mark.parametrize("respuestas", [("n",), ("",), ("si",), (), (KeyboardInterrupt(),)])
def test_no_confirmar_no_guarda_nada(entorno, capsys, respuestas):
    RegistroPeso(entorno["db"]).apuntar(d(2), 81.3, hoy=HOY)
    antes = _volcar(entorno["db"])

    _importar(entorno, *respuestas)

    assert "No se ha guardado nada" in capsys.readouterr().out
    assert _volcar(entorno["db"]) == antes
    assert not entorno["copias"].exists()


def test_cortar_en_mitad_de_los_dudosos_no_guarda_nada(entorno, capsys):
    entorno["md"].write_text(_md_con_dudosos(), encoding="utf-8")
    _importar(entorno, "", "1800")  # then end of input
    assert "No se ha guardado nada" in capsys.readouterr().out
    assert _volcar(entorno["db"]) is None
    assert not entorno["copias"].exists()


# -- The command: the backup (R6) -------------------------------------------------------------

@pytest.mark.parametrize("falla", ["codigo", "excepcion"])
def test_si_la_copia_falla_no_se_escribe_nada(entorno, monkeypatch, capsys, falla):
    RegistroPeso(entorno["db"]).apuntar(d(2), 81.3, hoy=HOY)
    antes = _volcar(entorno["db"])

    def copia_rota(*args, **kwargs):
        if falla == "excepcion":
            raise OSError("disco lleno (inventado)")
        return 1

    monkeypatch.setattr(cli, "_cmd_backup", copia_rota)
    codigo, _ = _importar(entorno, "s")

    assert codigo != 0
    assert "copia" in capsys.readouterr().err.lower()
    assert _volcar(entorno["db"]) == antes


# -- The command: twice, and what the app already had (R7, R8, R9) --------------------------

def test_importar_dos_veces_no_duplica(entorno, capsys):
    assert _importar(entorno, "s")[0] == 0
    antes = {t: _filas(entorno["db"], t) for t in ("peso_dia", "alimento_apuntado")}
    capsys.readouterr()

    # «N» to importing again: nothing at all
    volcado = _volcar(entorno["db"])
    codigo, preguntar = _importar(entorno, "", copias=entorno["tmp"] / "copias2")
    assert re.search(r"Ya importaste el \d{4}-\d{2}-\d{2} \(10 pesos, 7 días\)\. ¿Importar otra vez\? "
                     r"Solo se añadirá lo que falte \[s/N\]", preguntar.preguntas[0])
    assert "No se ha guardado nada" in capsys.readouterr().out
    assert _volcar(entorno["db"]) == volcado and not (entorno["tmp"] / "copias2").exists()

    # «s»: only what is missing, which is nothing
    codigo, _ = _importar(entorno, "s", "s", copias=entorno["tmp"] / "copias3")
    salida = capsys.readouterr().out
    assert codigo == 0, salida
    assert "0 pesos nuevos, 0 días nuevos; 10 y 7 ya estaban" in salida
    assert {t: _filas(entorno["db"], t) for t in ("peso_dia", "alimento_apuntado")} == antes
    assert _filas(entorno["db"], "importacion") == 2


def test_lo_que_ya_estaba_en_la_app_manda(entorno, capsys):
    db = entorno["db"]
    RegistroPeso(db).apuntar(d(2), 81.3, hoy=HOY)
    a_mano = RegistroComidas(db).anadir(Alimento(nombre="cena a mano", dia=d(6), cantidad=1, unidad="ración",
                                                 kcal=700), HOY)

    codigo, _ = _importar(entorno, "s")

    salida = capsys.readouterr().out
    assert codigo == 0, salida
    peso = RegistroPeso(db).del_dia(d(2))
    assert peso is not None and peso["kg"] == 81.3
    assert RegistroComidas(db).apuntes_del_dia(d(6)) == [a_mano]
    assert "2026-03-06: ya tenía comidas en la app" in salida
    assert "2026-03-02: ya tenía peso en la app" in salida
    assert "9 pesos nuevos, 6 días nuevos; 1 y 1 ya estaban" in salida
    ultima = Importaciones(db).ultima()
    assert ultima is not None
    r = ultima["resumen"]["resumen"]
    assert r["pesos_nuevos"] + r["pesos_ya"] + r["pesos_omitidos"] + r["sin_pesar"] == r["filas_pesos"] == 11
    assert r["dias_nuevos"] + r["dias_ya"] + r["dias_omitidos"] == r["filas_registro"] == 7


def test_las_cuentas_incluyen_los_pesos_que_solo_trae_la_grafica(entorno):
    entorno["md"].write_text(_cambiar(MD, "| Domingo 1/3/2026 | 80.9 kg | Inicio |\n", ""), encoding="utf-8")
    assert _importar(entorno, "", "s")[0] == 0
    ultima = Importaciones(entorno["db"]).ultima()
    assert ultima is not None
    r = ultima["resumen"]["resumen"]
    assert r["filas_pesos"] == 11 and r["pesos_nuevos"] == 10
    assert ultima["resumen"]["dudosos"][0]["decision"] == "aceptado"
    assert json.dumps(ultima["resumen"], ensure_ascii=False)  # stored as JSON


# -- Broken inputs (R11) ------------------------------------------------------------------

def test_md_que_no_existe(entorno, capsys):
    codigo, _ = _importar(entorno, "s", md=entorno["tmp"] / "no-existe.md")
    assert codigo != 0
    assert "no-existe.md" in capsys.readouterr().err
    assert not entorno["db"].exists()


def test_md_sin_registro_diario(entorno, capsys):
    entorno["md"].write_text(MD.replace("## Registro diario", "## Otra cosa"), encoding="utf-8")
    codigo, _ = _importar(entorno, "s")
    assert codigo != 0
    assert "Registro diario" in capsys.readouterr().err
    assert not entorno["db"].exists() and not entorno["copias"].exists()


def test_html_sin_weight(entorno, capsys):
    entorno["html"].write_text(HTML.replace("const weight", "const peso").replace("weight[9]", "peso[9]"),
                               encoding="utf-8")
    codigo, _ = _importar(entorno, "s")
    assert codigo != 0
    assert "weight" in capsys.readouterr().err
    assert not entorno["db"].exists()


def test_la_orden_esta_en_la_terminal(monkeypatch):
    llamadas = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "_cmd_importar", lambda md, html, simular: llamadas.append((md, html, simular)) or 0)
    assert cli.main(["importar-deficit", "--md", "a.md", "--simular"]) == 0
    assert cli.main(["importar-deficit", "--md", "a.md", "--html", "b.html"]) == 0
    assert llamadas == [(Path("a.md"), None, True), (Path("a.md"), Path("b.html"), False)]
