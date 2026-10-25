"""Google Calendar (unit 043, REC-27 of `mandar-sesion-reloj`): the calendar «TriData» where every planned session of
the plan goes as an all-day event (G-59).

- Permission: only `calendar.app.created` (the app creates «TriData» and touches nothing else of the agenda).
- Connecting is the installed-app flow of a Desktop client, redirected to 127.0.0.1: the browser of this PC opens
  Google's permission page. The client's JSON is where `TRIDATA_GOOGLE_CLIENTE` (in `.env`) says; the token is saved
  next to the database (`~/.tridata/google-token.json`) with permissions 600. Neither is ever logged or answered.
- The Google service is injectable (`servicio(token_path)`, `flujo(cliente) -> token JSON`): the tests never call
  Google. Every failure of Google becomes an `ErrorCalendario` with a fixed text that says why (R4): never the text of
  the library's exception, which might carry a token.
- The SQLite keeps the id of «TriData», the `eventId` of each session, the last good update and the last failure.

Without a token nothing here talks to Google (R6). `calendario/sincronizar.py` decides what to create, change or
delete.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

ALCANCE = "https://www.googleapis.com/auth/calendar.app.created"
NOMBRE = "TriData"
SIN_CONECTAR = "sin conectar"
PERMISO = "el permiso de Google caducó o se retiró: vuelve a conectar"
RED = "Google no responde (¿hay conexión?)"
OTRO = "Google respondió con un error"
FALTA_CLIENTE = ("Falta TRIDATA_GOOGLE_CLIENTE en .env: la ruta del JSON del cliente de Google (tipo Escritorio) "
                 "en este PC.")
LISTO = "Listo: ya puedes cerrar esta pestaña y volver a TriData."
ESPERA_PERMISO_S = 300  # the browser waits this long for Andrés to say yes

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calendario_estado (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS calendario_evento (sesion TEXT PRIMARY KEY, event_id TEXT NOT NULL);
"""


class ErrorCalendario(Exception):
    """Google could not be reached or refused: its text is one of the fixed reasons above, safe to show."""


def _escribir_600(ruta: Path, texto: str) -> None:
    """Writes the token readable only by this user (600), also when the file was already there."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(texto)
    os.chmod(ruta, 0o600)


def _flujo_google(cliente: str) -> str:
    """Google's permission page in the browser of this PC, back to 127.0.0.1; the token as JSON."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flujo = InstalledAppFlow.from_client_secrets_file(cliente, [ALCANCE])
    credenciales = flujo.run_local_server(host="127.0.0.1", port=0, open_browser=True,
                                          authorization_prompt_message="", success_message=LISTO,
                                          timeout_seconds=ESPERA_PERMISO_S)
    return str(credenciales.to_json())


def _servicio_google(token: Path) -> Any:
    """The Calendar v3 service with the saved token, refreshed (and saved again) when it has expired."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credenciales = Credentials.from_authorized_user_file(str(token), [ALCANCE])
    if not credenciales.valid and credenciales.refresh_token:
        credenciales.refresh(Request())
        _escribir_600(token, credenciales.to_json())
    return build("calendar", "v3", credentials=credenciales, cache_discovery=False)


def _estado_http(exc: BaseException) -> int | None:
    estado = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(estado) if estado is not None else None
    except (TypeError, ValueError):
        return None


def _motivo(exc: BaseException) -> str:
    """Why Google failed, in words; never the exception's own text."""
    nombre = type(exc).__name__
    if _estado_http(exc) in (401, 403) or nombre in ("RefreshError", "JSONDecodeError") or isinstance(exc, KeyError):
        return PERMISO
    if isinstance(exc, (OSError, TimeoutError)) or nombre in ("ServerNotFoundError", "HttpLib2Error",
                                                              "TransportError", "ConnectionError"):
        return RED
    return OTRO


