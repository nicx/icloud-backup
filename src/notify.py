"""macOS-Notifications (Re-Auth, Fehler, Erfolg).

Primär über ``rumps.notification`` (funktioniert nur innerhalb einer laufenden rumps-App
mit gültiger Bundle-ID). Fällt das aus — z. B. beim Start außerhalb eines ``.app``-Bundles
während der Entwicklung — wird ``pync`` (terminal-notifier) als Fallback genutzt.
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)


def notify(title: str, message: str, subtitle: str | None = None) -> None:
    """Zeigt eine macOS-Notification; schluckt Fehler (Notifications sind best-effort)."""
    try:
        import rumps

        rumps.notification(title=title, subtitle=subtitle or "", message=message)
        return
    except Exception as exc:  # noqa: BLE001 - Fallback ist Absicht
        LOGGER.debug("rumps.notification fehlgeschlagen (%s), versuche pync", exc)

    try:
        import pync

        pync.notify(message, title=title, subtitle=subtitle)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Notification konnte nicht angezeigt werden: %s", exc)
