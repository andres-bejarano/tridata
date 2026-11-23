"""Unit 104 (P-20261123-8b2747d0): one folder for the personal papers of sport and the daily copy into it.

- R1: with `TRIDATA_DOCS` the team reads the medical PDF of `$TRIDATA_DOCS/health`; the warnings name `TRIDATA_DOCS`
  (and say the old `TRIDATA_MEDICOS_DIR` was replaced by it).
- R2 (load-bearing): a file the team asks for (medical PDF or GPX) that, resolved with links and «..», falls outside
  `$TRIDATA_DOCS` is not read, and the answer says it only reads from the sports folder.
- R3, R4: `tridata backup` without `--dest` writes ONE compressed, verified copy (600) in `$TRIDATA_DOCS/tridata-backup/`;
  without the variable as always; with the variable but no folder (the Drive not mounted), locally and said.
- R5: an `--extra` that is a credential stops the copy (exit 1, nothing written).
- R6: 14 own copies kept; at most ONE old copy removed per natural day; nothing else is ever removed.
- R7: the routine still calls `tridata backup` (no `--dest`) and its line carries no path.
- R8: `.env.example` lists every variable the code reads, without values; `.gitignore` ignores the inscriptions.

Every folder is a temporary one with invented files: never the real folder of the user.
"""
from __future__ import annotations

import asyncio
import os
import re
import tarfile
import time
from datetime import datetime
from pathlib import Path

import pytest

from tridata import cli, mcp_server
from tridata.conversaciones.desde_pc import montar_equipo
from tridata.conversaciones.medicos import FueraDeLaCarpeta, Medicos
from tridata.mcp_server import build_server
from tridata.models import DailyStats
from tridata.storage import DataStore
from tridata.sync import carpeta_datos, dentro_de

RAIZ = Path(__file__).resolve().parents[1]
GPX = Path(__file__).parent / "fixtures" / "ruta-inventada.gpx"
SOLO_SU_CARPETA = "carpeta de deporte"


@pytest.fixture
def sin_variables(monkeypatch):
    for variable in ("TRIDATA_DOCS", "TRIDATA_MEDICOS_DIR"):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def docs(tmp_path, sin_variables):
    """An invented sports folder: health/ with a PDF, routes/ with a GPX, and a folder OUTSIDE it with the same."""
    d = tmp_path / "deporte-inventado"
    (d / "health").mkdir(parents=True)
    (d / "routes").mkdir()
    (d / "tridata-backup").mkdir()
    (d / "health" / "analitica-inventada.pdf").write_bytes(b"%PDF-1.4 analitica inventada")
    (d / "routes" / "ruta-inventada.gpx").write_bytes(GPX.read_bytes())
    fuera = tmp_path / "descargas-inventadas"
    fuera.mkdir()
    (fuera / "fuera-inventada.pdf").write_bytes(b"%PDF-1.4 fuera inventada")
    (fuera / "fuera-inventada.gpx").write_bytes(GPX.read_bytes())
    return d


# -- the folder and the frontier ---------------------------------------------------------------------------------------

def test_la_carpeta_sale_de_tridata_docs(tmp_path):
    assert carpeta_datos({}) is None
    assert carpeta_datos({"TRIDATA_DOCS": "  "}) is None
    assert carpeta_datos({"TRIDATA_MEDICOS_DIR": str(tmp_path)}) is None  # the old one is not read any more
    assert carpeta_datos({"TRIDATA_DOCS": str(tmp_path)}) == tmp_path
    assert carpeta_datos({"TRIDATA_DOCS": "~/inventada"}) == Path("~/inventada").expanduser()


