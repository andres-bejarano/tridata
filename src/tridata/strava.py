"""Strava, only read (unit 102, REC-46 of `ver-fitness`, G-66): the shoes in use with their km.

- Permission: `read,profile:read_all` (with less, `GET /athlete` gives no `shoes`). Connecting opens Strava's page
  in the browser of this PC (`oauth/authorize`), back to a one-use local server on 127.0.0.1 that checks a random
  `state`; the `code` is exchanged at `oauth/token`. Strava never gets anything but that exchange, the renewal and
  `GET /athlete`: the app never writes in Strava.
- The client is a JSON (`client_id`, `client_secret`) where `TRIDATA_STRAVA_CLIENTE` (in `.env`) says; the token is
  saved next to the database (`~/.tridata/strava-token.json`), permissions 600, written atomically. The access token
  lasts 6 h and every renewal gives a NEW refresh token that voids the old one: it is saved at once (R7).
- The HTTP transport is injectable (`transporte(metodo, url, cabeceras, formulario) -> (estado, json)`, and
  `navegador(url)`): the tests never call Strava. Every failure becomes a typed `ErrorStrava` whose text is one of the
  fixed reasons below: never the text of an exception, which might carry a token. Nothing here logs a token, the
  secret or the `code`, and the local server never logs its requests.
- The base URL is in one constant (`BASE_API`): Strava announced it moves to `https://www.api-v3.strava.com`.
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BASE_API = "https://www.strava.com/api/v3"
AUTORIZAR = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"  # noqa: S105 - a URL, not a password
ALCANCE = "read,profile:read_all"
ESPERA_PERMISO_S = 300  # the browser waits this long for Andrés to say yes
ESPERA_HTTP_S = 20
MARGEN_S = 120  # an access token that ends within this is renewed first

SIN_CONECTAR = "sin conectar"
NO_RESPONDE = "Strava no responde"
PERMISO = "el permiso de Strava ya no vale: vuelve a conectar en Ajustes"
LIMITANDO = "Strava está limitando: se reintenta mañana"
SIN_PERFIL = ("el permiso de Strava no deja ver tus zapatillas: vuelve a conectar en Ajustes y deja marcado ver los "
              "datos de tu perfil")
NO_DIO_PERMISO = "no diste permiso en Strava"
SIN_RESPUESTA = "Strava no volvió a TriData a tiempo: prueba otra vez"
OTRO = "Strava respondió con un error"
FALTA_CLIENTE = ("Falta TRIDATA_STRAVA_CLIENTE en .env: la ruta del JSON con el client_id y el client_secret de tu "
                 "aplicación de Strava (Strava › Ajustes › Mi API) en este PC.")
CLIENTE_MAL = "El JSON de TRIDATA_STRAVA_CLIENTE no trae client_id y client_secret."
LISTO = "Listo: ya puedes cerrar esta pestaña y volver a TriData."
NO_LISTO = "No se ha conectado Strava: vuelve a TriData y prueba otra vez."

Transporte = Callable[[str, str, Mapping[str, str], Mapping[str, str] | None], tuple[int, Any]]


class ErrorStrava(Exception):
    """Strava could not be reached or refused: its text is one of the fixed reasons above, safe to show and log."""


class SinConectar(ErrorStrava):
    pass


class PermisoNoVale(ErrorStrava):
    pass


class Limitando(ErrorStrava):
    pass


class NoResponde(ErrorStrava):
    pass


def _http(metodo: str, url: str, cabeceras: Mapping[str, str], formulario: Mapping[str, str] | None) -> tuple[int, Any]:
    """The real transport: urllib, the form url-encoded; (status, JSON or None). A network failure raises OSError."""
    if not url.startswith("https://"):  # only Strava's fixed https URLs (never file: or another scheme)
        raise ValueError("only https")
    datos = urllib.parse.urlencode(dict(formulario)).encode() if formulario is not None else None
    todas = {"Accept": "application/json", **cabeceras}
    if datos is not None:
        todas["Content-Type"] = "application/x-www-form-urlencoded"
    peticion = urllib.request.Request(url, data=datos, headers=todas, method=metodo)  # noqa: S310 - https checked
    try:
        with urllib.request.urlopen(peticion, timeout=ESPERA_HTTP_S) as r:  # noqa: S310 - fixed https URLs
            cuerpo = r.read()
            estado = r.status
    except urllib.error.HTTPError as exc:
        return exc.code, None
    try:
        return estado, json.loads(cuerpo.decode("utf-8")) if cuerpo else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return estado, None


def escribir_600(ruta: Path, texto: str) -> None:
    """Atomic: a temporary file 600 in the same folder, then renamed over the old one (R7)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, temporal = tempfile.mkstemp(prefix=".strava-token-", dir=ruta.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporal, ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
    os.chmod(ruta, 0o600)


def _segun_estado(estado: int) -> ErrorStrava:
    if estado in (401, 403):
        return PermisoNoVale(PERMISO)
    if estado == 429:
        return Limitando(LIMITANDO)
    if estado >= 500:
        return NoResponde(NO_RESPONDE)
    return ErrorStrava(OTRO)


class _Vuelta(http.server.BaseHTTPRequestHandler):
    """The one-use local page Strava sends Andrés back to. Never logs a request (the URL carries the `code`)."""

    server: _Servidor

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        partes = urllib.parse.urlsplit(self.path)
        if partes.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        consulta = {k: v[0] for k, v in urllib.parse.parse_qs(partes.query).items()}
        bien = self.server.recibir(consulta)
        cuerpo = (f"<!doctype html><meta charset=utf-8><title>TriData</title><p>{LISTO if bien else NO_LISTO}</p>"
                  ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the signature of http.server
        return


class _Servidor(http.server.HTTPServer):
    def __init__(self, estado: str) -> None:
        super().__init__(("127.0.0.1", 0), _Vuelta)
        self.estado = estado
        self.respuesta: dict[str, str] | None = None
        self.llego = threading.Event()

    def recibir(self, consulta: dict[str, str]) -> bool:
        if not secrets.compare_digest(consulta.get("state", ""), self.estado):
            return False  # not ours: it keeps waiting for the right one
        self.respuesta = consulta
        self.llego.set()
        return "code" in consulta and "error" not in consulta


class Strava:
    """Andrés's Strava account, only read, and the token this PC keeps."""

    def __init__(self, token: Path | str, cliente: str | None, *, transporte: Transporte | None = None,
                 navegador: Callable[[str], Any] | None = None, reloj: Callable[[], float] = time.time,
                 espera_s: float = ESPERA_PERMISO_S) -> None:
        self._token = Path(token)
        self._cliente = cliente
        self._transporte = transporte or _http
        self._navegador = navegador or webbrowser.open
        self._reloj = reloj
        self._espera_s = espera_s
        self._cerrojo = threading.Lock()  # one renewal at a time in this process (the refresh token rotates)

    @property
    def conectado(self) -> bool:
        return self._token.is_file()

    @property
    def configurado(self) -> bool:
        return bool(self._cliente) and Path(str(self._cliente)).expanduser().is_file()

    def _datos_cliente(self) -> tuple[str, str]:
        if not self.configurado:
            raise ErrorStrava(FALTA_CLIENTE)
        try:
            datos = json.loads(Path(str(self._cliente)).expanduser().read_text(encoding="utf-8"))
            ident, secreto = str(datos["client_id"]).strip(), str(datos["client_secret"]).strip()
        except (OSError, ValueError, KeyError, TypeError):
            raise ErrorStrava(CLIENTE_MAL) from None
        if not ident or not secreto:
            raise ErrorStrava(CLIENTE_MAL)
        return ident, secreto

    def _llamar(self, metodo: str, url: str, cabeceras: Mapping[str, str],
                formulario: Mapping[str, str] | None = None) -> tuple[int, Any]:
        try:
            return self._transporte(metodo, url, cabeceras, formulario)
        except OSError:  # no network, a timeout, a refused connection: never its text
            raise NoResponde(NO_RESPONDE) from None

    def _guardar(self, respuesta: Any) -> dict[str, Any]:
        """Only the three fields needed (nothing of the athlete), saved at once."""
        try:
            token = {"access_token": str(respuesta["access_token"]), "refresh_token": str(respuesta["refresh_token"]),
                     "expires_at": int(respuesta["expires_at"])}
        except (KeyError, TypeError, ValueError):
            raise ErrorStrava(OTRO) from None
        escribir_600(self._token, json.dumps(token))
        return token

    # -- connecting ------------------------------------------------------------------------------------------------

    def conectar(self) -> None:
        """Strava's permission in the browser of this PC, back to 127.0.0.1; the token saved (R7, S-96)."""
        ident, secreto = self._datos_cliente()
        estado = secrets.token_urlsafe(24)
        servidor = _Servidor(estado)
        servidor.timeout = 0.5
        try:
            vuelta = f"http://127.0.0.1:{servidor.server_address[1]}/"
            url = AUTORIZAR + "?" + urllib.parse.urlencode({
                "client_id": ident, "redirect_uri": vuelta, "response_type": "code", "approval_prompt": "auto",
                "scope": ALCANCE, "state": estado})
            self._navegador(url)
            limite = time.monotonic() + self._espera_s
            while not servidor.llego.is_set() and time.monotonic() < limite:
                servidor.handle_request()
        finally:
            servidor.server_close()
        respuesta = servidor.respuesta
        if respuesta is None:
            raise ErrorStrava(SIN_RESPUESTA)
        if "error" in respuesta or "code" not in respuesta:
            raise ErrorStrava(NO_DIO_PERMISO)
        if "profile:read_all" not in respuesta.get("scope", "").split(","):
            raise PermisoNoVale(SIN_PERFIL)
        estado_http, cuerpo = self._llamar("POST", TOKEN_URL, {}, {
            "client_id": ident, "client_secret": secreto, "code": respuesta["code"],
            "grant_type": "authorization_code"})
        if estado_http != 200:
            raise PermisoNoVale(PERMISO) if estado_http in (400, 401, 403) else _segun_estado(estado_http)
        self._guardar(cuerpo)
        log.info("Strava connected")

    # -- reading ---------------------------------------------------------------------------------------------------

    def _acceso(self) -> str:
        """The access token, renewed first if it ends soon (the NEW refresh token saved at once)."""
        if not self.conectado:
            raise SinConectar(SIN_CONECTAR)
        with self._cerrojo:
            try:
                token = json.loads(self._token.read_text(encoding="utf-8"))
                acceso, refresco, caduca = str(token["access_token"]), str(token["refresh_token"]), \
                    int(token["expires_at"])
            except (OSError, ValueError, KeyError, TypeError):
                raise PermisoNoVale(PERMISO) from None
            if caduca - MARGEN_S > self._reloj():
                return acceso
            ident, secreto = self._datos_cliente()
            estado, cuerpo = self._llamar("POST", TOKEN_URL, {}, {
                "client_id": ident, "client_secret": secreto, "grant_type": "refresh_token",
                "refresh_token": refresco})
            if estado != 200:
                raise PermisoNoVale(PERMISO) if estado in (400, 401, 403) else _segun_estado(estado)
            nuevo = self._guardar(cuerpo)
            log.info("Strava token renewed")
            return str(nuevo["access_token"])

    def zapatillas(self) -> list[dict[str, Any]]:
        """`GET /athlete` → each shoe `{id, nombre, km (1 decimal), retirada}`, as Strava lists them (R1).

        `retirada` is Strava's `retired` when it comes (the reference does not show it in `/athlete`: then every shoe
        listed counts as in use). Without `shoes` the permission lacks `profile:read_all`."""
        acceso = self._acceso()
        estado, atleta = self._llamar("GET", f"{BASE_API}/athlete", {"Authorization": f"Bearer {acceso}"})
        if estado != 200:
            raise _segun_estado(estado)
        if not isinstance(atleta, dict) or not isinstance(atleta.get("shoes"), list):
            raise PermisoNoVale(SIN_PERFIL)
        leidas = []
        for z in atleta["shoes"]:
            if not isinstance(z, dict) or not z.get("id"):
                continue
            try:
                km = round(float(z.get("distance") or 0) / 1000, 1)
            except (TypeError, ValueError):
                continue
            nombre = str(z.get("name") or z.get("nickname") or z["id"]).strip()
            leidas.append({"id": str(z["id"]), "nombre": nombre, "km": km, "retirada": bool(z.get("retired", False))})
        log.info("Strava read: %d shoes", len(leidas))
        return leidas


__all__ = ["ALCANCE", "AUTORIZAR", "BASE_API", "FALTA_CLIENTE", "LIMITANDO", "NO_RESPONDE", "PERMISO", "SIN_CONECTAR",
           "SIN_PERFIL", "TOKEN_URL", "ErrorStrava", "Limitando", "NoResponde", "PermisoNoVale", "SinConectar",
           "Strava", "escribir_600"]
