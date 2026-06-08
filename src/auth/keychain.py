"""Credential-Storage via macOS-Keychain (``keyring``).

Passwörter werden ausschließlich hier abgelegt — nie in ``users.json`` oder im Klartext.
Service-Name ist konstant, der Account-Schlüssel ist die Apple-ID.
"""

from __future__ import annotations

from typing import Optional

import keyring

# Einheitliche Keychain-Services. Stabil halten — Änderungen "verlieren" gespeicherte Passwörter.
KEYCHAIN_SERVICE = "icloud-backup"            # reguläres Apple-ID-Passwort (Web-API: Drive/Photos)
KEYCHAIN_SERVICE_MAIL = "icloud-backup-mail"  # app-spezifisches Passwort (IMAP/Mail)


def set_password(apple_id: str, password: str) -> None:
    """Speichert das Apple-ID-Passwort eines Accounts im Keychain."""
    keyring.set_password(KEYCHAIN_SERVICE, apple_id, password)


def get_password(apple_id: str) -> Optional[str]:
    """Liest das Apple-ID-Passwort eines Accounts aus dem Keychain (oder ``None``)."""
    return keyring.get_password(KEYCHAIN_SERVICE, apple_id)


def delete_password(apple_id: str) -> None:
    """Entfernt das Apple-ID-Passwort eines Accounts aus dem Keychain (idempotent)."""
    _delete(KEYCHAIN_SERVICE, apple_id)


def set_mail_password(apple_id: str, app_password: str) -> None:
    """Speichert das app-spezifische Passwort (IMAP/Mail) eines Accounts im Keychain."""
    keyring.set_password(KEYCHAIN_SERVICE_MAIL, apple_id, app_password)


def get_mail_password(apple_id: str) -> Optional[str]:
    """Liest das app-spezifische Mail-Passwort eines Accounts aus dem Keychain (oder ``None``)."""
    return keyring.get_password(KEYCHAIN_SERVICE_MAIL, apple_id)


def delete_mail_password(apple_id: str) -> None:
    """Entfernt das app-spezifische Mail-Passwort eines Accounts (idempotent)."""
    _delete(KEYCHAIN_SERVICE_MAIL, apple_id)


def _delete(service: str, apple_id: str) -> None:
    try:
        keyring.delete_password(service, apple_id)
    except keyring.errors.PasswordDeleteError:
        # Kein Eintrag vorhanden -> nichts zu tun.
        pass