class Calendario:
    """The calendar «TriData» of Andrés's Google account, and what this PC remembers of it."""

    def __init__(self, db_path: Path | str, token: Path | str, cliente: str | None, *,
                 flujo: Callable[[str], str] | None = None, servicio: Callable[[Path], Any] | None = None) -> None:
        self._db_path = Path(db_path)
        self._token = Path(token)
        self._cliente = cliente
        self._flujo = flujo or _flujo_google
        self._construir = servicio or _servicio_google
        self._servicio: Any = None
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- state ------------------------------------------------------------------------------------------------------

    @property
    def conectado(self) -> bool:
        return self._token.is_file()

    @property
    def configurado(self) -> bool:
        return bool(self._cliente) and Path(str(self._cliente)).expanduser().is_file()

    def _valor(self, clave: str) -> str | None:
        with self._connect() as conn:
            fila = conn.execute("SELECT valor FROM calendario_estado WHERE clave = ?", (clave,)).fetchone()
        return fila[0] if fila else None

    def _poner(self, clave: str, valor: str | None) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO calendario_estado (clave, valor) VALUES (?, ?) "
                         "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))

    def apuntar_bien(self, ahora: datetime) -> None:
        self._poner("ultima", ahora.isoformat(timespec="minutes"))
        self._poner("error", None)

    def apuntar_fallo(self, texto: str) -> None:
        self._poner("error", texto)

    def estado(self) -> dict[str, Any]:
        """What Ajustes › Google Calendar shows."""
        conectado = self.conectado
        return {"conectado": conectado, "configurado": self.configurado,
                "calendario": NOMBRE if conectado else None,
                "texto": f"Conectado · calendario {NOMBRE}" if conectado else SIN_CONECTAR,
                "ultima": self._valor("ultima") if conectado else None,
                "error": self._valor("error") if conectado else None}

    def recordar(self, eventos: dict[str, str]) -> None:
        """The `eventId` of each session, as it is in Google after a pass."""
        with self._connect() as conn:
            conn.execute("DELETE FROM calendario_evento")
            conn.executemany("INSERT INTO calendario_evento (sesion, event_id) VALUES (?, ?)", eventos.items())

    def eventos_guardados(self) -> dict[str, str]:
        with self._connect() as conn:
            return dict(conn.execute("SELECT sesion, event_id FROM calendario_evento").fetchall())

    # -- Google ---------------------------------------------------------------------------------------------------

    @contextmanager
    def _google(self) -> Iterator[None]:
        try:
            yield
        except ErrorCalendario:
            raise
        except Exception as exc:  # noqa: BLE001 - every failure of Google is said the same way (R4)
            self._servicio = None  # the next pass builds it again
            raise ErrorCalendario(_motivo(exc)) from None

    def _svc(self) -> Any:
        if not self.conectado:
            raise ErrorCalendario(SIN_CONECTAR)
        if self._servicio is None:
            self._servicio = self._construir(self._token)
        return self._servicio

    def conectar(self) -> str:
        """Google's permission (once), the token saved only on this PC, and «TriData» created or reused (R1)."""
        if not self.configurado:
            raise ErrorCalendario(FALTA_CLIENTE)
        with self._google():
            token = self._flujo(str(Path(str(self._cliente)).expanduser()))
        _escribir_600(self._token, token)
        self._servicio = None
        return self.asegurar()

    def asegurar(self) -> str:
        """The id of «TriData»: the one kept here if it is still in Google, else a new one (R1).

        `calendar.app.created` does not allow `calendarList.list` (only the calendars the app made, by their id), so
        «TriData» is found by the id this PC kept when it created it, never by its name.
        """
        guardado = self._valor("calendario_id")
        with self._google():
            servicio = self._svc()
            if guardado is not None:
                try:
                    servicio.calendars().get(calendarId=guardado).execute()
                    return guardado
                except Exception as exc:
                    if _estado_http(exc) not in (404, 410):  # Andrés deleted it: a new one is made
                        raise
            ident = str(servicio.calendars().insert(body={"summary": NOMBRE}).execute()["id"])
        self._poner("calendario_id", ident)
        return ident

    def _calendario_id(self) -> str:
        return self._valor("calendario_id") or self.asegurar()

    def eventos(self) -> list[dict]:
        """Every event of «TriData»."""
        cal = self._calendario_id()
        with self._google():
            servicio = self._svc()
            eventos: list[dict] = []
            pagina: str | None = None
            while True:
                respuesta = servicio.events().list(calendarId=cal, pageToken=pagina, showDeleted=False,
                                                   maxResults=2500).execute()
                eventos += respuesta.get("items", [])
                pagina = respuesta.get("nextPageToken")
                if not pagina:
                    return eventos

    def crear(self, cuerpo: dict) -> dict:
        cal = self._calendario_id()
        with self._google():
            return dict(self._svc().events().insert(calendarId=cal, body=cuerpo).execute())

    def cambiar(self, event_id: str, cambio: dict) -> dict:
        cal = self._calendario_id()
        with self._google():
            return dict(self._svc().events().patch(calendarId=cal, eventId=event_id, body=cambio).execute())

    def borrar(self, event_id: str) -> None:
        cal = self._calendario_id()
        with self._google():
            try:
                self._svc().events().delete(calendarId=cal, eventId=event_id).execute()
            except Exception as exc:
                if _estado_http(exc) not in (404, 410):  # already gone: what was wanted
                    raise


__all__ = ["ALCANCE", "NOMBRE", "SIN_CONECTAR", "Calendario", "ErrorCalendario"]
