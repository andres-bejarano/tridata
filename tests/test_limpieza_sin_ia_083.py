"""Unit 083 (P-20261113-33e057c3): the cleanup after removing the paid AI.

- R1 (load-bearing): with `anthropic` and `faster_whisper` impossible to import, every module of `src/tridata` imports,
  `tridata web` builds its app with its four tabs and `tridata-mcp` starts with ALL the team's tools, without warning
  about a missing extra.
- R2: neither the code nor `pyproject.toml`, `.env.example`, `AGENTS.md` or `README.md` bring the paid AI back.
- R3: what the coach reads names the real path (`preparar_cambio` -> Andrés's «sí» -> `hacer_cambio`), never
  `proponer_cambio`, a card or the Asistente's tab; a GPX of the PC is not «subido».
- R4: a plan with phases imported by the coach is saved whole or not at all.
- R5: a base of before (conversations, messages with their model, spending, medical transcriptions) opens as it is.

Every datum is invented; nothing talks to Anthropic, Garmin or Google.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from test_herramientas_coach import AHORA, Mundo

from tridata.conversaciones.contexto import INSTRUCCIONES
from tridata.conversaciones.desde_pc import TITULO, montar_equipo, texto_contexto
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, RegistroComidas
from tridata.plan import Planes
from tridata.plan import rutas
from tridata.storage import DataStore
from tridata.web import crear_app

RAIZ = Path(__file__).resolve().parents[1]
SRC = RAIZ / "src"

# every tool of `tridata-mcp` with the team (the Garmin ones, 065/066, 081 and 082)
HERRAMIENTAS = {
    "list_activities", "get_activity", "get_daily_metrics", "get_training_load", "get_weekly_summary", "get_records",
    "get_contexto", "preparar_cambio", "hacer_cambio", "apuntar_comida", "apuntar_peso", "apuntar_toma",
    "apuntar_leche_cafes", "preparar_plan", "importar_plan", "get_analiticas_pendientes", "preparar_transcripcion",
    "guardar_transcripcion", "get_recetas", "get_rutas", "get_perfil_ruta",
}

# -- R1 (portante): nothing that stays needs the paid AI -------------------------------------------------------------

_SIN_IA = r"""
import asyncio, importlib, json, pkgutil, sys
sys.modules["anthropic"] = None        # import anthropic -> ImportError
sys.modules["faster_whisper"] = None   # import faster_whisper -> ImportError

import tridata
fallos = []
for m in pkgutil.walk_packages(tridata.__path__, "tridata.", onerror=lambda nombre: fallos.append(nombre)):
    try:
        importlib.import_module(m.name)
    except Exception as exc:
        fallos.append(f"{m.name}: {exc!r}")

from datetime import date
from pathlib import Path
from starlette.testclient import TestClient
from tridata.nutricion import DESDE_SIEMPRE, Ajustes, RegistroComidas
from tridata.web import crear_app

db = Path(sys.argv[1])
ajustes = Ajustes(db)
ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)
with TestClient(crear_app(RegistroComidas(db), None, ajustes, hoy=lambda: date(2026, 11, 13))) as cliente:
    pagina = cliente.get("/")
    dia = cliente.get("/api/dia")

from mcp.server.mcpserver import MCPServer
from tridata import mcp_server

herramientas = []
MCPServer.run = lambda self, *a, **k: herramientas.extend(t.name for t in asyncio.run(self.list_tools()))
mcp_server.load_dotenv = lambda *a, **k: None  # never a real .env
mcp_server.main()
print(json.dumps({"fallos": fallos, "pagina": pagina.status_code, "html": pagina.text, "dia": dia.status_code,
                  "herramientas": sorted(herramientas)}))
