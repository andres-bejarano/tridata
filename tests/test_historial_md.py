"""nutricion.md: the whole food history, rebuilt every day from the base (unit 015).

Plans: `exportar-historico` R-60..R-63 (C-73..C-77), rules G-9 (no export asks for Garmin credentials)
and G-36 (nutricion.md is a view of the base); `rutina-diaria` (the routine rebuilds nutricion.md after
the backup, and again at the end if the sync works); `importar-deficit` R-36 (C-78: nutricion.md is also
a valid source to restore). Temporary SQLite, every value invented, Garmin never called (the Claude
interpreter is a `MagicMock`; `daily_update.sh` runs against a fake `tridata`). «Today» is 2026-03-12.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from tridata import cli
from tridata.models import Activity, DailyStats
from tridata.nutricion import (
    DESDE_SIEMPRE, Ajustes, Alimento, Cierres, Recetario, RegistroComidas, RegistroPeso, media_7_dias,
)
from tridata.nutricion import historial_md
from tridata.nutricion import importar_deficit as imp
from tridata.nutricion.recetas import leer_cargas
from tridata.storage import DataStore
from tridata.web import crear_app

HOY = date(2026, 3, 12)
D9, D10, D11 = date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11)
AHORA = datetime(2026, 3, 12, 8, 1)
REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "daily_update.sh"
PESOS = [80.9, 80.8, 80.6, 80.5, 80.4, 80.3, 80.2, 80.1, 80.0, 79.9]  # 1/3 .. 10/3
CABECERA_REGISTRO = ("| Fecha | Día | Entreno | Comidas (kcal) | Comido | Gasto estimado | Gasto real | Objetivo "
                     "| Margen |")
COMIDAS_D9 = [("avena", 60, 230), ("plátano", 120, 105), ("guiso de prueba", 350, 420)]


def _kcal(n: float) -> str:
    """The file's way: whole kcal with a thousands dot, «−» for a negative."""
    entero = int(abs(n) + 0.5)
    return ("−" if n < 0 and entero else "") + f"{entero:,}".replace(",", ".")


class Base:
    """The fixture of the contract, in a temporary SQLite."""

    def __init__(self, tmp_path: Path, *, con_ajustes: bool = True, con_pendiente: bool = True,
                 vacia: bool = False) -> None:
        self.db = tmp_path / "data.sqlite3"
        self.ajustes = Ajustes(self.db)
        self.registro = RegistroComidas(self.db)
        self.pesos = RegistroPeso(self.db)
        self.recetario = Recetario(self.db)
        self.store = DataStore(self.db)
        if vacia:
            return
        if con_ajustes:
            self.ajustes.cambiar({"basal_kcal": 1800, "pasos_por_defecto": 10000, "kcal_por_paso": 0.04,
                                  "objetivo_pct": -10}, desde=DESDE_SIEMPRE)
        for i, kg in enumerate(PESOS):
            self.pesos.apuntar(date(2026, 3, 1) + timedelta(days=i), kg, "Inicio" if i == 0 else None, hoy=HOY)
        self.registro.guardar([Alimento(nombre=n, dia=D9, cantidad=g, unidad="g", kcal=k) for n, g, k in COMIDAS_D9],
                              hoy=D9, texto="desayuno y comida", sin_cambios=True)
        self.registro.importar_dia(D10, [Alimento(nombre=imp.TOTAL_IMPORTADO, dia=D10, cantidad=1, unidad="día",
                                                  kcal=2150, estimado=True, supuestos=[imp.SUPUESTO])], HOY)
        if con_pendiente:
            self.registro.guardar([Alimento(nombre="pan de prueba", dia=D11)], hoy=D11, texto="pan", sin_cambios=True)
        self.recetario.crear_fijo({"nombre": "Yogur de prueba", "kcal": 64, "por": "100g"})
        self.recetario.crear_fijo({"nombre": "Barrita de prueba", "kcal": 190, "por": "unidad"})
        self.recetario.crear_olla({"nombre": "Olla de prueba pequeña", "tara_g": 1500})
        self.recetario.crear_receta({"nombre": "Guiso de prueba", "peso_neto_g": 1000, "ingredientes": [
            {"nombre": "cebolla", "cantidad": 100, "unidad": "g", "kcal": 400},
            {"nombre": "tomate", "cantidad": 200, "unidad": "g", "kcal": 700}]}, hoy=date(2026, 3, 2))
        self.store.save_daily_stats(DailyStats(stat_date=D9, steps=8000, total_calories=2750))
        self.store.save_activities([Activity(activity_id="r1", activity_date=D9, activity_type="running",
                                             name="Rodaje de prueba", duration_seconds=3000, calories=600)])
        if con_ajustes:
            # the close of unit 014, as it saves it: 03-09 closed with Garmin, 03-10 and 03-11 with the food closed
            Cierres(self.db, ajustes=self.ajustes, pesos=self.pesos, store=self.store, registro=self.registro).al_dia(HOY)
        # planned AFTER the close, so it stays planned on that day
        self.registro.guardar([Alimento(nombre="cena de prueba", dia=D9, cantidad=1, unidad="ración", kcal=500)],
                              hoy=D9 - timedelta(days=1), texto="cena", sin_cambios=True)

    def texto(self) -> str:
        return historial_md.generar(self.db, self.store, HOY, ahora=AHORA)

    def cliente(self) -> TestClient:
        return TestClient(crear_app(self.registro, MagicMock(), self.ajustes, hoy=lambda: HOY, store=self.store))


