"""App-Support-Pfad-Helfer.

Alle persistenten Daten der App liegen unter
``~/Library/Application Support/icloud-backup/`` — dieser Ort überlebt App-Updates
(im Gegensatz zum Bundle-Inneren) und ist von ``auth`` und ``sync`` gemeinsam genutzt.

Passwörter liegen NICHT hier, sondern ausschließlich im macOS-Keychain (siehe
``auth.keychain``).
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "icloud-backup"


def app_support_dir() -> Path:
    """Basisverzeichnis der App in ``~/Library/Application Support``.

    Wird bei Bedarf angelegt.
    """
    base = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
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
