"""Sync-Orchestrierung pro User (Phase-1-STUB).

Eigenständig (auch ohne UI) aufrufbar — für Tests/Debug und vom Scheduler in ``app.py``.

Phase 1: ``run_user`` führt die *Rahmenlogik* aus (Mount-Check, Status setzen, last_run),
ruft aber noch **nicht** die eigentlichen Downloads (``drive``/``photos`` sind Stubs).
Phase 2 hängt die echten Sync-Aufrufe an den markierten Stellen ein.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from ..config.users import User, UsersStore, UserStatus

LOGGER = logging.getLogger(__name__)


def is_mount_available(dest_base_path: str) -> bool:
    """Prüft, ob der Ziel-Basispfad existiert/erreichbar ist (Fallstrick #5: UNAS-Mount fehlt)."""
    return bool(dest_base_path) and os.path.isdir(dest_base_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_user(user: User, store: Optional[UsersStore] = None) -> UserStatus:
    """Führt einen Sync-Lauf für *einen* User aus (Phase-1-Stub).

    Setzt Status auf ``RUNNING`` -> ``OK``/``ERROR`` und aktualisiert ``last_run``.
    Vor jedem echten Sync wird der Mount geprüft. Die eigentlichen Drive-/Photos-Downloads
    sind in Phase 1 noch nicht verdrahtet.
    """
    if store is not None:
        store.set_status(user.apple_id, UserStatus.RUNNING)

    if not is_mount_available(user.dest_base_path):
        LOGGER.warning("Ziel-Volume nicht verfügbar für %s: %s", user.apple_id, user.dest_base_path)
        if store is not None:
            store.set_status(user.apple_id, UserStatus.ERROR)
        return UserStatus.ERROR

    # --- Phase 2: hier echten Sync einhängen -------------------------------
    # from . import drive, photos
    # from ..auth import keychain, session
    # password = keychain.get_password(user.apple_id)
    # result = session.login(user.apple_id, password)
    # if result.needs_2fa: -> NEEDS_REAUTH; if result.error: -> ERROR
    # if user.sync_drive:  drive.sync_drive(result.api, user.dest_base_path, user.apple_id)
    # if user.sync_photos: photos.sync_photos(result.api, user.dest_base_path, user.apple_id)
    LOGGER.info("[STUB] Sync für %s übersprungen (Engine in Phase 1 nicht aktiv).", user.apple_id)

    if store is not None:
        store.set_status(user.apple_id, UserStatus.OK, last_run=_now_iso())
    return UserStatus.OK


def run_all(store: UsersStore) -> None:
    """Sequenziell alle aktiven User durchlaufen (Phase-1-Stub).

    Ein Fehler bei einem User darf die anderen nicht stoppen.
    """
    for user in store.list():
        try:
            run_user(user, store)
        except Exception:  # noqa: BLE001 - ein User darf den Gesamtlauf nicht kippen
            LOGGER.exception("Sync-Lauf für %s abgebrochen", user.apple_id)
            store.set_status(user.apple_id, UserStatus.ERROR)
