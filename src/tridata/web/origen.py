"""Bug 078: only the app itself reads and changes the data, not any other page open in the browser.

The app has no users: whoever reaches it gets in. A browser, though, carries to it the requests of any page the user
has open, so two things are looked at before a request touches anything:

- the `Host` it came by (every request, `/static` too): an IP, a name without dots (`localhost`, the machine's own
  name), a name of the tailnet (`*.ts.net`) or one of TRIDATA_WEB_HOSTS (comma separated). Any other name is a page
  that points its own name to this machine (DNS rebinding) to read through the browser;
- where a write comes from (whatever is not GET, HEAD or OPTIONS): the browser says it in `Sec-Fetch-Site` or, the
  ones that do not send it, in `Origin`, which must be the place the request came by (the `Host`, or the
  `X-Forwarded-Host` a proxy in front may put).

Without those two headers (curl, scripts, the tests) a write goes on as always: they are not a browser carrying
somebody else's request. A rejection is a 403 without data and without the value of any header received, and a line in
the log.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

SOLO_LEEN = frozenset({"GET", "HEAD", "OPTIONS"})
DEL_MISMO_SITIO = frozenset({"same-origin", "none"})  # `none`: the user did it (typed the address, a bookmark)
VARIABLE = "TRIDATA_WEB_HOSTS"
OTRO_NOMBRE = "Petición rechazada: TriData no atiende por ese nombre."
OTRA_WEB = "Petición rechazada: no viene de la propia app."

_SIN_PUNTO = re.compile(r"[a-z0-9_-]+")
_PUERTO = re.compile(r":\d*$")


def de_env() -> frozenset[str]:
    """The extra names of TRIDATA_WEB_HOSTS, without port, in lower case."""
    return frozenset(nombre for nombre in (_sin_puerto(trozo) for trozo in os.environ.get(VARIABLE, "").split(","))
                     if nombre)


def _sin_puerto(host: str) -> str:
    """`Maquina.tail0000.ts.net:443` → `maquina.tail0000.ts.net`; `[::1]:8765` → `[::1]`. The port does not count."""
    host = host.strip().lower()
    return host[:host.index("]") + 1] if host.startswith("[") and "]" in host else _PUERTO.sub("", host)


def host_permitido(host: str | None, extra: frozenset[str] = frozenset()) -> bool:
    """R2: is this `Host` header one of this machine's names?"""
    nombre = _sin_puerto(host or "")
    if not nombre:
        return False
    if nombre in extra or _SIN_PUNTO.fullmatch(nombre) or (nombre.endswith(".ts.net") and nombre != ".ts.net"):
        return True
    try:
        ipaddress.ip_address(nombre[1:-1] if nombre.startswith("[") and nombre.endswith("]") else nombre)
    except ValueError:
        return False
    return True


def _sitio(valor: str) -> str:
    """`https://Maquina.tail0000.ts.net/` → `maquina.tail0000.ts.net`; with its port if it has one, since another
    port of the same machine is another program."""
    return valor.strip().lower().partition("://")[2].partition("/")[0]


def viene_de_otra_web(sec_fetch_site: str | None, origin: str | None, llegadas: list[str]) -> bool:
    """R1: does the browser say this request comes from another site? `llegadas` are the names it came by: the `Host`
    and, behind a proxy, the `X-Forwarded-Host`."""
    if sec_fetch_site is not None:
        return sec_fetch_site.strip().lower() not in DEL_MISMO_SITIO
    if origin is None:
        return False
    sitio = _sitio(origin)  # `null` (a sandboxed frame, a file) is no site: another web
    return not sitio or sitio not in {llegada.strip().lower() for llegada in llegadas}


class Origen:
    """Pure ASGI, the first of the list in `crear_app`: what it rejects never reaches a route nor `Despertar`."""

    def __init__(self, app: ASGIApp, extra: frozenset[str] | None = None) -> None:
        self.app = app
        self.extra = de_env() if extra is None else extra

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cabeceras: dict[str, list[str]] = {}
        for clave, valor in scope["headers"]:
            cabeceras.setdefault(clave.decode("latin-1").lower(), []).append(valor.decode("latin-1"))
        hosts = cabeceras.get("host", [])
        if not hosts or not all(host_permitido(host, self.extra) for host in hosts):
            await self._rechazar(scope, receive, send, "host not allowed", OTRO_NOMBRE)
            return
        if scope["method"] not in SOLO_LEEN:
            llegadas = hosts + [trozo for valor in cabeceras.get("x-forwarded-host", []) for trozo in valor.split(",")]
            if viene_de_otra_web(_una(cabeceras, "sec-fetch-site"), _una(cabeceras, "origin"), llegadas):
                await self._rechazar(scope, receive, send, "write from another site", OTRA_WEB)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _rechazar(scope: Scope, receive: Receive, send: Send, motivo: str, mensaje: str) -> None:
        # R4: the method, the path and the reason; never the body nor what the headers said
        log.warning("Request rejected (%s): %s %r", motivo, scope["method"], scope["path"])
        await JSONResponse({"error": mensaje}, status_code=403)(scope, receive, send)


def _una(cabeceras: dict[str, list[str]], nombre: str) -> str | None:
    """A header sent twice with different values is not what a browser sends: the two, joined, match nothing."""
    valores = cabeceras.get(nombre)
    return ", ".join(valores) if valores else None


__all__ = ["Origen", "de_env", "host_permitido", "viene_de_otra_web"]
