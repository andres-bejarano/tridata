"""Unit 031 (R9 · R-110, C-151, G-49): `tridata comprobar-privados` counts how many times the values of the stress
test saved in the local database appear in the repo (tracked files and history) and in the logs — and never prints
them. Every value is invented; the repo is a throwaway git repository.
"""
from __future__ import annotations

import subprocess  # noqa: S404 - the test builds a throwaway git repo
from pathlib import Path

import pytest

from tridata import privados
from tridata.nutricion import DESDE_SIEMPRE, Ajustes

VALOR = 207  # an invented HR max, the value «leaked»
PULSO = {"fc_max": VALOR, "fc_reposo": 43, "zonas_fc": [[43, 118], [119, 133], [134, 149], [150, 166], [167, VALOR]],
         "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}
FUGA = "zonas: 119-133, 134-149 y máxima 207"  # three values of the test on one line: that is a leak (037 R2)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)  # noqa: S603,S607


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "prueba@example.invalid")
    _git(r, "config", "user.name", "Prueba")
    (r / "codigo.py").write_text("LIMITE = 1730  # not the value: a different word\nx = 'a17b'\n")
    _git(r, "add", "codigo.py")
    _git(r, "commit", "-q", "-m", "inicio")
    return r


@pytest.fixture
def db(tmp_path) -> Path:
    d = tmp_path / "data.sqlite3"
    Ajustes(d).cambiar(PULSO, desde=DESDE_SIEMPRE)
    return d


def test_sin_fugas_cuenta_cero(repo, db, tmp_path):  # R9 · C-151
    registros = tmp_path / "registros"
    registros.mkdir()
    (registros / "sync.log").write_text("sync: todo bien a las 08:01\n")
    r = privados.comprobar(db, repo, [registros])
    assert r == {"valores": 10, "repo": 0, "registros": 0}


def test_un_valor_en_un_fichero_del_repo_cuenta_uno(repo, db, tmp_path):  # R9 · C-151
    (repo / "notas.md").write_text(f"{FUGA}\n")
    _git(repo, "add", "notas.md")
    assert privados.comprobar(db, repo, [])["repo"] == 1  # staged, not committed: only the tracked file
    _git(repo, "commit", "-q", "-m", "fuga")
    assert privados.comprobar(db, repo, [])["repo"] == 2  # the tracked file and the + line of its commit


def test_un_valor_en_el_historial_cuenta_aunque_se_borre(repo, db):  # R9 · commits
    (repo / "fuga.txt").write_text(f"{FUGA}\n")
    _git(repo, "add", "fuga.txt")
    _git(repo, "commit", "-q", "-m", "fuga")
    _git(repo, "rm", "-q", "fuga.txt")
    _git(repo, "commit", "-q", "-m", "quitar")
    assert privados.comprobar(db, repo, [])["repo"] == 2  # the + of one commit and the − of the other


def test_un_valor_en_un_registro_cuenta_uno(repo, db, tmp_path):  # R9 · logs
    registros = tmp_path / "registros"
    (registros / "viejo").mkdir(parents=True)
    (registros / "viejo" / "web.log").write_text(f"pulso {FUGA}\n")
    (registros / "data.sqlite3").write_bytes(b"no es un registro")
    r = privados.comprobar(db, repo, [registros])
    assert (r["repo"], r["registros"]) == (0, 1)


def test_la_salida_nunca_dice_el_valor(repo, db, tmp_path, capsys, monkeypatch):  # R9 · C-151
    from tridata import cli

    (repo / "notas.md").write_text(f"{FUGA}\n")
    _git(repo, "add", "notas.md")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db)
    codigo = cli.main(["comprobar-privados", "--repo", str(repo), "--registros", str(tmp_path / "no-hay")])
    salida = capsys.readouterr()
    assert codigo == 1  # something appears: the check fails
    for valor in ("207", "43", "118", "119", "133", "134", "149", "150", "166", "167"):
        assert valor not in salida.out + salida.err
    assert "1 aparici" in salida.out