@pytest.fixture
def base(tmp_path):
    return Base(tmp_path)


def _seccion(texto: str, titulo: str) -> list[str]:
    """The lines of the table under `## titulo` (header and separator included)."""
    dentro, filas = False, []
    for linea in texto.splitlines():
        if linea.startswith("## "):
            dentro = linea[3:].strip() == titulo
            continue
        if dentro and linea.startswith("|"):
            filas.append(linea)
    return filas


def _celdas(linea: str) -> list[str]:
    return [c.strip() for c in linea.strip().strip("|").split("|")]


def _fila(texto: str, dia: date) -> list[str]:
    filas = [_celdas(f) for f in _seccion(texto, imp.REGISTRO) if _celdas(f)[0] == dia.isoformat()]
    assert len(filas) == 1, filas
    return filas[0]


# -- R1: the daily record (criterio portante) ------------------------------------------------------------

def test_registro_diario_del_fixture(base, tmp_path, monkeypatch, capsys):  # R1 · R-60, C-73, G-9
    salida = tmp_path / "out" / "nutricion.md"
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", base.db)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)

    assert cli.main(["export-nutricion", "--out", str(salida)]) == 0  # no Garmin credentials needed (G-9)

    texto = salida.read_text(encoding="utf-8")
    registro = _seccion(texto, imp.REGISTRO)
    assert registro[0] == CABECERA_REGISTRO
    assert [_celdas(f)[0] for f in registro[2:]] == ["2026-03-09", "2026-03-10", "2026-03-11"]
    fila = _fila(texto, D9)
    pantalla = base.cliente().get(f"/api/dia?fecha={D9.isoformat()}").json()
    assert fila == ["2026-03-09", "lunes", "Rodaje de prueba",
                    "avena 60 g 230 / plátano 120 g 105 / guiso de prueba 350 g 420 → TOTAL 755", "755",
                    _kcal(pantalla["gasto"]["estimado_kcal"]), "2.750", "−10 % · 2.475", "1.720"]
    assert "cena de prueba" not in texto  # what is planned neither appears nor adds


def test_un_dia_solo_con_lo_planeado_no_sale(base):  # R1 · R-60
    base.registro.guardar([Alimento(nombre="comida futura", dia=HOY, cantidad=1, unidad="ración", kcal=700)],
                          hoy=HOY - timedelta(days=1), texto="plan", sin_cambios=True)
    assert HOY.isoformat() not in [_celdas(f)[0] for f in _seccion(base.texto(), imp.REGISTRO)]


# -- R2: the same numbers as the day screen ----------------------------------------------------------------

@pytest.mark.parametrize("dia", [D9, D10, D11])
def test_mismos_numeros_que_la_pantalla(base, dia):  # R2 · R-61, C-75
    fila = _fila(base.texto(), dia)
    pantalla = base.cliente().get(f"/api/dia?fecha={dia.isoformat()}").json()

    g, objetivo = pantalla["gasto"], pantalla["objetivo"]
    assert fila[4] == _kcal(pantalla["total_kcal"])
    assert fila[5] == _kcal(g["estimado_kcal"])
    assert fila[6] == ("—" if g["real_kcal"] is None else _kcal(g["real_kcal"]))
    assert fila[7] == f"−10 % · {_kcal(objetivo['kcal'])}"
    assert fila[8] == _kcal(objetivo["kcal"] - pantalla["total_kcal"])


