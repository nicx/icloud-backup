"""Config-Sicherung: Kopie von ``settings.json`` + ``users.json``.

Bewusst **ohne Geheimnisse**: Passwörter liegen im macOS-Keychain (nicht in diesen Dateien),
und die Trusted-Session-Tokens unter ``sessions/`` werden **nicht** mitgesichert — sie umgehen
2FA und sind ohnehin per Re-Auth regenerierbar. Eine importierte Konfiguration verlangt daher,
dass die Passwörter neu gesetzt werden.

Genutzt für die automatische Kopie ins Ziel-Volume (``<dest>/_config-backup/`` — die
UNAS-Snapshots versionieren sie) und für den manuellen Export/Import im Menü.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .paths import settings_file, users_file

LOGGER = logging.getLogger(__name__)

# Unterordner-Name für die automatische Kopie auf dem Ziel-Volume.
BACKUP_DIRNAME = "_config-backup"


def _config_files() -> list[Path]:
    """Die zu sichernden Config-Dateien, soweit vorhanden (keine Passwörter/Sessions)."""
    return [p for p in (settings_file(), users_file()) if p.exists()]


def backup_config_to(target_dir) -> int:
    """Kopiert vorhandene Config-Dateien nach ``target_dir`` (best-effort).

    :returns: Anzahl kopierter Dateien (0 bei Fehler/keine Dateien).
    """
    target = Path(target_dir)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        LOGGER.warning("Config-Backup-Ziel nicht anlegbar %s: %s", target, exc)
        return 0
    copied = 0
    for f in _config_files():
        try:
            shutil.copy2(f, target / f.name)
            copied += 1
        except OSError as exc:
            LOGGER.warning("Config-Datei nicht sicherbar %s: %s", f, exc)
    return copied


def restore_config_from(source_dir) -> int:
    """Kopiert ``settings.json``/``users.json`` aus ``source_dir`` zurück nach App Support.

    :returns: Anzahl wiederhergestellter Dateien (0, wenn nichts gefunden/kopierbar).
    """
    source = Path(source_dir)
    restored = 0
    for target in (settings_file(), users_file()):
        src = source / target.name
        if src.exists():
            try:
                shutil.copy2(src, target)
                restored += 1
            except OSError as exc:
                LOGGER.warning("Config-Datei nicht importierbar %s: %s", src, exc)
    return restored