"""


def test_sin_anthropic_ni_whisper_todo_arranca(tmp_path):  # R1 (portante)
    entorno = {k: v for k, v in os.environ.items()
               if k not in ("ANTHROPIC_API_KEY", "TRIDATA_MEDICOS_DIR", "TRIDATA_GOOGLE_CLIENTE")}
    entorno |= {"HOME": str(tmp_path), "PYTHONPATH": os.pathsep.join([str(SRC), str(RAIZ / "tests")])}
    hecho = subprocess.run([sys.executable, "-c", _SIN_IA, str(tmp_path / "data.sqlite3")], cwd=tmp_path,  # noqa: S603
                           env=entorno, capture_output=True, text=True, timeout=180, check=False)
    assert hecho.returncode == 0, hecho.stderr[-3000:]
    salida = json.loads(hecho.stdout.strip().splitlines()[-1])
    assert salida["fallos"] == [], salida["fallos"]
    assert salida["pagina"] == 200 and salida["dia"] == 200
    for pestana in ("hoy", "nutricion", "fitness", "ajustes"):  # the four tabs
        assert f'data-ir="{pestana}"' in salida["html"], pestana
    assert set(salida["herramientas"]) == HERRAMIENTAS
    assert "extra" not in hecho.stderr.lower() and "off" not in hecho.stderr.lower(), hecho.stderr


# -- R2: it does not come back ---------------------------------------------------------------------------------------

_IMPORTA_IA = re.compile(r"^\s*(?:import|from)\s+(?:anthropic|faster_whisper|tridata\.ia|\.+ia)\b", re.MULTILINE)


def test_ningun_modulo_importa_la_ia_de_pago():  # R2
    assert not (SRC / "tridata" / "ia").exists()
    culpables = [str(p.relative_to(RAIZ)) for p in sorted(SRC.rglob("*.py"))
                 if _IMPORTA_IA.search(p.read_text(encoding="utf-8"))]
    assert culpables == []
    assert not (RAIZ / "scripts" / "probar_dictado.py").exists()
    assert not (RAIZ / "scripts" / "probar_interpretacion.py").exists()


def test_dependencias_y_configuracion_sin_ia():  # R2
    pyproject = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    for fuera in ("anthropic", "faster-whisper", "nvidia-", "voz ="):
        assert fuera not in pyproject, fuera
    ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in ejemplo and "TRIDATA_MODELO_" not in ejemplo
    for guia in ("AGENTS.md", "README.md"):
        texto = (RAIZ / guia).read_text(encoding="utf-8")
        for fuera in ("anthropic", "Anthropic", "faster-whisper", "Whisper", '".[voz]"', "`voz`", "probar_dictado",
                      "TRIDATA_MODELO_", "[mcp,app]", "proponer_cambio"):
            assert fuera not in texto, f"{guia}: {fuera}"
        assert '".[mcp]"' in texto, f"{guia}: how the MCP with the team is installed"


# -- R3: what the coach reads ----------------------------------------------------------------------------------------

_NO_EXISTE = ("proponer_cambio", "tarjeta", "Tarjeta", "Asistente", "pestaña", "poner_titulo")
_CARD = re.compile(r"\bcards?\b")  # the card, in English (not «cardiaca»)


def test_las_instrucciones_explican_el_camino_real():  # R3
    for fuera in _NO_EXISTE:
        assert fuera not in INSTRUCCIONES, fuera
    assert not _CARD.search(INSTRUCCIONES)
    cambios = INSTRUCCIONES[INSTRUCCIONES.index("preparar_cambio"):]
    assert "«sí»" in cambios and "hacer_cambio" in cambios
    assert cambios.index("«sí»") < cambios.index("hacer_cambio")  # first his «sí», then it is done
    assert "apuntar_" in INSTRUCCIONES


def test_ninguna_herramienta_del_mcp_habla_de_tarjetas(tmp_path):  # R3
    import asyncio

    from tridata.mcp_server import build_server

    store = DataStore(tmp_path / "data.sqlite3")
    equipo = montar_equipo(tmp_path / "data.sqlite3", store=store, token=tmp_path / "google-token.json")
    for t in asyncio.run(build_server(store, equipo=equipo).list_tools()):
        for fuera in _NO_EXISTE:
            assert fuera not in (t.description or ""), f"{t.name}: {fuera}"
        assert not _CARD.search(t.description or ""), t.name


def test_un_cambio_mal_formado_no_habla_de_tarjetas(tmp_path):  # R3: what a tool answers is read too
    from tridata.conversaciones.desde_pc import preparar_desde_pc

    equipo = montar_equipo(tmp_path / "data.sqlite3", store=None, token=tmp_path / "google-token.json")
    for entrada in ("no es un objeto", {"accion": "objetivo_pasos", "dia": "mañana"}):
        respuesta = preparar_desde_pc(equipo, entrada)
        assert "No se ha hecho nada" in respuesta or "No he entendido" in respuesta, respuesta
        for fuera in _NO_EXISTE:
            assert fuera not in respuesta, f"{entrada!r}: {fuera}"
        assert not _CARD.search(respuesta)


def test_un_gpx_del_pc_no_es_un_gpx_subido():  # R3
    assert "subido" not in rutas.FUENTES["gpx"] and "GPX" in rutas.FUENTES["gpx"]
    perfil = {"nombre": "Ruta inventada", "fuente": "gpx", "distancia_km": 20.0, "sube_m": 115, "baja_m": 115}
    assert rutas.resumen(perfil).startswith("Ruta inventada (GPX) · 20 km")


# -- R4: the whole plan or nothing -----------------------------------------------------------------------------------

PLAN_CON_FASES = {"nombre": "Plan inventado con fases", "apartados": [
    {"titulo": "Previsto", "parte": "previsto", "texto": "las semanas inventadas", "filas": [
        "semana|1|2026-11-02|2026-11-08|4:00|tirada||", "semana|2|2026-11-09|2026-11-15|4:30|series||",
        "fase|Base inventada|1|1", "fase|Específica inventada|2|2",
        "sesion|2026-11-04|Rodaje inventado|45' a 5:40|no|20:00"]}]}


def _fallo_al_guardar(db: Path, tabla: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(f"CREATE TRIGGER fallo_inyectado BEFORE INSERT ON {tabla} "  # noqa: S608 - a fixed test table
                     "BEGIN SELECT RAISE(ABORT, 'fallo inyectado'); END")
        conn.commit()
    finally:
        conn.close()


def _quitar_fallo(db: Path) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TRIGGER fallo_inyectado")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("tabla", ["plan_fase", "plan_sesion", "plan_semana"])
def test_importar_plan_con_fases_guarda_todo_o_nada(tmp_path, tabla):  # R4
    mundo = Mundo(tmp_path)
    huella = mundo.huella(mundo.llamar("preparar_plan", {"plan": PLAN_CON_FASES}))
    _fallo_al_guardar(mundo.db, tabla)
    respuesta = mundo.llamar("importar_plan", {"papel": "entrenador", "plan": PLAN_CON_FASES, "huella": huella})
    assert not respuesta.startswith("Hecho") and "No se ha hecho nada" in respuesta, respuesta
    assert "fallo inyectado" not in respuesta  # what the base said stays in the PC
    assert mundo.planes() == [] and mundo.mensajes_pc() == []  # no plan, not even one without its phases
    conn = sqlite3.connect(mundo.db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM plan_fase").fetchone() == (0,)
    finally:
        conn.close()

    _quitar_fallo(mundo.db)  # the retry, with the same «sí»: one plan with its phases
    assert mundo.llamar("importar_plan", {"papel": "entrenador", "plan": PLAN_CON_FASES,
                                          "huella": huella}).startswith("Hecho")
    (guardado,) = mundo.planes()
    leido = Planes(mundo.db).leer(guardado["id"])
    assert leido is not None
    assert [(f["nombre"], f["desde_n"], f["hasta_n"]) for f in leido["fases"]] == [
        ("Base inventada", 1, 1), ("Específica inventada", 2, 2)]
    assert [(s["titulo"], s.get("hora")) for s in leido["sesiones"]] == [("Rodaje inventado", "20:00")]


def test_planes_crear_con_fases_que_no_cuadran_no_guarda_nada(tmp_path):  # R4: one write, one check
    planes = Planes(tmp_path / "data.sqlite3")
    datos = {"nombre": "Plan inventado", "apartados": [], "semanas": [
        {"apartado": 0, "n": 1, "desde": "2026-11-02", "hasta": "2026-11-08", "previsto_s": 14400, "clave": "",
         "hecho_plan_s": None, "hecho_plan_km": None}]}
    with pytest.raises(ValueError, match="semana 3"):
        planes.crear(datos, AHORA, fases=[{"nombre": "Base inventada", "desde_n": 1, "hasta_n": 3}])
    assert planes.lista() == []
    ident = planes.crear(datos, AHORA, fases=[{"nombre": "Base inventada", "desde_n": 1, "hasta_n": 1}])
    leido = planes.leer(ident)
    assert leido is not None and [f["nombre"] for f in leido["fases"]] == ["Base inventada"]


# -- R5: what was saved before is seen as before ---------------------------------------------------------------------

_BASE_DE_ANTES = """
CREATE TABLE conversacion (id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT NOT NULL,
    titulo_auto INTEGER NOT NULL DEFAULT 1, creada TEXT NOT NULL, usada TEXT NOT NULL,
    papel TEXT NOT NULL DEFAULT 'asistente');