def test_sin_valores_de_prueba_no_busca_nada(repo, tmp_path, capsys, monkeypatch):  # R9
    from tridata import cli

    d = tmp_path / "vacia.sqlite3"
    Ajustes(d).cambiar({**PULSO, "fc_origen": {"tipo": "configuracion"}}, desde=DESDE_SIEMPRE)
    assert privados.comprobar(d, repo, [])["valores"] == 0
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", d)
    assert cli.main(["comprobar-privados", "--repo", str(repo)]) == 0
    assert "nada que buscar" in capsys.readouterr().out


# -- unit 032 (R12 · R-119, C-160, G-50): the values of the blood test too ----------------------------------------

class _TranscriptorFijo:
    """The transcriber double: an invented blood test, never the API."""

    def transcribir(self, pdf: bytes):
        from tridata.conversaciones.medicos import TranscripcionMedica, ValorMedico

        valores = [ValorMedico(prueba="hemoglobina de prueba", valor="13,87", unidad="g/dL", rango="13-17", fecha=None),
                   ValorMedico(prueba="ferritina de prueba", valor="2468", unidad="ng/mL", rango=None, fecha=None),
                   ValorMedico(prueba="glucosa de prueba", valor="91", unidad="mg/dL", rango=None, fecha=None),
                   ValorMedico(prueba="orina de prueba", valor="negativo", unidad=None, rango=None, fecha=None)]
        uso = {"entrada": 0, "salida": 0, "cache_escritura": 0, "cache_lectura": 0}
        return TranscripcionMedica(tipo="analitica", titulo="Analítica de prueba", fecha="2026-03-02",
                                   valores=valores, notas=[]), uso, "claude-haiku-5-5"


@pytest.fixture
def db_con_analitica(db, tmp_path) -> Path:
    from tridata.conversaciones.medicos import Medicos

    carpeta = tmp_path / "medicos"
    carpeta.mkdir()
    (carpeta / "analitica-de-prueba.pdf").write_bytes(b"%PDF-1.4 inventado\n%%EOF\n")
    Medicos(db, carpeta, _TranscriptorFijo()).al_dia()
    return db


def test_la_analitica_limpia_cuenta_cero(repo, db_con_analitica, tmp_path):  # R12 · C-160
    r = privados.comprobar(db_con_analitica, repo, [tmp_path / "no-hay"])
    assert r["valores"] > 10 and (r["repo"], r["registros"]) == (0, 0)


def test_un_valor_de_la_analitica_cuenta_en_el_repo_y_en_los_registros(repo, db_con_analitica, tmp_path):  # R12
    (repo / "notas.md").write_text("hemoglobina de prueba 13.87 y glucosa de prueba 91\n")  # 91: too common
    _git(repo, "add", "notas.md")
    registros = tmp_path / "registros"
    registros.mkdir()
    (registros / "web.log").write_text("Ferritina de Prueba: 2468\n")
    r = privados.comprobar(db_con_analitica, repo, [registros])
    assert (r["repo"], r["registros"]) == (1, 1)


def test_la_salida_nunca_dice_un_valor_de_la_analitica(repo, db_con_analitica, capsys, monkeypatch):  # R12 · C-160
    from tridata import cli

    (repo / "notas.md").write_text("hemoglobina de prueba: 13,87\n")
    _git(repo, "add", "notas.md")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db_con_analitica)
    assert cli.main(["comprobar-privados", "--repo", str(repo)]) == 1
    salida = capsys.readouterr()
    for valor in ("13,87", "13.87", "2468", "hemoglobina", "ferritina"):
        assert valor not in salida.out + salida.err
    assert "analítica" in salida.out and "1 aparici" in salida.out


def test_solo_la_analitica_sin_prueba_de_esfuerzo_tambien_busca(repo, tmp_path):  # R12
    from tridata.conversaciones.medicos import Medicos

    d = tmp_path / "solo.sqlite3"
    carpeta = tmp_path / "medicos-solo"
    carpeta.mkdir()
    (carpeta / "a.pdf").write_bytes(b"%PDF-1.4 inventado\n%%EOF\n")
    Medicos(d, carpeta, _TranscriptorFijo()).al_dia()
    (repo / "fuga.txt").write_text("ferritina de prueba 2468\n")
    _git(repo, "add", "fuga.txt")
    assert privados.comprobar(d, repo, [])["repo"] == 1


