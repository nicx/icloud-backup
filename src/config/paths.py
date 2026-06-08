"""App-Support-Pfad-Helfer.

Alle persistenten Daten der App liegen unter
``~/Library/Application Support/icloud-sync/`` — dieser Ort überlebt App-Updates
(im Gegensatz zum Bundle-Inneren) und ist von ``auth`` und ``sync`` gemeinsam genutzt.

Passwörter liegen NICHT hier, sondern ausschließlich im macOS-Keychain (siehe
``auth.keychain``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

APP_DIR_NAME = "icloud-sync"
_LEGACY_DIR_NAME = "icloud-backup"  # vor der Umbenennung – wird einmalig migriert


def app_support_dir() -> Path:
    """Basisverzeichnis der App in ``~/Library/Application Support``.

    Migriert ein evtl. vorhandenes Alt-Verzeichnis (``icloud-backup``) einmalig auf den
    neuen Namen, damit bestehende Config/Sessions nach der Umbenennung erhalten bleiben.
    Wird bei Bedarf angelegt.
    """
    support = Path.home() / "Library" / "Application Support"
    base = support / APP_DIR_NAME
    legacy = support / _LEGACY_DIR_NAME
    if not base.exists() and legacy.is_dir():
        try:
            legacy.rename(base)
            LOGGER.info("App-Support migriert: %s -> %s", legacy, base)
        except OSError as exc:
            LOGGER.warning("Migration des App-Support-Verzeichnisses fehlgeschlagen: %s", exc)
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_file() -> Path:
    """Pfad zur globalen ``settings.json``."""
    return app_support_dir() / "settings.json"


def users_file() -> Path:
    """Pfad zur ``users.json`` (User-Liste ohne Passwörter)."""
    return app_support_dir() / "users.json"


def _safe_name(apple_id: str) -> str:
    """Macht eine Apple-ID dateisystemtauglich (für Unterverzeichnisse)."""
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in apple_id)


def session_dir(apple_id: str) -> Path:
    """Pro-User-Verzeichnis für pyicloud-Cookies (Trusted-Session).

    Wird als ``cookie_directory`` an ``PyiCloudService`` übergeben. Wird bei Bedarf angelegt.
    """
    d = app_support_dir() / "sessions" / _safe_name(apple_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    """Verzeichnis für Logdateien (wird bei Bedarf angelegt)."""
    d = app_support_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