CREATE TABLE mensaje (id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion_id INTEGER NOT NULL REFERENCES conversacion(id) ON DELETE CASCADE,
    quien TEXT NOT NULL CHECK (quien IN ('andres', 'asistente')), texto TEXT NOT NULL, hora TEXT NOT NULL,
    contexto_hasta TEXT, sin_respuesta INTEGER NOT NULL DEFAULT 0, modelo TEXT, tokens_entrada INTEGER,
    tokens_salida INTEGER, tokens_cache_escritura INTEGER, tokens_cache_lectura INTEGER,
    tarjetas TEXT NOT NULL DEFAULT '[]', propuesto TEXT NOT NULL DEFAULT '[]', papel TEXT);
CREATE TABLE modelo_papel (papel TEXT PRIMARY KEY, modelo TEXT NOT NULL);
CREATE TABLE conversacion_ruta (conversacion_id INTEGER PRIMARY KEY REFERENCES conversacion(id) ON DELETE CASCADE,
    perfil TEXT NOT NULL, adjuntada TEXT NOT NULL);
CREATE TABLE gasto_asistente (id INTEGER PRIMARY KEY AUTOINCREMENT, hora TEXT NOT NULL, mes TEXT NOT NULL,
    que TEXT NOT NULL, modelo TEXT NOT NULL, tokens_entrada INTEGER NOT NULL, tokens_salida INTEGER NOT NULL,
    tokens_cache_escritura INTEGER NOT NULL, tokens_cache_lectura INTEGER NOT NULL, dolares REAL NOT NULL);