def test_dentro_de_resuelve_enlaces_y_puntos(docs):
    fuera = docs.parent / "descargas-inventadas" / "fuera-inventada.gpx"
    (docs / "routes" / "enlace.gpx").symlink_to(fuera)
    assert dentro_de("routes/ruta-inventada.gpx", docs) == (docs / "routes" / "ruta-inventada.gpx").resolve()
    assert dentro_de(docs / "routes" / "ruta-inventada.gpx", docs) is not None
    assert dentro_de(fuera, docs) is None
    assert dentro_de("routes/../../descargas-inventadas/fuera-inventada.gpx", docs) is None
    assert dentro_de(docs / "routes" / "enlace.gpx", docs) is None


# -- R1: the medical PDF from $TRIDATA_DOCS/health, and the warnings -----------------------------------------------------

def test_las_analiticas_se_leen_de_health(tmp_path, docs, monkeypatch):  # R1 · R-221
    monkeypatch.setenv("TRIDATA_DOCS", str(docs))
    llamadas = {}

    def montar(db_path, **kw):
        llamadas.update(kw)
        return montar_equipo(tmp_path / "data.sqlite3", store=None, token=tmp_path / "google-token.json",
                             medicos_dir=kw.get("medicos_dir"), docs_dir=kw.get("docs_dir"))

    class _Servidor:
        def run(self, *a, **k):
            llamadas["run"] = True

    monkeypatch.setattr(mcp_server, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "DataStore", lambda *a, **k: None)
    monkeypatch.setattr("tridata.conversaciones.desde_pc.montar_equipo", montar)
    monkeypatch.setattr(mcp_server, "build_server", lambda *a, **k: _Servidor())
    mcp_server.main()
    assert llamadas["medicos_dir"] == docs / "health" and llamadas["docs_dir"] == docs and llamadas["run"]
    equipo = montar(None, medicos_dir=docs / "health", docs_dir=docs)
    assert [p["fichero"] for p in equipo.medicos.pendientes()] == ["analitica-inventada.pdf"]


def test_sin_tridata_docs_el_aviso_la_nombra(tmp_path, sin_variables):  # R1
    aviso = Medicos(tmp_path / "data.sqlite3", None).estado()["aviso"]
    assert "TRIDATA_DOCS" in aviso and "TRIDATA_MEDICOS_DIR" not in aviso


def test_con_la_variable_vieja_dice_que_se_sustituyo(tmp_path, sin_variables, monkeypatch):  # R1
    monkeypatch.setenv("TRIDATA_MEDICOS_DIR", str(tmp_path))
    aviso = Medicos(tmp_path / "data.sqlite3", None).estado()["aviso"]
    assert "TRIDATA_MEDICOS_DIR" in aviso and "TRIDATA_DOCS" in aviso and "sustitu" in aviso


def test_mcp_ya_no_lee_la_variable_vieja(tmp_path, sin_variables, monkeypatch):  # R1
    monkeypatch.setenv("TRIDATA_MEDICOS_DIR", str(tmp_path))
    llamadas = {}
    monkeypatch.setattr(mcp_server, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "DataStore", lambda *a, **k: None)
    monkeypatch.setattr("tridata.conversaciones.desde_pc.montar_equipo", lambda db, **kw: llamadas.update(kw))
    monkeypatch.setattr(mcp_server, "build_server", lambda *a, **k: type("S", (), {"run": lambda s, *a: None})())
    mcp_server.main()
    assert llamadas["medicos_dir"] is None and llamadas["docs_dir"] is None


# -- R2 (load-bearing): nothing outside $TRIDATA_DOCS is read --------------------------------------------------------------

def test_una_analitica_fuera_no_se_lee(tmp_path, docs):  # R2 · R-222
    fuera = docs.parent / "descargas-inventadas" / "fuera-inventada.pdf"
    (docs / "health" / "enlace-inventado.pdf").symlink_to(fuera)
    medicos = Medicos(tmp_path / "data.sqlite3", docs / "health", raiz=docs)
    assert [p["fichero"] for p in medicos.pendientes()] == ["analitica-inventada.pdf"]  # the link is not even listed
    for pedido in (str(fuera), "../../descargas-inventadas/fuera-inventada.pdf", "enlace-inventado.pdf"):
        with pytest.raises(FueraDeLaCarpeta):
            medicos.pdf(pedido)
    assert medicos.pdf("analitica-inventada.pdf")[0] == "analitica-inventada.pdf"


