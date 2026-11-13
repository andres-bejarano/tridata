"""`Web`: the app of `crear_app` over the invented world of `test_conversaciones_contexto.Mundo`.

Since unit 080 the app mounts no route of the Asistente, and since unit 083 (P-20261113-33e057c3) the modules that
answered them (`web.conversaciones.rutas`, `ia/conversacion.py`) are gone, so `Web` is only the app (`cliente`) and
the context of the cards (`asistente`), as the tests of the plan, its incidents and its sessions use it
(`test_plan_acciones.py`, `test_sesiones_del_dia.py`, `test_incidencias.py`).

`claude` / `nueva` are a LEFTOVER kept only for `test_incidencias.py::test_ante_una_alarma_el_fisio_manda_al_medico_sin_
diagnosticar`, outside unit 083: no conversation exists; `nueva(texto, papel=…)` only records the request the role's
text (`conversaciones.papeles.bloque`) went in. Its removal is proposed in the `hallazgos.md` of the 083.
"""
from __future__ import annotations

from datetime import datetime

from starlette.testclient import TestClient

from test_conversaciones_contexto import Mundo
from tridata.conversaciones.papeles import POR_DEFECTO, bloque
from tridata.nutricion import ObjetivosDia, Recetario
from tridata.nutricion.asistente import Contexto as ContextoAsistente
from tridata.plan import Planes
from tridata.web import crear_app

AHORA = datetime(2026, 10, 24, 9, 42)


class _SoloPapel:
    """See the module: what `test_incidencias.py` reads of the old double (`peticiones[i]["system"][-1]["text"]`)."""

    def __init__(self) -> None:
        self.peticiones: list[dict] = []

    def responder(self, texto: str, *herramientas) -> None:
        """Nothing: there is no answer any more."""


class Web(Mundo):
    """The app over the invented world of `Mundo`, at `AHORA`."""

    def __init__(self, tmp_path) -> None:
        super().__init__(tmp_path)
        self.ahora = AHORA
        hoy, ahora = (lambda: self.ahora.date()), (lambda: self.ahora)
        objetivos = ObjetivosDia(self.db)
        app = crear_app(self.registro, None, self.ajustes, hoy=hoy, pesos=self.pesos, objetivos=objetivos,
                        store=self.store, ahora=ahora)
        self.asistente = ContextoAsistente(registro=self.registro, objetivos=objetivos, ajustes=self.ajustes,
                                           pesos=self.pesos, recetario=Recetario(self.db), hoy=hoy, store=self.store,
                                           planes=Planes(self.db))
        self.cliente = TestClient(app)
        self.claude = _SoloPapel()

    def nueva(self, texto: str, papel: str = POR_DEFECTO) -> dict:
        """See the module: the request a role's text went in, nothing else."""
        self.claude.peticiones.append({"system": [{"type": "text", "text": bloque(papel)}],
                                       "messages": [{"role": "user", "content": texto}]})
        return {}
