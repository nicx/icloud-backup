"""Login-Autostart über einen LaunchAgent (In-App-Toggle).

Schreibt/entfernt ein ``~/Library/LaunchAgents``-plist, das die App beim Login startet.
Bewusst ein LaunchAgent statt SMAppService: kommt ohne registrierten Helfer aus und
funktioniert auch für ein ad-hoc-signiertes Eigengebrauch-Bundle zuverlässig.
"""

from __future__ import annotations

import logging
import os
import plistlib
import subprocess
from pathlib import Path
from typing import Sequence

LOGGER = logging.getLogger(__name__)

LABEL = "de.nicx.icloud-backup"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def is_enabled() -> bool:
    return plist_path().exists()


def enable(program_args: Sequence[str]) -> None:
    """Aktiviert den Autostart mit den gegebenen Programm-Argumenten."""
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": list(program_args),
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    }
    with open(path, "wb") as fh:
        plistlib.dump(payload, fh)
    _launchctl("load", str(path))
    LOGGER.info("Autostart aktiviert: %s", program_args)


def disable() -> None:
    """Deaktiviert den Autostart (idempotent)."""
    path = plist_path()
    if path.exists():
        _launchctl("unload", str(path))
        try:
            path.unlink()
        except OSError as exc:
            LOGGER.warning("LaunchAgent-plist nicht löschbar: %s", exc)
    LOGGER.info("Autostart deaktiviert")


def _launchctl(action: str, path: str) -> None:
    """Best-effort ``launchctl load/unload`` (Fehler werden nur geloggt)."""
    try:
        subprocess.run(["launchctl", action, "-w", path],
                       check=False, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        LOGGER.debug("launchctl %s fehlgeschlagen: %s", action, exc)