CREATE TABLE transcripcion_medica (fichero TEXT PRIMARY KEY, huella TEXT NOT NULL, tipo TEXT NOT NULL,
    titulo TEXT NOT NULL, fecha TEXT, valores TEXT NOT NULL, notas TEXT NOT NULL, leida TEXT NOT NULL);

INSERT INTO conversacion VALUES (1, 'Una charla inventada', 1, '2026-10-01T09:00:00', '2026-10-01T09:05:00',
    'asistente');
INSERT INTO conversacion VALUES (2, 'Desde el PC', 0, '2026-11-01T08:00:00', '2026-11-02T10:00:00', 'asistente');
INSERT INTO mensaje (conversacion_id, quien, texto, hora, modelo, tokens_entrada, tokens_salida,
    tokens_cache_escritura, tokens_cache_lectura, papel) VALUES
    (1, 'andres', '¿Qué tal voy?', '2026-10-01T09:00:00', NULL, NULL, NULL, NULL, NULL, NULL),
    (1, 'asistente', 'Vas bien, respuesta inventada.', '2026-10-01T09:01:00', 'claude-sonnet-5-5', 1000, 200, 0, 0,
     'entrenador'),
    (2, 'asistente', 'Hecho: cambio inventado uno.', '2026-11-01T08:00:00', NULL, NULL, NULL, NULL, NULL,
     'entrenador'),
    (2, 'asistente', 'Hecho: cambio inventado dos.', '2026-11-02T10:00:00', NULL, NULL, NULL, NULL, NULL, 'fisio');
INSERT INTO modelo_papel VALUES ('entrenador', 'claude-sonnet-5-5');
INSERT INTO conversacion_ruta VALUES (1, '{"nombre": "Ruta inventada"}', '2026-10-01T09:02:00');
INSERT INTO gasto_asistente (hora, mes, que, modelo, tokens_entrada, tokens_salida, tokens_cache_escritura,
    tokens_cache_lectura, dolares) VALUES
    ('2026-10-01T09:01:00', '2026-10', 'conversacion', 'claude-sonnet-5-5', 1000, 200, 0, 0, 0.004);
INSERT INTO transcripcion_medica VALUES ('analitica-inventada.pdf', 'h', 'analitica', 'Analítica inventada',
    '2026-09-01', '[{"prueba": "Ferritina inventada", "valor": "123,4", "unidad": "ng/mL", "rango": null,
    "fecha": null}]', '["Nota inventada."]', '2026-09-02T10:00:00');
"""
_TABLAS_DE_ANTES = ("conversacion", "mensaje", "modelo_papel", "conversacion_ruta", "gasto_asistente",
                    "transcripcion_medica")


def _filas(db: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(db)
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()  # noqa: S608 - fixed test tables
                for t in _TABLAS_DE_ANTES}
    finally:
        conn.close()


def test_una_base_de_antes_se_abre_y_se_ve_igual(tmp_path):  # R5
    db = tmp_path / "data.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(_BASE_DE_ANTES)
    conn.close()
    carpeta = tmp_path / "medicos"
    carpeta.mkdir()
    (carpeta / "analitica-inventada.pdf").write_bytes(b"%PDF-1.4 inventada")
    antes = _filas(db)
    ajustes = Ajustes(db)
    ajustes.cambiar({"basal_kcal": 1600, "kcal_por_paso": 0.04}, desde=DESDE_SIEMPRE)

    with TestClient(crear_app(RegistroComidas(db), None, ajustes, ahora=lambda: datetime(2026, 11, 13, 9))) as web:
        coach = web.get("/api/coach").json()["cambios"]
    assert [(c["texto"], c["papel"]) for c in coach] == [("Hecho: cambio inventado dos.", "fisio"),
                                                         ("Hecho: cambio inventado uno.", "entrenador")]
    assert coach[0]["hora"].startswith("2026-11-02T10:00")

    equipo = montar_equipo(db, store=None, medicos_dir=carpeta, token=tmp_path / "google-token.json")
    contexto = texto_contexto(equipo)
    assert "Ferritina inventada" in contexto and "123,4" in contexto  # the transcription of before, as it was
    assert TITULO == "Desde el PC"
    assert _filas(db) == antes  # nothing deleted, nothing changed
