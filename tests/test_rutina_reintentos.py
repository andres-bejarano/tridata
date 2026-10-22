"""Unit 018: the daily routine tries again every hour until 14:00 and waits longer when Garmin limits (429).

Plans: `rutina-diaria` R-92, G-6, G-7, G-8, G-45, R-60 and Q-3. `daily_update.sh` is copied to a temporary
folder and runs against a fake `tridata` (it only writes down what it is asked and answers with the codes it is
given) and a fake `sleep` (it writes down its seconds and returns at once), with EVERY variable of the script
set to a temporary path: nothing real is ever touched (incident of unit 015). Garmin is never called.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "daily_update.sh"

FALSO = """#!/usr/bin/env bash
echo "$*" >> "$LLAMADAS"
if [ "$1" = "sync" ] && [ "${2:-}" != "--laps" ] && [ "${2:-}" != "--weather" ] && [ -s "$SYNC_CODIGOS" ]; then
    c=$(head -n 1 "$SYNC_CODIGOS")
    tail -n +2 "$SYNC_CODIGOS" > "$SYNC_CODIGOS.resto"
    mv "$SYNC_CODIGOS.resto" "$SYNC_CODIGOS"
    if [ "$c" != "0" ]; then
        echo "sync FAILED: sesion (GarminAuthError: Could not authenticate with Garmin Connect: <email>)"
        exit "$c"
    fi
