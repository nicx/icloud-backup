"""pyicloud-Session-, 2FA- und Re-Auth-Handling.

**Dies ist die einzige Stelle, die ``pyicloud`` importiert.** Damit ein API-Bruch des
inoffiziellen Web-API-Wrappers (Fallstrick #7) nur hier gefixt werden muss, kapselt dieses
Modul den kompletten Auth-Pfad und gibt nach außen nur eigene, stabile Typen zurück
(:class:`LoginResult`, :class:`~src.config.users.UserStatus`).

Auth-Flow (siehe pyicloud-README):

1. ``api = PyiCloudService(apple_id, password, cookie_directory=...)`` — authentifiziert sofort,
   nutzt vorhandene Trusted-Session-Cookies aus ``cookie_directory``.
2. ``api.requires_2fa`` / ``api.requires_2sa`` -> 2FA/2SA nötig.
3. ``api.validate_2fa_code(code)`` und danach ``api.trust_session()`` (falls
   ``not api.is_trusted_session``) verlängert das Trust-Fenster und persistiert die Cookies.

Sessions laufen periodisch ab (~2 Monate); das wird über ``requires_2fa``/``requires_2sa``
bzw. einen Login-Fehler erkannt und nach außen als ``UserStatus.NEEDS_REAUTH`` gemeldet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloud2FARequiredException,
    PyiCloud2SARequiredException,
    PyiCloudException,
    PyiCloudFailedLoginException,
)

from ..config.paths import session_dir
from ..config.users import UserStatus

LOGGER = logging.getLogger(__name__)


@dataclass
class LoginResult:
    """Ergebnis eines Login-Versuchs.

    :param api: die ``PyiCloudService``-Instanz bei erfolgreicher Verbindung (sonst ``None``).
    :param needs_2fa: True, wenn ein 2FA/2SA-Code eingegeben werden muss.
    :param error: menschenlesbare Fehlermeldung bei hartem Fehler (z. B. falsches Passwort).
    """

    api: Optional["PyiCloudService"] = None
    needs_2fa: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True, wenn eine voll nutzbare, vertrauenswürdige Session besteht."""
        return self.api is not None and not self.needs_2fa and self.error is None


def build_service(apple_id: str, password: str) -> PyiCloudService:
    """Erzeugt eine ``PyiCloudService``-Instanz mit Pro-User-Cookie-Verzeichnis.

    ``accept_terms=True`` fängt den bekannten „neue Nutzungsbedingungen"-Login-Fehler ab.
    Kann ``PyiCloud*Exception`` werfen (z. B. falsches Passwort) — Aufrufer fangen das.
    """
    return PyiCloudService(
        apple_id,
        password,
        cookie_directory=str(session_dir(apple_id)),
        accept_terms=True,
    )


def login(apple_id: str, password: str) -> LoginResult:
    """Versucht einen Login und meldet, ob 2FA nötig ist oder ein Fehler auftrat.

    Wirft nicht — alle pyicloud-Fehler werden auf :class:`LoginResult` abgebildet.
    """
    try:
        api = build_service(apple_id, password)
    except PyiCloudFailedLoginException as exc:
        LOGGER.warning("Login fehlgeschlagen für %s: %s", apple_id, exc)
        return LoginResult(error="Login fehlgeschlagen — Apple-ID/Passwort prüfen.")
    except (PyiCloud2FARequiredException, PyiCloud2SARequiredException):
        # Manche Versionen werfen beim Konstruktor statt nur das Flag zu setzen.
        # Service nochmal ohne harten Fehler aufbauen wäre fragil — hier reicht die Meldung,
        # der Re-Auth-Flow baut die Session neu auf und fragt den Code ab.
        return LoginResult(needs_2fa=True)
    except PyiCloudException as exc:
        LOGGER.error("Auth-Fehler für %s: %s", apple_id, exc)
        return LoginResult(error=f"Verbindungsfehler: {exc}")

    if api.requires_2fa or api.requires_2sa:
        return LoginResult(api=api, needs_2fa=True)
    return LoginResult(api=api)


def submit_2fa_code(api: PyiCloudService, code: str) -> bool:
    """Validiert einen 2FA-Code und vertraut anschließend der Session.

    :return: True, wenn der Code akzeptiert wurde.
    """
    try:
        ok = api.validate_2fa_code(code)
    except PyiCloudException as exc:
        LOGGER.error("2FA-Validierung fehlgeschlagen: %s", exc)
        return False
    if not ok:
        return False
    # Trust verlängert das Fenster und persistiert die Cookies -> künftige Starts ohne 2FA.
    try:
        if not api.is_trusted_session:
            api.trust_session()
    except PyiCloudException as exc:
        # Trust optional; Session ist trotzdem für diesen Lauf nutzbar.
        LOGGER.warning("trust_session() fehlgeschlagen: %s", exc)
    return True


def check_session(apple_id: str, password: Optional[str]) -> UserStatus:
    """Prüft anhand gespeicherter Cookies + Keychain-Passwort, ob die Session noch gültig ist.

    Wird vom Scheduler/Status-Refresh genutzt, um ``NEEDS_REAUTH`` früh zu erkennen,
    ohne einen vollen Sync zu starten.

    :return: ``OK`` bei gültiger Session, ``NEEDS_REAUTH`` bei abgelaufener 2FA/2SA,
             ``ERROR`` bei sonstigen Problemen (auch fehlendes Passwort).
    """
    if not password:
        return UserStatus.ERROR
    try:
        api = build_service(apple_id, password)
    except PyiCloudFailedLoginException:
        return UserStatus.NEEDS_REAUTH
    except (PyiCloud2FARequiredException, PyiCloud2SARequiredException):
        return UserStatus.NEEDS_REAUTH
    except PyiCloudException as exc:
        LOGGER.error("Session-Check-Fehler für %s: %s", apple_id, exc)
        return UserStatus.ERROR

    if api.requires_2fa or api.requires_2sa:
        return UserStatus.NEEDS_REAUTH
    return UserStatus.OK
