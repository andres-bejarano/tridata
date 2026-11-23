"""Nothing personal enters the public repo (unit 105).

R1 the pre-commit hook rejects personal files (naming the file and why) · R2 gitleaks secrets ·
R3 without gitleaks the hook refuses, never passes in silence · R4 `scripts/ci/lint` checks the
whole versioned tree · R5 no local user in paths · R6 the old Asistente screenshot is gone ·
R7 AGENTS.md says how to turn the hook on.

The hook runs for real in temporary git repos. The fake secret is built at run time, so this
file never trips gitleaks or the CI itself. Every path is invented.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
COMPROBACION = RAIZ / "scripts" / "ci" / "ficheros_personales.py"
HOOKS = RAIZ / "scripts" / "hooks"
HOOK = HOOKS / "pre-commit"
GITLEAKS = shutil.which("gitleaks")


def _corre(orden: list[str], **opciones) -> subprocess.CompletedProcess[str]:
    return subprocess.run(orden, capture_output=True, text=True, check=False, **opciones)  # noqa: S603 - fixed commands


def _modulo():
    spec = importlib.util.spec_from_file_location("ficheros_personales", COMPROBACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# -- R1 / R4: the rules ------------------------------------------------------------------------------

RECHAZADOS = [
    "prueba.pdf",
    "docs/Analitica.PDF",
    "salida.gpx",
    "rutas/Carrera.GPX",
    "tests/fixtures/ruta-real.gpx",
    "fixtures/ruta-inventada.gpx",            # the exception is only inside tests/fixtures/
    "inscripciones/dorsal.txt",
    "docs/Inscripciones/foto.jpg",
    "inscriptions/a/b.md",
    "deficit.md",
    "deficit/deficit.md",
    "datos/Deficit_2026.xlsx",
    ".env",
    ".env.local",
    "config/prod.env",
    "src/.ENV",
    "env",
    "envio.txt",
    "Env.sh",
    ".env.example.bak",
]

ACEPTADOS = [
    ".env.example",
    "tests/fixtures/ruta-inventada.gpx",
    "tests/fixtures/Ruta_Sintetica.GPX",
    "tests/fixtures/deficit_inventado.md",
    "tests/fixtures/deficit_detalle_inventado.md",
    "src/tridata/nutricion/importar_deficit.py",  # «deficit» in the middle of a name is not a match
    "README.md",
    "docs/screenshots/today.png",
    "scripts/ci/lint",
]


@pytest.mark.parametrize("ruta", RECHAZADOS)
def test_r1_cada_regla_rechaza_con_motivo(ruta):
    motivo = _modulo().motivo(ruta)
    assert motivo, f"{ruta} debería rechazarse"


@pytest.mark.parametrize("ruta", ACEPTADOS)
def test_r1_las_excepciones_y_lo_normal_pasan(ruta):
    assert _modulo().motivo(ruta) is None


def test_r1_la_linea_de_ordenes_nombra_fichero_y_motivo():
    proceso = _corre([sys.executable, str(COMPROBACION), "a/prueba.pdf", "README.md", "x/.env"])
    assert proceso.returncode != 0
    salida = proceso.stdout + proceso.stderr
    assert "a/prueba.pdf" in salida and "x/.env" in salida
    assert "README.md" not in salida
    assert "PDF" in salida


def test_r1_sin_ficheros_vetados_sale_bien():
    proceso = _corre([sys.executable, str(COMPROBACION), "README.md", ".env.example"])
    assert proceso.returncode == 0, proceso.stdout + proceso.stderr


# -- helpers for temporary repos ---------------------------------------------------------------------

def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return _corre(["git", "-C", str(repo), *args], env=env)  # noqa: S607 - git from PATH, as the hook


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Prueba")
    _git(repo, "config", "user.email", "prueba@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.hooksPath", str(HOOKS))
    (repo / "README.md").write_text("hola\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    assert _git(repo, "commit", "-q", "--no-verify", "-m", "inicio").returncode == 0
    return repo


def _entorno_sin_gitleaks(tmp_path: Path) -> dict[str, str]:
    """A PATH with only what the hook needs, and no gitleaks."""
    binarios = tmp_path / "bin"
    binarios.mkdir()
    for nombre in ("bash", "env", "git", "dirname", "cat"):
        origen = shutil.which(nombre)
        assert origen, nombre
        (binarios / nombre).symlink_to(origen)
    (binarios / "python3").symlink_to(sys.executable)
    entorno = dict(os.environ)
    entorno["PATH"] = str(binarios)
    return entorno


def _commit(repo: Path, nombre: str, contenido: str, env: dict[str, str] | None = None):
    fichero = repo / nombre
    fichero.parent.mkdir(parents=True, exist_ok=True)
    fichero.write_text(contenido, encoding="utf-8")
    _git(repo, "add", nombre, env=env)
    return _git(repo, "commit", "-q", "-m", "prueba", env=env)


def _commits(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").stdout.strip())


# -- R1 / R2 / R3: the hook for real -----------------------------------------------------------------

def test_r1_el_hook_es_ejecutable():
    assert HOOK.is_file() and os.access(HOOK, os.X_OK)


@pytest.mark.parametrize("nombre", ["prueba.pdf", "inscripciones/dorsal.txt", "Deficit_semana.csv",
                                    "config/.env", "salida.GPX"])
def test_r1_el_hook_rechaza_un_fichero_personal(tmp_path, nombre):
    repo = _repo(tmp_path)
    proceso = _commit(repo, nombre, "nada\n")
    assert proceso.returncode != 0
    salida = proceso.stdout + proceso.stderr
    assert nombre in salida
    assert _commits(repo) == 1


def test_r1_el_hook_rechaza_un_pdf_aunque_no_haya_gitleaks(tmp_path):
    repo = _repo(tmp_path)
    proceso = _commit(repo, "prueba.pdf", "nada\n", env=_entorno_sin_gitleaks(tmp_path))
    assert proceso.returncode != 0
    assert "prueba.pdf" in proceso.stdout + proceso.stderr
    assert _commits(repo) == 1


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks no está instalado")
def test_r2_el_hook_rechaza_un_secreto(tmp_path):
    repo = _repo(tmp_path)
    clave = "AK" + "IA" + "QX7ZR4M2WB6KT3PY"  # invented, AWS-token shaped; built here so this file stays clean
    proceso = _commit(repo, "config.py", f'aws_access_key_id = "{clave}"\n')
    assert proceso.returncode != 0
    assert clave not in proceso.stdout + proceso.stderr  # --redact
    assert _commits(repo) == 1


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks no está instalado")
def test_r2_un_commit_limpio_pasa_sin_ruido(tmp_path):
    repo = _repo(tmp_path)
    proceso = _commit(repo, "src/suma.py", "def suma(a, b):\n    return a + b\n")
    assert proceso.returncode == 0, proceso.stdout + proceso.stderr
    assert proceso.stdout.strip() == "" and proceso.stderr.strip() == ""
    assert _commits(repo) == 2


def test_r3_sin_gitleaks_el_hook_no_deja_pasar(tmp_path):
    repo = _repo(tmp_path)
    proceso = _commit(repo, "src/suma.py", "x = 1\n", env=_entorno_sin_gitleaks(tmp_path))
    assert proceso.returncode != 0
    salida = proceso.stdout + proceso.stderr
    assert "gitleaks" in salida
    assert "AGENTS.md" in salida          # how to install it
    assert "--no-verify" in salida        # how to skip it knowingly
    assert _commits(repo) == 1


# -- R4: the lint over the whole versioned tree ------------------------------------------------------

def test_r4_el_arbol_de_hoy_pasa():
    proceso = _corre([sys.executable, str(COMPROBACION)], cwd=RAIZ)
    assert proceso.returncode == 0, proceso.stdout + proceso.stderr


def test_r4_un_fichero_vetado_versionado_hace_fallar(tmp_path):
    repo = _repo(tmp_path)
    (repo / "Inscripciones").mkdir()
    (repo / "Inscripciones" / "carrera.txt").write_text("x\n", encoding="utf-8")
    (repo / "informe.pdf").write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    assert _git(repo, "commit", "-q", "--no-verify", "-m", "saltado").returncode == 0
    proceso = _corre([sys.executable, str(COMPROBACION)], cwd=repo)
    assert proceso.returncode != 0
    salida = proceso.stdout + proceso.stderr
    assert "informe.pdf" in salida and "Inscripciones/carrera.txt" in salida


def test_r4_el_lint_corre_la_comprobacion():
    texto = (RAIZ / "scripts" / "ci" / "lint").read_text(encoding="utf-8")
    assert "scripts/ci/ficheros_personales.py" in texto


# -- R5: no local user in versioned files ------------------------------------------------------------

def test_r5_ningun_fichero_versionado_lleva_una_carpeta_personal():
    patron = re.compile("/" + "home" + r"/[A-Za-z0-9_][A-Za-z0-9_.-]*")
    listado = _corre(["git", "ls-files", "-z"], cwd=RAIZ)  # noqa: S607 - git from PATH
    assert listado.returncode == 0
    nombres = listado.stdout
    culpables = []
    for nombre in nombres.split("\0"):
        ruta = RAIZ / nombre
        if not nombre or not ruta.is_file():
            continue
        try:
            texto = ruta.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if patron.search(texto):
            culpables.append(nombre)
    assert culpables == []


def test_r5_la_rutina_y_su_servicio_usan_la_carpeta_personal():
    rutina = (RAIZ / "daily_update.sh").read_text(encoding="utf-8")
    for variable in ("TRIDATA", "LOG", "EXPORTS_DIR", "WIDE_MARKER"):
        assert re.search(rf'^{variable}="\$\{{{variable}:-\$\{{HOME\}}/', rutina, re.MULTILINE), variable
    servicio = (RAIZ / ".config" / "systemd" / "user" / "tridata-sync.service").read_text(encoding="utf-8")
    assert "ExecStart=%h/projects/tridata/daily_update.sh" in servicio


# -- R6 / R7 ------------------------------------------------------------------------------------------

def test_r6_sin_la_captura_del_asistente():
    assert not (RAIZ / "docs" / "screenshots" / "assistant.png").exists()
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "assistant.png" not in readme
    for captura in ("today.png", "nutrition.png", "fitness.png"):
        assert f"docs/screenshots/{captura}" in readme


def test_r7_agents_dice_como_activar_el_hook():
    texto = (RAIZ / "AGENTS.md").read_text(encoding="utf-8")
    assert "git config core.hooksPath scripts/hooks" in texto