fi
if [ "$1" = "sync" ]; then echo "Sync complete."; fi
exit 0
"""

SLEEP = """#!/usr/bin/env bash
echo "$1" >> "$ESPERAS"
"""

FALLA_RUTINA = "sync FAILED tras 3 intentos — se reintenta en la próxima hora (hasta las 14:00)"


class Rutina:
    """The script in `tmp_path`, with its fake `tridata` and `sleep` and every variable in a temporary folder."""

    def __init__(self, tmp_path: Path) -> None:
        self.carpeta = tmp_path / "rutina"
        self.carpeta.mkdir()
        self.script = self.carpeta / "daily_update.sh"
        shutil.copy(SCRIPT, self.script)
        bin_ = self.carpeta / "bin"
        bin_.mkdir()
        for nombre, texto in (("tridata", FALSO), ("sleep", SLEEP)):
            ruta = bin_ / nombre
            ruta.write_text(texto, encoding="utf-8")
            ruta.chmod(ruta.stat().st_mode | stat.S_IEXEC)
        self.llamadas = self.carpeta / "llamadas.txt"
        self.esperas = self.carpeta / "esperas.txt"
        self.codigos = self.carpeta / "codigos.txt"
        self.log = self.carpeta / "sync.log"
        self.marca = self.carpeta / "tridata-home" / "last_routine"
        self.wide = self.carpeta / "last_wide_sync"
        self.wide.write_text(date.today().isoformat() + "\n", encoding="utf-8")  # no wide sweep by default
        self.entorno = {
            "PATH": f"{bin_}:{os.environ['PATH']}", "HOME": str(self.carpeta),
            "TRIDATA": str(bin_ / "tridata"), "LOG": str(self.log), "EXPORTS_DIR": str(self.carpeta / "exports"),
            "WIDE_MARKER": str(self.wide), "ROUTINE_MARKER": str(self.marca), "RETRY_DELAY_SECONDS": "60",
            "RATE_LIMIT_DELAY_SECONDS": "300", "LLAMADAS": str(self.llamadas), "ESPERAS": str(self.esperas),
            "SYNC_CODIGOS": str(self.codigos),
        }

    def correr(self, *codigos: int, args: tuple[str, ...] = ()) -> int:
        """One pass; `codigos` are what the next `tridata sync` answer, in order (0 when they run out)."""
        self.codigos.write_text("".join(f"{c}\n" for c in codigos), encoding="utf-8")
        for f in (self.llamadas, self.esperas):
            f.unlink(missing_ok=True)
        r = subprocess.run(["bash", str(self.script), *args], env=self.entorno, check=False,  # noqa: S603, S607
                           timeout=60, capture_output=True, text=True)
        return r.returncode

    def ordenes(self) -> list[str]:
        if not self.llamadas.exists():
            return []
        return self.llamadas.read_text(encoding="utf-8").splitlines()

    def esperas_s(self) -> list[int]:
        if not self.esperas.exists():
            return []
        return [int(x) for x in self.esperas.read_text(encoding="utf-8").split()]

    def texto_log(self) -> str:
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def texto_marca(self) -> str:
        return self.marca.read_text(encoding="utf-8").strip()


@pytest.fixture
def rutina(tmp_path) -> Rutina:
    return Rutina(tmp_path)


def _syncs(ordenes: list[str]) -> list[str]:
    return [o for o in ordenes if o.split()[0] == "sync" and "--laps" not in o and "--weather" not in o]


HOY_ISO = date.today().isoformat


# -- R4 · R-92, G-7, G-8: the same day, every hour -----------------------------------------------------------

def test_a_falla_y_deja_la_copia(rutina):  # R4 (a)
    assert rutina.correr(1, 1, 1) == 1
    log = rutina.texto_log()
    assert FALLA_RUTINA in log
    assert "mañana" not in log
    assert rutina.texto_marca() == f"{HOY_ISO()} copia"
    assert [o.split()[0] for o in rutina.ordenes()][:2] == ["backup", "export-nutricion"]
    assert len(_syncs(rutina.ordenes())) == 3


def test_b_la_siguiente_pasada_no_repite_la_copia(rutina):  # R4 (b)
    rutina.correr(1, 1, 1)
    assert rutina.correr() == 0
    ordenes = [o.split()[0] for o in rutina.ordenes()]
    assert "backup" not in ordenes
    assert ordenes[0] == "export-nutricion" and ordenes[-1] == "export-nutricion" and "export-pmc" in ordenes
    log = rutina.texto_log()
    assert "backup: ya hecha hoy" in log and log.rstrip().endswith("Done.")
    assert rutina.texto_marca() == f"{HOY_ISO()} hecha"


def test_c_ya_hecha_no_llama_a_nada(rutina):  # R4 (c)
    rutina.correr()
    assert rutina.texto_marca() == f"{HOY_ISO()} hecha"
    antes = rutina.texto_log().splitlines()
    assert rutina.correr() == 0
    despues = rutina.texto_log().splitlines()
    assert despues[:len(antes)] == antes
    assert despues[len(antes):] == [f"Rutina ya hecha hoy ({HOY_ISO()}): nada que hacer"]
    assert rutina.ordenes() == []


def test_d_forzar_corre_entera(rutina):  # R4 (d)
    rutina.correr()
    assert rutina.correr(args=("--forzar",)) == 0
    ordenes = [o.split()[0] for o in rutina.ordenes()]
    assert ordenes[:3] == ["backup", "export-nutricion", "sync"] and ordenes[-1] == "export-nutricion"
    assert rutina.texto_log().rstrip().endswith("Done.")


def test_e_la_marca_de_ayer_corre_entera(rutina):  # R4 (e)
    ayer = (date.today() - timedelta(days=1)).isoformat()
    rutina.marca.parent.mkdir(parents=True)
    rutina.marca.write_text(f"{ayer} hecha\n", encoding="utf-8")
    assert rutina.correr() == 0
    assert [o.split()[0] for o in rutina.ordenes()][:2] == ["backup", "export-nutricion"]
    assert rutina.texto_marca() == f"{HOY_ISO()} hecha"


def test_f_el_barrido_amplio_que_falla_tampoco_dice_manana(rutina):  # R4 (f) · G-8
    rutina.wide.write_text("", encoding="utf-8")  # it is due
    assert rutina.correr(1, 1, 1) == 1
    log = rutina.texto_log()
    assert "sync (wide) FAILED tras 3 intentos — se reintenta en la próxima hora (hasta las 14:00)" in log
    assert "mañana" not in log
    assert rutina.wide.read_text(encoding="utf-8") == ""
    assert rutina.correr() == 0
    assert rutina.wide.read_text(encoding="utf-8").strip() == date.today().strftime("%Y-%m-%d")


# -- R5 · G-6, G-45: waits that double, and longer on a 429 ---------------------------------------------------

def test_esperas_que_se_doblan(rutina):  # R5 · G-6
    assert rutina.correr(1, 1, 1) == 1
    assert rutina.esperas_s() == [60, 120]
    assert len(_syncs(rutina.ordenes())) == 3


def test_esperas_ante_un_429(rutina):  # R5 · G-45
    assert rutina.correr(75, 75, 75) == 1
    assert rutina.esperas_s() == [300, 600]
    assert len(_syncs(rutina.ordenes())) == 3
    assert "sync: Garmin limita las peticiones (429), espero 300 s" in rutina.texto_log()
    assert "sync: Garmin limita las peticiones (429), espero 600 s" in rutina.texto_log()


def test_bien_al_segundo_intento(rutina):  # R5: at most 3, and it stops when it works
    assert rutina.correr(75, 0) == 0
    assert rutina.esperas_s() == [300]
    assert len(_syncs(rutina.ordenes())) == 2
    assert "sync OK (intento 2/3)" in rutina.texto_log()


# -- R6 · R-60, Q-3: the routine's log and its environment ------------------------------------------------------

def test_el_log_de_la_rutina_sin_salud(rutina):  # R6
    rutina.correr(1, 75, 1)
    rutina.correr()
    log = rutina.texto_log()
    for patron in (r"2[.,]?437", r"12[.,]?345", r"27[.,]?000", r"71[.,]3", r"(?<![\d:,.\-])47(?![\d:])"):
        assert not re.search(patron, log), patron
    assert "Traceback" not in log and "@" not in log
    assert all(len(linea) <= 300 for linea in log.splitlines())


def test_cada_variable_del_script_esta_en_el_entorno_de_los_tests(rutina):  # R6 (incident of unit 015)
    variables = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*):-", SCRIPT.read_text(encoding="utf-8")))
    assert {"TRIDATA", "LOG", "EXPORTS_DIR", "WIDE_MARKER", "ROUTINE_MARKER", "RETRY_DELAY_SECONDS",
            "RATE_LIMIT_DELAY_SECONDS"} <= variables
    assert variables <= set(rutina.entorno), sorted(variables - set(rutina.entorno))


def test_rutas_por_defecto_de_lo_nuevo():  # R4: derived from $HOME, the tests set a temporary one
    texto = SCRIPT.read_text(encoding="utf-8")
    assert 'ROUTINE_MARKER="${ROUTINE_MARKER:-$HOME/.tridata/last_routine}"' in texto
    assert 'RATE_LIMIT_DELAY_SECONDS="${RATE_LIMIT_DELAY_SECONDS:-300}"' in texto
