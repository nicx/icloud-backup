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
from . import drive, mail, photos
from .mail import MailAuthError

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


def run_user(user: User, store: Optional[UsersStore] = None, progress_cb=None) -> UserStatus:
    """Führt einen kompletten Sync-Lauf für *einen* User aus.

    Setzt Status auf ``RUNNING`` und am Ende ``OK``/``ERROR``/``NEEDS_REAUTH`` und
    aktualisiert ``last_run`` bei Erfolg.

    :param progress_cb: optionaler Callback ``cb(apple_id, phase, counts)`` für Live-Fortschritt.
    """
    def _phase_cb(phase: str):
        if progress_cb is None:
            return None
        return lambda counts: progress_cb(user.apple_id, phase, counts)
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

    had_error = False
    web_reauth = False

    # 2) Web-API (Drive/Photos) – nur wenn benötigt. Mail läuft davon unabhängig.
    if user.sync_drive or user.sync_photos:
        password = keychain.get_password(user.apple_id)
        if not password:
            LOGGER.error("Kein Apple-ID-Passwort im Keychain für %s", user.apple_id)
            had_error = True
        else:
            result = session.login(user.apple_id, password)
            if result.needs_2fa:
                web_reauth = True
                notify.notify("iCloud Backup – Re-Auth nötig",
                              f"{user.apple_id}: bitte erneut anmelden (Drive/Photos).")
            elif result.error or result.api is None:
                LOGGER.error("Login-Fehler für %s: %s", user.apple_id, result.error)
                had_error = True
            else:
                api = result.api
                try:
                    if user.sync_drive:
                        s = drive.sync_drive(api, user.dest_base_path, user.apple_id, _phase_cb("drive"))
                        had_error = had_error or s.errors > 0
                    if user.sync_photos:
                        ps = photos.sync_photos(api, user.dest_base_path, user.apple_id, _phase_cb("photos"))
                        had_error = had_error or ps.errors > 0
                except Exception:  # noqa: BLE001 - harter, unerwarteter Fehler
                    LOGGER.exception("Drive/Photos-Sync für %s abgebrochen", user.apple_id)
                    had_error = True

    # 3) Mail (IMAP) – eigene Credentials, unabhängig von der Web-Session.
    if user.sync_mail:
        app_pw = keychain.get_mail_password(user.apple_id)
        if not app_pw:
            LOGGER.error("Kein Mail-App-Passwort im Keychain für %s", user.apple_id)
            notify.notify("iCloud Backup – Mail-Passwort fehlt",
                          f"{user.apple_id}: app-spezifisches Passwort setzen.")
            had_error = True
        else:
            try:
                ms = mail.sync_mail(user.apple_id, app_pw, user.dest_base_path, _phase_cb("mail"))
                had_error = had_error or ms.errors > 0
            except MailAuthError as exc:
                LOGGER.error("Mail-Login fehlgeschlagen für %s: %s", user.apple_id, exc)
                notify.notify("iCloud Backup – Mail-Login",
                              f"{user.apple_id}: App-Passwort prüfen/neu erzeugen.")
                had_error = True
            except Exception:  # noqa: BLE001
                LOGGER.exception("Mail-Sync für %s abgebrochen", user.apple_id)
                had_error = True

    # Status-Priorität: harte Fehler > Re-Auth > OK. last_run am Ende setzen.
    if had_error:
        status = UserStatus.ERROR
    elif web_reauth:
        status = UserStatus.NEEDS_REAUTH
    else:
        status = UserStatus.OK
    return _set(status, last_run=_now_iso())


def run_all(store: UsersStore, progress_cb=None) -> None:
    """Sequenziell alle aktiven User durchlaufen. Ein Fehler stoppt die anderen nicht."""
    for user in store.list():
        try:
            run_user(user, store, progress_cb)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Sync-Lauf für %s abgebrochen", user.apple_id)
            store.set_status(user.apple_id, UserStatus.ERROR)
