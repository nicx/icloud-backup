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


def send_mail(host: str, port: int, sender: str, recipient: str, subject: str,
              body: str, timeout: float = 15.0) -> bool:
    """Liefert eine Mail per **einfachem SMTP** an ein lokales Relay ein (kein Auth/TLS).

    Gedacht für das MailRelay-Projekt (Default ``127.0.0.1:2525``), das selbst Upstream-Auth,
    STARTTLS und Retry/Backoff übernimmt. Best-effort: Fehler werden geloggt, nicht geworfen
    (eine nicht zustellbare Benachrichtigung darf den Sync nie beeinflussen).
    """
    try:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001 - Benachrichtigung ist best-effort
        LOGGER.warning("Fehler-E-Mail an %s über %s:%s fehlgeschlagen: %s",
                       recipient, host, port, exc)
        return False