def test_dia_sin_cerrar_con_garmin_usa_el_estimado(base):  # R2 · R-61, C-75
    fila = _fila(base.texto(), D10)
    pantalla = base.cliente().get(f"/api/dia?fecha={D10.isoformat()}").json()

    assert pantalla["gasto"]["origen"] == "estimado" and fila[6] == "—"
    assert fila[3] == "2.150"  # the imported total is written as a number, not as a detail
    objetivo = round(pantalla["gasto"]["estimado_kcal"] * 0.9, 1)
    assert fila[7] == f"−10 % · {_kcal(objetivo)}" and fila[8] == _kcal(objetivo - 2150)


# -- R3, R4: weights, recipes, fixed foods and pots ---------------------------------------------------------

def test_seguimiento_de_peso(base):  # R3 · R-60, C-73
    filas = _seccion(base.texto(), imp.PESOS)
    assert _celdas(filas[0]) == ["Fecha", "Peso", "Notas", "Media 7 días"]
    datos = [_celdas(f) for f in filas[2:]]
    assert len(datos) == 10
    assert datos[0] == ["Domingo 1/3/2026", "80.9 kg", "Inicio", "80,9"]
    assert datos[8][:3] == ["Lunes 9/3/2026", "80 kg", ""]
    todos = base.pesos.entre(date(2026, 1, 1), HOY)
    for i, celdas in enumerate(datos):
        media = media_7_dias(todos, date(2026, 3, 1) + timedelta(days=i))
        assert media is not None and celdas[3] == imp.numero(media.kg)


def test_recetas_fijos_y_ollas(base):  # R4 · R-60, C-73
    texto = base.texto()
    referencias = _seccion(texto, imp.REFERENCIAS)
    assert _celdas(referencias[0]) == ["Plato", "Ración", "Kcal", "Notas"]
    datos = [_celdas(f)[:3] for f in referencias[2:]]
    assert ["Guiso de prueba (cebolla 100 g + tomate 200 g)", "receta 1.000 g total", "1.100 kcal"] in datos
    assert ["Yogur de prueba", "100 g", "64 kcal"] in datos
    assert ["Barrita de prueba", "1 unidad", "190 kcal"] in datos
    assert len(datos) == 3
    taras = _seccion(texto, imp.TARAS)
    assert _celdas(taras[0]) == ["Recipiente", "Peso"]
    assert [_celdas(f) for f in taras[2:]] == [["Olla de prueba pequeña", "1.500 g"]]


def test_cabecera_dice_que_se_rehace_y_lo_que_no_vuelve(base):  # R-62, G-36
    cabecera = base.texto().split("\n## ")[0]
    assert "Generado por TriData el 2026-03-12 08:01 desde la base; se rehace cada día: no lo edites" in cabecera
    for perdido in ("g/ml", "macros", "estimado", "supuestos", "alias", "versiones antiguas", "objetivos",
                    "ajustes"):
        assert perdido in cabecera


def test_receta_provisional_y_version_en_uso(base):  # R4 · R-60
    receta = base.recetario.recetas()[0]
    base.recetario.otra_tanda(receta["id"], {"ingredientes": [{"nombre": "puerro", "cantidad": 300, "unidad": "g",
                                                               "kcal": 900}]}, HOY)
    datos = [_celdas(f)[:3] for f in _seccion(base.texto(), imp.REFERENCIAS)[2:]]
    assert ["Guiso de prueba (puerro 300 g)", "receta entera", "900 kcal"] in datos
    assert not any("cebolla" in d[0] for d in datos)  # only the version in use


# -- R5: back through the imports ---------------------------------------------------------------------------

def _comidos(registro: RegistroComidas, dia: date) -> list[tuple]:
    return [(a["nombre"], a["kcal"], a["cantidad"], a["unidad"]) for a in registro.apuntes_del_dia(dia)
            if a["estado"] == "comido"]