def _equipo(tmp_path, docs):
    store = DataStore(tmp_path / "data.sqlite3")
    equipo = montar_equipo(tmp_path / "data.sqlite3", store=store, medicos_dir=docs / "health", docs_dir=docs,
                           token=tmp_path / "google-token.json")
    return build_server(store, equipo=equipo)


def _perfil(server, gpx: str) -> str:
    resultado = asyncio.run(server.call_tool("get_perfil_ruta", {"gpx": gpx}))
    return "".join(c.text for c in resultado.content)


def test_un_gpx_fuera_no_se_lee_y_uno_dentro_si(tmp_path, docs, monkeypatch):  # R2 (portante)
    fuera = docs.parent / "descargas-inventadas" / "fuera-inventada.gpx"
    (docs / "routes" / "enlace-inventado.gpx").symlink_to(fuera)
    server = _equipo(tmp_path, docs)
    leidos: list[Path] = []
    original = Path.read_bytes

    def espia(self):
        leidos.append(self.resolve())
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", espia)
    for pedido in (str(fuera), str(docs / "routes" / ".." / ".." / "descargas-inventadas" / "fuera-inventada.gpx"),
                   str(docs / "routes" / "enlace-inventado.gpx"), "routes/../../descargas-inventadas/fuera-inventada.gpx"):
        respuesta = _perfil(server, pedido)
        assert SOLO_SU_CARPETA in respuesta and "Perfil de la ruta" not in respuesta, pedido
    assert fuera.resolve() not in leidos  # nothing outside was read
    for pedido in (str(docs / "routes" / "ruta-inventada.gpx"), "routes/ruta-inventada.gpx"):
        assert "Perfil de la ruta" in _perfil(server, pedido), pedido


def test_sin_tridata_docs_ningun_gpx_se_lee(tmp_path, sin_variables):  # R2
    store = DataStore(tmp_path / "data.sqlite3")
    equipo = montar_equipo(tmp_path / "data.sqlite3", store=store, token=tmp_path / "google-token.json")
    respuesta = _perfil(build_server(store, equipo=equipo), str(GPX))
    assert "TRIDATA_DOCS" in respuesta and "Perfil de la ruta" not in respuesta


# -- R3, R4: the compressed copy in the folder ------------------------------------------------------------------------------

def _store(tmp_path) -> DataStore:
    store = DataStore(tmp_path / "base-sintetica" / "data.sqlite3")
    store.save_daily_stats(DailyStats(stat_date=datetime(2026, 2, 1).date(), steps=10000))
    return store


def _backup(tmp_path, monkeypatch, argv: list[str], entorno: dict[str, str]) -> int:
    for variable in ("TRIDATA_DOCS", "TRIDATA_MEDICOS_DIR"):
        monkeypatch.delenv(variable, raising=False)
    for k, v in entorno.items():
        monkeypatch.setenv(k, v)
    store = _store(tmp_path)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "DataStore", lambda *a, **k: store)
    monkeypatch.setattr(cli, "DEFAULT_BACKUP_DIR", tmp_path / "local" / "backups")
    monkeypatch.setattr(cli, "PODA_MARCADOR", tmp_path / "local" / "ultima-poda-copia")
    return cli.main(["backup", *argv])


