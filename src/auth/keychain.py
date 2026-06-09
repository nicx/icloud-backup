"""Credential-Storage via macOS-Keychain (``keyring``).

Passwörter werden ausschließlich hier abgelegt — nie in ``users.json`` oder im Klartext.
Service-Name ist konstant, der Account-Schlüssel ist die Apple-ID.

Hinweis Umbenennung: Die Services hießen früher ``icloud-backup`` / ``icloud-backup-mail``.
Beim Lesen wird auf die Alt-Services zurückgegriffen und der Eintrag transparent auf den
neuen Service migriert, damit nach der Umbenennung nichts neu eingegeben werden muss.
"""

from __future__ import annotations

from typing import Optional

import keyring

# Einheitliche Keychain-Services. Stabil halten — Änderungen "verlieren" gespeicherte Passwörter.
KEYCHAIN_SERVICE = "icloud-sync"            # reguläres Apple-ID-Passwort (Web-API: Drive/Photos)
KEYCHAIN_SERVICE_MAIL = "icloud-sync-mail"  # app-spezifisches Passwort (IMAP/Mail)

_LEGACY_SERVICE = "icloud-backup"
_LEGACY_SERVICE_MAIL = "icloud-backup-mail"


def set_password(apple_id: str, password: str) -> None:
    """Speichert das Apple-ID-Passwort eines Accounts im Keychain."""
    keyring.set_password(KEYCHAIN_SERVICE, apple_id, password)


def get_password(apple_id: str) -> Optional[str]:
    """Liest das Apple-ID-Passwort eines Accounts aus dem Keychain (oder ``None``)."""
    return _get_with_migration(KEYCHAIN_SERVICE, _LEGACY_SERVICE, apple_id)


def delete_password(apple_id: str) -> None:
    """Entfernt das Apple-ID-Passwort eines Accounts aus dem Keychain (idempotent)."""
    _delete(KEYCHAIN_SERVICE, apple_id)
    _delete(_LEGACY_SERVICE, apple_id)


def set_mail_password(apple_id: str, app_password: str) -> None:
    """Speichert das app-spezifische Passwort (IMAP/Mail) eines Accounts im Keychain."""
    keyring.set_password(KEYCHAIN_SERVICE_MAIL, apple_id, app_password)


def get_mail_password(apple_id: str) -> Optional[str]:
    """Liest das app-spezifische Mail-Passwort eines Accounts aus dem Keychain (oder ``None``)."""
    return _get_with_migration(KEYCHAIN_SERVICE_MAIL, _LEGACY_SERVICE_MAIL, apple_id)


def delete_mail_password(apple_id: str) -> None:
    """Entfernt das app-spezifische Mail-Passwort eines Accounts (idempotent)."""
    _delete(KEYCHAIN_SERVICE_MAIL, apple_id)
    _delete(_LEGACY_SERVICE_MAIL, apple_id)


def _get_with_migration(service: str, legacy: str, apple_id: str) -> Optional[str]:
    """Liest ``service``; fällt auf ``legacy`` zurück und migriert den Eintrag dann.

    Lesezugriffe sind abgesichert: Schlägt der Keychain-Zugriff fehl (z. B. weil der
    Alt-Eintrag eine ACL für eine frühere App-Signatur hat und der Zugriff verweigert
    wird), liefert die Funktion ``None`` statt zu werfen — so kippt ein Keychain-
    Problem nicht den ganzen Sync-Lauf, sondern führt nur zu „Passwort fehlt".
    """
    try:
        value = keyring.get_password(service, apple_id)
    except keyring.errors.KeyringError:
        value = None
    if value is not None:
        return value
    try:
        legacy_value = keyring.get_password(legacy, apple_id)
    except keyring.errors.KeyringError:
        return None
    if legacy_value is not None:
        try:
            keyring.set_password(service, apple_id, legacy_value)
            _delete(legacy, apple_id)
        except keyring.errors.KeyringError:
            pass
    return legacy_value


def _delete(service: str, apple_id: str) -> None:
    try:
        keyring.delete_password(service, apple_id)
    except keyring.errors.PasswordDeleteError:
        # Kein Eintrag vorhanden -> nichts zu tun.
        pass