def test_vuelta_por_las_importaciones(base, tmp_path):  # R5 · R-62, C-76, G-36
    texto = base.texto()
    vacia = tmp_path / "vacia"
    vacia.mkdir()
    db2 = vacia / "data.sqlite3"

    lectura = imp.leer(texto, None, HOY)
    assert [(d.dia, d.motivo) for d in lectura.dudosos] == [(D11, imp.NO_CUADRA)]  # the food without kcal
    registro2, pesos2 = RegistroComidas(db2), RegistroPeso(db2)
    imp.aplicar(lectura, {}, registro2, pesos2, HOY)

    assert [(p["dia"], p["kg"]) for p in pesos2.entre(date(2026, 1, 1), HOY)] == \
        [(p["dia"], p["kg"]) for p in base.pesos.entre(date(2026, 1, 1), HOY)]
    assert pesos2.del_dia(date(2026, 3, 1))["nota"] == "Inicio"
    assert _comidos(registro2, D9) == [(n, float(k), float(g), "g") for n, g, k in COMIDAS_D9]
    assert [(a[0], a[1]) for a in _comidos(registro2, D10)] == [(imp.TOTAL_IMPORTADO, 2150)]
    assert registro2.apuntes_del_dia(D11) == []  # dubious: never an invented number

    filas = imp.filas_de_recetas(texto)
    recetario2 = Recetario(db2)
    resultado = recetario2.cargar_de_deficit(leer_cargas(filas.referencias, filas.taras), HOY)
    assert [estado for _, estado in resultado] == ["nueva"] * 4
    receta = recetario2.recetas()[0]
    assert receta["nombre"] == "Guiso de prueba" and not receta["por_revisar"]
    assert receta["en_uso"]["estado"] == "definitiva" and receta["en_uso"]["kcal_100g"] == 110
    assert {(f["nombre"], f["kcal"], f["por"]) for f in recetario2.fijos()} == {
        ("Yogur de prueba", 64, "100g"), ("Barrita de prueba", 190, "unidad")}
    assert [(o["nombre"], o["tara_g"]) for o in recetario2.ollas()] == [("Olla de prueba pequeña", 1500)]


def test_vuelta_sin_ningun_dudoso(tmp_path):  # R5 · R-36, C-78
    base = Base(tmp_path, con_pendiente=False)
    lectura = imp.leer(base.texto(), None, HOY)
    assert lectura.dudosos == []
    assert lectura.resumen().startswith("10 pesos · 2 días")
    assert len(lectura.referencias) == 3 and lectura.taras == ["Olla de prueba pequeña"]


# -- R6: what would break the reading ----------------------------------------------------------------------

@pytest.mark.parametrize("nombre", ["pan/aceite | casero", "arroz / pollo", "pasta + atún", "té; limón",
                                    "tortilla [grande]", "café → leche", "zumo = naranja", "cena: sopa",
                                    "pizza TOTAL 900"])
def test_nombres_peligrosos(tmp_path, nombre):  # R6 · R-62, G-36
    base = Base(tmp_path, vacia=True)
    base.registro.guardar([Alimento(nombre=nombre, dia=D9, cantidad=200, unidad="g", kcal=600),
                           Alimento(nombre="arroz", dia=D9, cantidad=100, unidad="g", kcal=130)],
                          hoy=D9, texto=nombre, sin_cambios=True)
    texto = base.texto()
    celda = _fila(texto, D9)[3]
    nombre_escrito = celda.split(" 200 g 600")[0]
    for peligroso in ("|", " / ", " + ", ";", "[", "]", "→", "=", ":", "TOTAL"):
        assert peligroso not in nombre_escrito
    if nombre == "pan/aceite | casero":
        assert nombre_escrito == "pan/aceite - casero"

    lectura = imp.leer(texto, None, HOY)
    assert lectura.dudosos == []
    [dia] = lectura.dias
    assert [(a.kcal, a.cantidad, a.unidad) for a in dia.alimentos] == [(600, 200, "g"), (130, 100, "g")]


def test_kcal_enteras_con_punto_de_miles_y_unidades_raras(tmp_path):  # R6 · R-62
    base = Base(tmp_path, vacia=True)
    base.registro.guardar([Alimento(nombre="lasaña", dia=D9, cantidad=450.5, unidad="g", kcal=1234.6),
                           Alimento(nombre="huevo", dia=D9, cantidad=2, unidad="ud", kcal=150),
                           Alimento(nombre="galletas", dia=D9, kcal=120)],
                          hoy=D9, texto="lasaña", sin_cambios=True)
    fila = _fila(base.texto(), D9)
    assert fila[3] == "lasaña 450,5 g 1.235 / huevo (2 ud) 150 / galletas 120 → TOTAL 1.505"
    assert fila[4] == "1.505"
    [dia] = imp.leer(base.texto(), None, HOY).dias
    assert [(a.nombre, a.kcal, a.cantidad, a.unidad) for a in dia.alimentos] == [
        ("lasaña", 1235, 450.5, "g"), ("huevo (2 ud)", 150, 1, "ración"), ("galletas", 120, 1, "ración")]