def test_la_copia_va_comprimida_a_la_carpeta(tmp_path, docs, monkeypatch, capsys):  # R3, R7
    diario = tmp_path / "diario-inventado.md"
    diario.write_text("diario inventado")
    assert _backup(tmp_path, monkeypatch, ["--extra", str(diario)], {"TRIDATA_DOCS": str(docs)}) == 0
    (copia,) = (docs / "tridata-backup").iterdir()
    assert re.fullmatch(r"tridata-\d{4}-\d{2}-\d{2}T\d{6}\.tar\.gz", copia.name)
    assert copia.stat().st_mode & 0o777 == 0o600
    with tarfile.open(copia, "r:gz") as tar:
        assert sorted(tar.getnames()) == ["data.sqlite3", "diario-inventado.md"]
        assert tar.extractfile("diario-inventado.md").read() == b"diario inventado"
        assert tar.extractfile("data.sqlite3").read(16) == b"SQLite format 3\x00"
    assert not (tmp_path / "local" / "backups").exists()  # nothing local
    salida = capsys.readouterr().out
    assert "Backup OK" in salida and str(docs) not in salida and str(tmp_path) not in salida  # R7: no path


def test_sin_tridata_docs_la_copia_es_la_de_siempre(tmp_path, monkeypatch):  # R3
    assert _backup(tmp_path, monkeypatch, [], {}) == 0
    (carpeta,) = (tmp_path / "local" / "backups").iterdir()
    assert (carpeta / "data.sqlite3").is_file()


def test_carpeta_ausente_copia_local_y_lo_dice(tmp_path, monkeypatch, capsys):  # R4
    ausente = tmp_path / "drive-sin-montar"
    assert _backup(tmp_path, monkeypatch, [], {"TRIDATA_DOCS": str(ausente)}) == 0
    assert not ausente.exists()  # the Drive folder is never created
    (carpeta,) = (tmp_path / "local" / "backups").iterdir()
    assert (carpeta / "data.sqlite3").is_file()
    salida = capsys.readouterr().out
    assert "TRIDATA_DOCS" in salida and str(ausente) not in salida


def test_dest_explicito_sigue_como_hoy(tmp_path, docs, monkeypatch):  # R3
    destino = tmp_path / "explicito"
    assert _backup(tmp_path, monkeypatch, ["--dest", str(destino)], {"TRIDATA_DOCS": str(docs)}) == 0
    (carpeta,) = destino.iterdir()
    assert (carpeta / "data.sqlite3").is_file()
    assert list((docs / "tridata-backup").iterdir()) == []


# -- R5: credentials never ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("nombre", ["env-antes-x", ".env", "copia.env.bak", "x/.garmin_claude_bridge/y",
                                    "strava-token.json", "google-token.json"])
def test_una_credencial_para_la_copia(tmp_path, docs, monkeypatch, capsys, nombre):  # R5
    extra = tmp_path / "extras" / nombre
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("secreto inventado")
    assert _backup(tmp_path, monkeypatch, ["--extra", str(extra)], {"TRIDATA_DOCS": str(docs)}) == 1
    assert list((docs / "tridata-backup").iterdir()) == []
    assert not (tmp_path / "local" / "backups").exists()
    assert "credencial" in capsys.readouterr().err.lower()


def test_una_credencial_tampoco_va_a_la_copia_local(tmp_path, monkeypatch):  # R5
    extra = tmp_path / ".env"
    extra.write_text("secreto inventado")
    assert _backup(tmp_path, monkeypatch, ["--extra", str(extra)], {}) == 1
    assert not (tmp_path / "local" / "backups").exists()


# -- R6: retention, at most one removal a day -------------------------------------------------------------------------------

def _copias(carpeta: Path) -> list[str]:
    return sorted(p.name for p in carpeta.iterdir() if p.name.startswith("tridata-"))