# -- bug 037: no noise. A number on its own is not a leak; a value next to what it is, is ------------------------

def test_numeros_sueltos_de_la_prueba_no_cuentan(repo, db, tmp_path):  # 037 R2 (the bug: ~300 false positives)
    (repo / "codigo.py").write_text(f"TOPE = {VALOR}\nx = [43, 118]  # two values on a line are still noise\n")
    _git(repo, "add", "codigo.py")
    _git(repo, "commit", "-q", "-m", "numeros sueltos")
    registros = tmp_path / "registros"
    registros.mkdir()
    (registros / "sync.log").write_text("descargadas 133 actividades en 149 s\n")
    r = privados.comprobar(db, repo, [registros])
    assert (r["repo"], r["registros"]) == (0, 0)


def test_tres_valores_de_la_prueba_en_una_linea_cuentan_una_vez(repo, db):  # 037 R2
    (repo / "notas.md").write_text("43 118 119\notra línea 207\n")
    _git(repo, "add", "notas.md")
    assert privados.comprobar(db, repo, [])["repo"] == 1


def test_un_numero_de_la_analitica_sin_su_nombre_no_cuenta(repo, db_con_analitica, tmp_path):  # 037 R1
    (repo / "notas.md").write_text("PI = 13.87\nfactor = 2468\nhemoglobina de prueba: ver el PDF\n")
    _git(repo, "add", "notas.md")
    registros = tmp_path / "registros"
    registros.mkdir()
    (registros / "web.log").write_text("GET /api 2468 ms\n")
    r = privados.comprobar(db_con_analitica, repo, [registros])
    assert (r["repo"], r["registros"]) == (0, 0)


def test_el_nombre_de_la_analitica_vale_sin_mayusculas_ni_tildes(repo, db_con_analitica):  # 037 R1
    (repo / "notas.md").write_text("HEMOGLOBÍNA DE PRUEBA = 13,87\n")
    _git(repo, "add", "notas.md")
    assert privados.comprobar(db_con_analitica, repo, [])["repo"] == 1


def _commit_de_fuga(repo: Path) -> str:
    (repo / "fuga.txt").write_text(f"{FUGA}\n")
    _git(repo, "add", "fuga.txt")
    _git(repo, "commit", "-q", "-m", "fuga aceptada")
    _git(repo, "rm", "-q", "fuga.txt")
    _git(repo, "commit", "-q", "-m", "quitada")
    log = subprocess.run(["git", "-C", str(repo), "log", "--format=%H"], check=True, capture_output=True,  # noqa: S603,S607
                         text=True).stdout.split()
    return log[1]  # the commit that added it; log[0] removed it


def test_los_commits_aceptados_no_cuentan_pero_se_dicen(repo, db):  # 037 R4
    añadido = _commit_de_fuga(repo)
    quitado = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True,  # noqa: S603,S607
                             text=True).stdout.strip()
    assert privados.comprobar(db, repo, [])["repo"] == 2
    r = privados.comprobar(db, repo, [], aceptados=[añadido[:7], quitado])
    assert (r["repo"], r["aceptadas"]) == (0, 2)


def test_la_lista_de_aceptados_se_lee_de_su_fichero_local(repo, db, tmp_path, capsys, monkeypatch):  # 037 R4 · R5
    from tridata import cli

    añadido = _commit_de_fuga(repo)
    lista = tmp_path / "privados-aceptados"
    lista.write_text(f"# zonas viejas, aceptado por el usuario\n{añadido[:7]}\n\n")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(cli, "PRIVADOS_ACEPTADOS", lista)
    assert cli.main(["comprobar-privados", "--repo", str(repo)]) == 1  # the commit that removed it is not listed
    salida = capsys.readouterr().out
    assert "1 aparición" in salida and "aceptad" in salida
    lista.write_text(f"{añadido}\n" + subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,  # noqa: S603,S607
                                                      capture_output=True, text=True).stdout)
    assert cli.main(["comprobar-privados", "--repo", str(repo)]) == 0
    for valor in ("207", "119", "133", "134", "149"):
        assert valor not in capsys.readouterr().out