def test_alimento_sin_kcal_sale_legible_y_vuelve_dudoso(base):  # R6 · R-62, C-76
    texto = base.texto()
    assert _fila(texto, D11)[3] == "pan de prueba (sin kcal) → TOTAL 0"
    [dudoso] = imp.leer(texto, None, HOY).dudosos
    assert dudoso.dia == D11 and dudoso.motivo == imp.NO_CUADRA


# -- R7: privacy ------------------------------------------------------------------------------------------

def test_nutricion_md_esta_ignorado_por_git():  # R7 · R-63, C-77
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", "exports/nutricion.md"], check=False)  # noqa: S603, S607
    assert r.returncode == 0, "exports/nutricion.md is no longer ignored by .gitignore: it would go public"


def test_la_orden_solo_dice_ruta_y_recuentos(base, tmp_path, capsys):  # R7 · R-63, C-77
    salida = tmp_path / "nutricion.md"
    assert cli._cmd_export_nutricion(salida, db_path=base.db, hoy=HOY) == 0
    capturado = capsys.readouterr()
    assert capturado.out.strip() == f"Wrote {salida} · 3 días, 10 pesos, 1 recetas"
    for dato in ("755", "80.9", "80,9", "2.750", "2750", "2.150"):
        assert dato not in capturado.out + capturado.err


# -- R8: the daily routine, also with the sync down -----------------------------------------------------------

FALSO = """#!/usr/bin/env bash
echo "$*" >> "$LLAMADAS"
if [ "$1" = "sync" ] && [ "${SYNC_FALLA:-}" = "1" ]; then exit 1; fi
exit 0
"""


def _rutina(tmp_path: Path, sync_falla: bool) -> tuple[int, list[str], str]:
    carpeta = tmp_path / "rutina"
    carpeta.mkdir()
    script = carpeta / "daily_update.sh"
    shutil.copy(SCRIPT, script)
    falso = carpeta / "tridata"
    falso.write_text(FALSO, encoding="utf-8")
    falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
    llamadas, log, exports = carpeta / "llamadas.txt", carpeta / "sync.log", carpeta / "exports"
    entorno = {"PATH": os.environ["PATH"], "HOME": str(carpeta), "TRIDATA": str(falso), "LOG": str(log),
               "EXPORTS_DIR": str(exports), "WIDE_MARKER": str(carpeta / "last_wide_sync"),
               "RETRY_DELAY_SECONDS": "0", "LLAMADAS": str(llamadas), "SYNC_FALLA": "1" if sync_falla else "0"}
    r = subprocess.run(["bash", str(script)], env=entorno, check=False, timeout=60)  # noqa: S603, S607
    lineas = llamadas.read_text(encoding="utf-8").splitlines()
    return r.returncode, lineas, log.read_text(encoding="utf-8")


def test_rutina_con_el_sync_caido_ya_ha_rehecho_nutricion(tmp_path):  # R8 · R-60, C-74, rutina-diaria
    codigo, llamadas, log = _rutina(tmp_path, sync_falla=True)

    assert codigo != 0
    ordenes = [c.split()[0] for c in llamadas]
    assert ordenes[:3] == ["backup", "export-nutricion", "sync"]
    assert ordenes.count("export-nutricion") == 1
    assert llamadas[1] == f"export-nutricion --out {tmp_path / 'rutina' / 'exports' / 'nutricion.md'}"
    assert log.count("export-nutricion OK") == 1


def test_rutina_con_el_sync_bien_lo_rehace_dos_veces(tmp_path):  # R8 · R-60, C-74, rutina-diaria
    codigo, llamadas, log = _rutina(tmp_path, sync_falla=False)

    assert codigo == 0
    ordenes = [c.split()[0] for c in llamadas]
    assert ordenes[:2] == ["backup", "export-nutricion"]
    assert ordenes.count("export-nutricion") == 2
    assert ordenes[-1] == "export-nutricion" and ordenes[-2] == "export-pmc"
    assert log.count("export-nutricion OK") == 2 and log.rstrip().endswith("Done.")


