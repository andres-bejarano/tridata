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

VALOR = 173  # an invented HR max, the value «leaked»
PULSO = {"fc_max": VALOR, "fc_reposo": 51, "zonas_fc": [[51, 119], [120, 139], [140, 152], [153, 164], [165, VALOR]],
         "fc_origen": {"tipo": "prueba", "mes": "2025-11"}}


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
    (repo / "notas.md").write_text(f"mi máxima es {VALOR} ppm\n")
    _git(repo, "add", "notas.md")
    assert privados.comprobar(db, repo, [])["repo"] == 1  # staged, not committed: only the tracked file
    _git(repo, "commit", "-q", "-m", "fuga")
    assert privados.comprobar(db, repo, [])["repo"] == 2  # the tracked file and the + line of its commit


def test_un_valor_en_el_historial_cuenta_aunque_se_borre(repo, db):  # R9 · commits
    (repo / "fuga.txt").write_text(f"{VALOR}\n")
    _git(repo, "add", "fuga.txt")
    _git(repo, "commit", "-q", "-m", "fuga")
    _git(repo, "rm", "-q", "fuga.txt")
    _git(repo, "commit", "-q", "-m", "quitar")
    assert privados.comprobar(db, repo, [])["repo"] == 2  # the + of one commit and the − of the other


def test_un_valor_en_un_registro_cuenta_uno(repo, db, tmp_path):  # R9 · logs
    registros = tmp_path / "registros"
    (registros / "viejo").mkdir(parents=True)
    (registros / "viejo" / "web.log").write_text(f"pulso {VALOR}\n")
    (registros / "data.sqlite3").write_bytes(b"no es un registro")
    r = privados.comprobar(db, repo, [registros])
    assert (r["repo"], r["registros"]) == (0, 1)


def test_la_salida_nunca_dice_el_valor(repo, db, tmp_path, capsys, monkeypatch):  # R9 · C-151
    from tridata import cli

    (repo / "notas.md").write_text(f"{VALOR}\n")
    _git(repo, "add", "notas.md")
    monkeypatch.setattr(cli, "DEFAULT_DB_PATH", db)
    codigo = cli.main(["comprobar-privados", "--repo", str(repo), "--registros", str(tmp_path / "no-hay")])
    salida = capsys.readouterr()
    assert codigo == 1  # something appears: the check fails
    for valor in ("173", "51", "119", "120", "139", "140", "152", "153", "164", "165"):
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
