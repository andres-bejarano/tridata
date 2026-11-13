"""Unit 032 through the web (`web/conversaciones.py`): since unit 080 (P-20261105-cf32bea5, S-5) the app no longer
mounts the Asistente's routes (they call the paid API), and the tests that only probed them through the app are gone
with them (`hallazgos.md` of the 080 lists them).

What stays is `Web`, the conversations over the invented world of `test_conversaciones_contexto.Mundo`, used by the
tests of the context of the team (`test_conversaciones_contexto.py`), the incidents (`test_incidencias.py`), the plan
(`test_plan_acciones.py`, `test_sesiones_del_dia.py`): it is the app of `crear_app` plus, in front of it and ONLY
here, the routes of `web.conversaciones.rutas` wired as `crear_app` wired them before unit 080, with Claude doubled
(`ClaudeFalso`: no API, no cost). The cleanup that removes the AI modules takes this away with them.
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from test_conversaciones import ClaudeFalso, PdfFalso
from test_conversaciones_contexto import Mundo
from tridata.conversaciones import Conversaciones
from tridata.conversaciones.contexto import montar
from tridata.conversaciones.medicos import Medicos, TranscriptorPDF
from tridata.ia.conversacion import ConversadorClaude
from tridata.nutricion import ObjetivosDia, Recetario
from tridata.nutricion.asistente import Contexto as ContextoAsistente
from tridata.plan import Planes
from tridata.web import crear_app
from tridata.web.conversaciones import rutas as rutas_conversaciones

AHORA = datetime(2026, 10, 24, 9, 42)


class Web(Mundo):
    """The app over the invented world of `test_conversaciones_contexto.Mundo`, with the conversations of before
    unit 080 in front of it (only in the tests) and Claude doubled."""

    def __init__(self, tmp_path, *, con_transcriptor: bool = True) -> None:
        super().__init__(tmp_path)
        self.claude = ClaudeFalso()
        self.pdf = PdfFalso()
        self.ahora = AHORA
        self.transcriptor = MagicMock(spec=["transcribir"])
        self.transcriptor.transcribir.return_value = "¿y el jueves qué hago?"
        hoy, ahora = (lambda: self.ahora.date()), (lambda: self.ahora)
        objetivos = ObjetivosDia(self.db)
        app = crear_app(self.registro, None, self.ajustes, hoy=hoy, pesos=self.pesos, objetivos=objetivos,
                        store=self.store, ahora=ahora)
        conversaciones = Conversaciones(self.db)
        medicos = Medicos(self.db, tmp_path / "medicos", TranscriptorPDF(cliente=self.pdf),
                          al_gastar=lambda modelo, uso: conversaciones.apuntar_gasto(ahora(), modelo, uso,
                                                                                     "transcripcion"))
        planes = Planes(self.db)
        asistente = ContextoAsistente(registro=self.registro, objetivos=objetivos, ajustes=self.ajustes,
                                      pesos=self.pesos, recetario=Recetario(self.db), hoy=hoy, store=self.store,
                                      planes=planes)

        def contexto():
            estado = medicos.al_dia()
            return montar(registro=self.registro, pesos=self.pesos, ajustes=self.ajustes, store=self.store,
                          transcripciones=medicos.transcripciones(), aviso_medico=estado["aviso"], hoy=hoy(),
                          planes=planes, calendario_conectado=False)

        chat = rutas_conversaciones(conversaciones, ConversadorClaude(cliente=self.claude), medicos, contexto,
                                    asistente, hoy, ahora, self.transcriptor if con_transcriptor else None, planes)
        self.asistente = asistente
        self.cliente = TestClient(Starlette(routes=[*chat, Mount("/", app=app)]))

    def sembrar(self) -> None:
        """Nothing more: the world of `Mundo` is already sown when it is built (kept for the readers)."""

    @staticmethod
    def eventos(r) -> list[dict]:
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(linea) for linea in r.text.splitlines() if linea.strip()]

    def _fin(self, r) -> dict:
        eventos = self.eventos(r)
        assert eventos[0]["tipo"] == "inicio"
        return {"eventos": eventos, **eventos[-1]}

    def nueva(self, texto: str, **extra) -> dict:
        return self._fin(self.cliente.post("/api/conversaciones", json={"texto": texto, **extra}))

    def enviar(self, ident: int, texto: str, **extra) -> dict:
        return self._fin(self.cliente.post(f"/api/conversaciones/{ident}/mensajes", json={"texto": texto, **extra}))

    def ver(self, ident: int) -> dict:
        r = self.cliente.get(f"/api/conversaciones/{ident}")
        assert r.status_code == 200, r.text
        return r.json()

    def lista(self) -> list[dict]:
        return self.cliente.get("/api/conversaciones").json()["conversaciones"]