def test_rutina_sin_entorno_usa_las_rutas_de_siempre():  # R8 · the 08:00 routine does the same as today
    texto = SCRIPT.read_text(encoding="utf-8")
    assert 'TRIDATA="${TRIDATA:-/home/ankalagon/anaconda3/envs/tridata/bin/tridata}"' in texto
    assert 'LOG="${LOG:-/home/ankalagon/projects/tridata/sync.log}"' in texto
    assert 'EXPORTS_DIR="${EXPORTS_DIR:-/home/ankalagon/projects/tridata/exports}"' in texto
    assert 'NUTRICION="$EXPORTS_DIR/nutricion.md"' in texto
    assert 'WIDE_MARKER="${WIDE_MARKER:-/home/ankalagon/.tridata/last_wide_sync}"' in texto
    assert 'RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-60}"' in texto


def test_un_fallo_de_la_primera_pasada_no_corta_la_rutina(tmp_path):  # R8 · R-60
    carpeta = tmp_path / "rutina"
    carpeta.mkdir()
    script = carpeta / "daily_update.sh"
    shutil.copy(SCRIPT, script)
    falso = carpeta / "tridata"
    falso.write_text(FALSO.replace('exit 0\n', 'if [ "$1" = "export-nutricion" ]; then exit 3; fi\nexit 0\n'),
                     encoding="utf-8")
    falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
    log = carpeta / "sync.log"
    entorno = {"PATH": os.environ["PATH"], "HOME": str(carpeta), "TRIDATA": str(falso), "LOG": str(log),
               "EXPORTS_DIR": str(carpeta / "exports"), "WIDE_MARKER": str(carpeta / "marca"),
               "RETRY_DELAY_SECONDS": "0", "LLAMADAS": str(carpeta / "llamadas.txt")}
    r = subprocess.run(["bash", str(script)], env=entorno, check=False, timeout=60)  # noqa: S603, S607

    ordenes = [c.split()[0] for c in (carpeta / "llamadas.txt").read_text(encoding="utf-8").splitlines()]
    assert "sync" in ordenes and "export-pmc" in ordenes  # the first failure did not stop it
    assert "export-nutricion FAILED (exit 3)" in log.read_text(encoding="utf-8")
    assert r.returncode == 1  # the second pass fails like any other export


# -- R9: edge cases -----------------------------------------------------------------------------------------

def test_base_vacia(tmp_path):  # R9 · R-60, R-36
    texto = Base(tmp_path, vacia=True).texto()
    for titulo in (imp.PESOS, imp.REGISTRO, imp.REFERENCIAS, imp.TARAS):
        filas = _seccion(texto, titulo)
        assert len(filas) == 2, (titulo, filas)  # header and separator only
    lectura = imp.leer(texto, None, HOY)
    assert (len(lectura.pesos), len(lectura.dias), lectura.dudosos) == (0, 0, [])


def test_si_la_escritura_falla_queda_el_anterior(base, tmp_path, monkeypatch):  # R9 · R-60
    ruta = tmp_path / "exports" / "nutricion.md"
    ruta.parent.mkdir()
    ruta.write_text("el de ayer\n", encoding="utf-8")

    def falla(*a, **k):
        raise OSError("disco lleno (inventado)")

    monkeypatch.setattr(historial_md.os, "replace", falla)
    with pytest.raises(OSError):
        historial_md.escribir(base.texto(), ruta)

    assert ruta.read_text(encoding="utf-8") == "el de ayer\n"
    assert [p.name for p in ruta.parent.iterdir()] == ["nutricion.md"]  # no temporary left behind


def test_escribir_crea_la_carpeta_y_reemplaza(base, tmp_path):  # R9 · R-60
    ruta = tmp_path / "nueva" / "nutricion.md"
    historial_md.escribir("uno\n", ruta)
    historial_md.escribir("dos\n", ruta)
    assert ruta.read_text(encoding="utf-8") == "dos\n"


def test_sin_ajustes_la_fila_lleva_rayas(tmp_path):  # R9 · R-61
    base = Base(tmp_path, con_ajustes=False)
    texto = base.texto()
    fila = _fila(texto, D9)
    assert fila[4] == "755"
    assert fila[5:] == ["—", "—", "—", "—"]
    assert len(_seccion(texto, imp.PESOS)) == 12 and len(_seccion(texto, imp.REFERENCIAS)) == 5