def test_poda_una_al_dia_y_solo_las_suyas(tmp_path, docs, monkeypatch):  # R6
    destino = docs / "tridata-backup"
    for dia in range(1, 17):
        (destino / f"tridata-2026-10-{dia:02d}T080000.tar.gz").write_bytes(b"copia inventada")
    otros = ["notas.txt", "tridata-a-mano.zip", "copia-vieja.tar.gz"]
    for nombre in otros:
        (destino / nombre).write_text("de Andrés")
    marcador = tmp_path / "local" / "ultima-poda-copia"
    marcador.parent.mkdir(parents=True)
    marcador.touch()
    ayer = time.time() - 86400 * 2
    os.utime(marcador, (ayer, ayer))

    assert _backup(tmp_path, monkeypatch, [], {"TRIDATA_DOCS": str(docs)}) == 0
    primera = _copias(destino)
    assert "tridata-2026-10-01T080000.tar.gz" not in primera  # the oldest one, only
    assert "tridata-2026-10-02T080000.tar.gz" in primera
    assert len([n for n in primera if n.endswith(".tar.gz")]) == 16  # 16 + 1 new - 1
    for nombre in otros:
        assert (destino / nombre).exists()

    time.sleep(1.1)  # a second copy the same day: new name, no removal
    assert _backup(tmp_path, monkeypatch, [], {"TRIDATA_DOCS": str(docs)}) == 0
    segunda = _copias(destino)
    assert "tridata-2026-10-02T080000.tar.gz" in segunda
    assert len([n for n in segunda if n.endswith(".tar.gz")]) == 17
    for nombre in otros:
        assert (destino / nombre).exists()


def test_con_14_o_menos_no_se_borra_nada(tmp_path, docs, monkeypatch):  # R6
    destino = docs / "tridata-backup"
    for dia in range(1, 14):
        (destino / f"tridata-2026-10-{dia:02d}T080000.tar.gz").write_bytes(b"copia inventada")
    assert _backup(tmp_path, monkeypatch, [], {"TRIDATA_DOCS": str(docs)}) == 0
    assert len(_copias(destino)) == 14
    assert not (tmp_path / "local" / "ultima-poda-copia").exists()


def test_el_marcador_de_poda_vive_fuera_del_drive():  # R6
    assert cli.PODA_MARCADOR.parent == cli.DEFAULT_BACKUP_DIR.parent  # ~/.tridata, never the synced folder


# -- R7: the routine ---------------------------------------------------------------------------------------------------------

def test_la_rutina_llama_a_backup_sin_destino():  # R7
    rutina = (RAIZ / "daily_update.sh").read_text(encoding="utf-8")
    assert '"$TRIDATA" backup "${BACKUP_EXTRAS[@]}"' in rutina
    assert "--dest" not in rutina
    for linea in ('echo "backup OK"', 'echo "backup: ya hecha hoy"', "se sigue con el sync"):
        assert linea in rutina


# -- R8: .env.example and .gitignore -----------------------------------------------------------------------------------------

_LEIDA = re.compile(r"""["']((?:TRIDATA|GARMIN)_[A-Z_]+)["']""")


def test_env_example_lista_todas_sin_valores():  # R8
    leidas = set()
    for fuente in (RAIZ / "src").rglob("*.py"):
        leidas |= set(_LEIDA.findall(fuente.read_text(encoding="utf-8")))
    leidas.discard("TRIDATA_MEDICOS_DIR")  # only named to say it was replaced
    assert "TRIDATA_DOCS" in leidas
    ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
    asignaciones = dict(re.findall(r"^([A-Z_]+)=(.*)$", ejemplo, flags=re.M))
    assert leidas <= set(asignaciones), leidas - set(asignaciones)
    assert all(valor == "" for valor in asignaciones.values()), asignaciones
    assert "TRIDATA_MEDICOS_DIR=" not in ejemplo


def test_gitignore_ignora_las_inscripciones():  # R8
    ignorados = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "inscripciones/" in ignorados and "inscriptions/" in ignorados


def test_ningun_fichero_lleva_la_ruta_de_la_carpeta():  # R8
    for nombre in ("README.md", "AGENTS.md", ".env.example"):
        texto = (RAIZ / nombre).read_text(encoding="utf-8")
        assert not re.search(r"TRIDATA_DOCS[ \t]*=[ \t]*\S", texto), nombre
    for nombre in ("README.md", ".env.example"):
        assert "/home/" not in (RAIZ / nombre).read_text(encoding="utf-8"), nombre
