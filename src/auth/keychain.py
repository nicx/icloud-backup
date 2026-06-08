"""Credential-Storage via macOS-Keychain (``keyring``).

Passwörter werden ausschließlich hier abgelegt — nie in ``users.json`` oder im Klartext.
Service-Name ist konstant, der Account-Schlüssel ist die Apple-ID.
"""

from __future__ import annotations

from typing import Optional

import keyring

# Einheitlicher Keychain-Service. Stabil halten — Änderungen "verlieren" gespeicherte Passwörter.
KEYCHAIN_SERVICE = "icloud-backup"


def set_password(apple_id: str, password: str) -> None:
    """Speichert das Passwort eines Accounts im Keychain."""
    keyring.set_password(KEYCHAIN_SERVICE, apple_id, password)


def get_password(apple_id: str) -> Optional[str]:
    """Liest das Passwort eines Accounts aus dem Keychain (oder ``None``)."""
    return keyring.get_password(KEYCHAIN_SERVICE, apple_id)


def delete_password(apple_id: str) -> None:
    """Entfernt das Passwort eines Accounts aus dem Keychain (idempotent)."""
    try:
        keyring.delete_password(KEYCHAIN_SERVICE, apple_id)
    except keyring.errors.PasswordDeleteError:
        # Kein Eintrag vorhanden -> nichts zu tun.
        pass
