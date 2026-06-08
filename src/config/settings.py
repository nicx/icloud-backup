"""Globale App-Settings, als JSON in App Support persistiert.

Bewusst getrennt von der User-Liste (``users.py``): hier stehen nur prozessweite
Einstellungen wie das Sync-Intervall.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .paths import settings_file

# Default-Sync-Intervall. Nutzer-Entscheidung: konfigurierbar, nicht fix täglich.
DEFAULT_SYNC_INTERVAL_HOURS = 4


@dataclass
class Settings:
    """Prozessweite Einstellungen.

    :param sync_interval_hours: Mindestabstand zwischen zwei Sync-Läufen je User.
    :param autostart: Beim Login automatisch starten (Verdrahtung folgt späterer Durchgang).
    :param notifications: macOS-Notifications aktiviert.
    """

    sync_interval_hours: int = DEFAULT_SYNC_INTERVAL_HOURS
    autostart: bool = False
    notifications: bool = True


def load_settings() -> Settings:
    """Lädt die Settings; bei fehlender/kaputter Datei werden Defaults zurückgegeben.

    Unbekannte Felder in der JSON werden ignoriert, damit alte Dateien nach einem
    Schema-Zuwachs nicht brechen.
    """
    path = settings_file()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Settings()
    known = {f for f in Settings.__dataclass_fields__}
    filtered = {k: v for k, v in raw.items() if k in known}
    return Settings(**filtered)


def save_settings(settings: Settings) -> None:
    """Schreibt die Settings atomar-genug (write + replace) als JSON."""
    path = settings_file()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    tmp.replace(path)
