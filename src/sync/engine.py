"""Sync-Orchestrierung pro User.

Eigenständig (auch ohne UI) aufrufbar – für Tests/Debug und vom Scheduler in ``app.py``.

Ablauf je User: Mount prüfen -> Passwort aus Keychain -> Login (ggf. Re-Auth nötig) ->
Drive- und/oder Photos-Sync gemäß User-Flags -> Status/last_run aktualisieren.
Ein Fehler bei einem User darf die anderen nicht stoppen.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Optional

from .. import notify
from ..auth import keychain, session
from ..config.users import User, UsersStore, UserStatus
from . import drive, photos

LOGGER = logging.getLogger(__name__)

# Vor einem großen Lauf warnen, wenn weniger als das frei ist (reine Warnung, kein Abbruch).
_LOW_SPACE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


def is_mount_available(dest_base_path: str) -> bool:
    """Prüft, ob der Ziel-Basispfad existiert/erreichbar ist (Fallstrick #5: UNAS-Mount fehlt)."""
    return bool(dest_base_path) and os.path.isdir(dest_base_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_free_space(dest_base_path: str) -> None:
    """Loggt eine Warnung bei wenig freiem Speicher (Fallstrick #6)."""
    try:
        usage = shutil.disk_usage(dest_base_path)
        if usage.free < _LOW_SPACE_BYTES:
            LOGGER.warning("Wenig freier Speicher auf %s: %.1f GiB",
                           dest_base_path, usage.free / 1024 ** 3)
    except OSError:
        pass


def run_user(user: User, store: Optional[UsersStore] = None) -> UserStatus:
    """Führt einen kompletten Sync-Lauf für *einen* User aus.

    Setzt Status auf ``RUNNING`` und am Ende ``OK``/``ERROR``/``NEEDS_REAUTH`` und
    aktualisiert ``last_run`` bei Erfolg.
    """
    def _set(status: UserStatus, last_run: Optional[str] = None) -> UserStatus:
        if store is not None:
            store.set_status(user.apple_id, status, last_run=last_run)
        return status

    _set(UserStatus.RUNNING)

    # 1) Ziel-Volume gemountet?
    if not is_mount_available(user.dest_base_path):
        LOGGER.warning("Ziel-Volume nicht verfügbar für %s: %s", user.apple_id, user.dest_base_path)
        notify.notify("iCloud Backup – Ziel fehlt",
                    f"{user.apple_id}: {user.dest_base_path} ist nicht gemountet.")
        return _set(UserStatus.ERROR)
    _check_free_space(user.dest_base_path)

    # 2) Credentials + Login
    password = keychain.get_password(user.apple_id)
    if not password:
        LOGGER.error("Kein Passwort im Keychain für %s", user.apple_id)
        return _set(UserStatus.ERROR)

    result = session.login(user.apple_id, password)
    if result.needs_2fa:
        notify.notify("iCloud Backup – Re-Auth nötig", f"{user.apple_id}: bitte erneut anmelden.")
        return _set(UserStatus.NEEDS_REAUTH)
    if result.error or result.api is None:
        LOGGER.error("Login-Fehler für %s: %s", user.apple_id, result.error)
        return _set(UserStatus.ERROR)

    # 3) Eigentlicher Sync (Drive + Photos je nach Flags)
    api = result.api
    had_error = False
    try:
        if user.sync_drive:
            stats = drive.sync_drive(api, user.dest_base_path, user.apple_id)
            had_error = had_error or stats.errors > 0
        if user.sync_photos:
            pstats = photos.sync_photos(api, user.dest_base_path, user.apple_id)
            had_error = had_error or pstats.errors > 0
    except Exception:  # noqa: BLE001 - harter, unerwarteter Fehler
        LOGGER.exception("Sync für %s abgebrochen", user.apple_id)
        return _set(UserStatus.ERROR)

    # Teilfehler (einzelne Dateien) -> trotzdem last_run setzen, aber Status ERROR signalisieren.
    status = UserStatus.ERROR if had_error else UserStatus.OK
    return _set(status, last_run=_now_iso())


def run_all(store: UsersStore) -> None:
    """Sequenziell alle aktiven User durchlaufen. Ein Fehler stoppt die anderen nicht."""
    for user in store.list():
        try:
            run_user(user, store)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Sync-Lauf für %s abgebrochen", user.apple_id)
            store.set_status(user.apple_id, UserStatus.ERROR)
