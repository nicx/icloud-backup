"""iCloud Mail – dateibasierter IMAP-Sync-Spiegel.

Spiegelt das iCloud-Postfach (alle Ordner) nach ``<dest_base_path>/Mail/<Ordner>/<uid>.eml``
(rohes RFC822, inkl. Anhänge). **Kein sqlite** — das Dateisystem ist der Zustand.

Eigenschaften:

- **Echte Ordnerstruktur** wie in iCloud Mail (IMAP-modified-UTF-7-Namen werden dekodiert).
- **Ungelesen-schonend:** ``select(readonly=True)`` + ``BODY.PEEK[]`` ⇒ Mails werden nicht als
  gelesen markiert und keine Flags verändert.
- **Empfangsdatum:** beim Download wird ``INTERNALDATE`` (Server-Empfangszeit) mitgeholt und als
  mtime der ``.eml`` gesetzt ⇒ Finder-Datumsspalte/Sortierung zeigen die Empfangszeit, nicht
  die Download-Zeit.
- **Inkrementell:** nur UIDs ohne lokale ``<uid>.eml`` werden geladen.
- **Spiegel:** lokale Mails/Ordner, die es serverseitig nicht mehr gibt, werden entfernt — aber
  **nur** nach vollständigem, fehlerfreiem Listing aller Ordner (Guard ``complete``). Historie ⇒ Snapshots.
- **UIDVALIDITY:** je Ordner in ``.uidvalidity`` gemerkt; ändert sie sich (UIDs serverseitig
  neu vergeben), wird der Ordner lokal zurückgesetzt und neu geladen.

Auth: app-spezifisches Passwort (Apple-Pflicht; reguläres Passwort wird im IMAP abgelehnt).
"""

from __future__ import annotations

import base64
import imaplib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import util

LOGGER = logging.getLogger(__name__)

IMAP_HOST = "imap.mail.me.com"
IMAP_PORT = 993
_TIMEOUT = 60
_PROGRESS_EVERY = 100

# LIST-Antwortzeile: (flags) "sep" name
_LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+"(?P<sep>[^"]*)"\s+(?P<name>.+)$')


class MailAuthError(Exception):
    """IMAP-Login fehlgeschlagen (meist falsches/abgelaufenes app-spezifisches Passwort)."""


@dataclass
class MailStats:
    downloaded: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0
    folders: int = 0

    def summary(self) -> str:
        return (f"Mail: {self.downloaded} geladen, {self.skipped} vorhanden, "
                f"{self.deleted} entfernt, {self.errors} Fehler ({self.folders} Ordner)")


# --- IMAP modified UTF-7 (RFC 3501) ----------------------------------------

def imap_utf7_decode(name: str) -> str:
    """Dekodiert einen IMAP-Mailbox-Namen (modified UTF-7) nach Unicode."""
    out: list[str] = []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == "&":
            j = name.find("-", i + 1)
            if j == -1:
                j = len(name)
            chunk = name[i + 1:j]
            if chunk == "":
                out.append("&")  # "&-" -> "&"
            else:
                b64 = chunk.replace(",", "/")
                b64 += "=" * ((4 - len(b64) % 4) % 4)
                try:
                    out.append(base64.b64decode(b64).decode("utf-16-be"))
                except Exception:  # noqa: BLE001 - im Zweifel roh übernehmen
                    out.append(name[i:j + 1])
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# --- Hauptablauf -----------------------------------------------------------

def sync_mail(apple_id: str, app_password: str, dest_base_path: str, progress_cb=None) -> MailStats:
    """Spiegelt das iCloud-Postfach nach ``dest_base_path/Mail`` (IMAP, dateibasiert).

    :raises MailAuthError: wenn der IMAP-Login scheitert.
    """
    stats = MailStats()
    mail_root = Path(dest_base_path) / "Mail"
    expected: set = set()
    complete = True
    _emit(stats, progress_cb)

    imap = _connect_login(apple_id, app_password)
    try:
        typ, raw_lines = imap.list()
        if typ != "OK" or raw_lines is None:
            LOGGER.error("IMAP LIST fehlgeschlagen für %s: %s", apple_id, typ)
            return stats  # ohne Ordnerliste niemals löschen
        for line in raw_lines:
            folder = _parse_list_line(line)
            if folder is None:
                continue
            flags, sep, raw_name = folder
            if "\\noselect" in flags.lower():
                continue  # reiner Container ohne Mails
            stats.folders += 1
            ok = _sync_folder(imap, raw_name, sep, mail_root, stats, expected, progress_cb)
            if not ok:
                complete = False
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass

    # Spiegel: entfernte Mails UND ganze entfernte Ordner in einem Schritt – nur wenn vollständig.
    if complete:
        stats.deleted = util.prune_extra(mail_root, expected)
    else:
        LOGGER.warning("[%s] Mail-Listing unvollständig -> kein Löschen (nur Download).", apple_id)
    _emit(stats, progress_cb)
    LOGGER.info("[%s] %s", apple_id, stats.summary())
    return stats


def _connect_login(apple_id: str, app_password: str) -> imaplib.IMAP4_SSL:
    """Verbindet und meldet sich an; probiert bei Fehler den Lokalteil; sonst ``MailAuthError``."""
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=_TIMEOUT)
    except OSError as exc:
        raise MailAuthError(f"IMAP-Verbindung fehlgeschlagen: {exc}") from exc

    for username in _candidate_usernames(apple_id):
        try:
            imap.login(username, app_password)
            return imap
        except imaplib.IMAP4.error as exc:
            last = exc
    try:
        imap.logout()
    except Exception:  # noqa: BLE001
        pass
    raise MailAuthError(f"IMAP-Login abgelehnt (App-Passwort prüfen): {last}")


def _candidate_usernames(apple_id: str) -> list[str]:
    cands = [apple_id]
    if "@" in apple_id:
        local = apple_id.split("@", 1)[0]
        if local and local != apple_id:
            cands.append(local)
    return cands


def _parse_list_line(line) -> Optional[tuple[str, str, str]]:
    """Parst eine LIST-Zeile zu (flags, separator, roher Mailbox-Name)."""
    try:
        text = line.decode("ascii", "replace") if isinstance(line, (bytes, bytearray)) else str(line)
    except Exception:  # noqa: BLE001
        return None
    m = _LIST_RE.match(text.strip())
    if not m:
        return None
    name = m.group("name").strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return m.group("flags"), m.group("sep") or "/", name


def _folder_dir(mail_root: Path, raw_name: str, sep: str) -> Path:
    """Lokales Verzeichnis für einen Mailbox-Namen (dekodiert, sanitisiert, geschachtelt)."""
    decoded = imap_utf7_decode(raw_name)
    parts = decoded.split(sep) if sep else [decoded]
    safe = [util.safe_component(p) for p in parts if p != ""]
    d = mail_root
    for p in safe:
        d = d / p
    return d


def _sync_folder(imap, raw_name: str, sep: str, mail_root: Path, stats: MailStats,
                 expected: set, progress_cb) -> bool:
    """Spiegelt einen Ordner. Liefert True bei Erfolg (sonst kein Pruning für den Lauf)."""
    quoted = '"%s"' % raw_name
    folder_dir = _folder_dir(mail_root, raw_name, sep)

    try:
        typ, _ = util.with_retries(lambda: imap.select(quoted, readonly=True),
                                   label=f"mail.select:{raw_name}")
        if typ != "OK":
            raise imaplib.IMAP4.error(f"SELECT {typ}")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Ordner nicht wählbar %s: %s", raw_name, exc)
        stats.errors += 1
        return False

    # UIDVALIDITY prüfen / Ordner-Reset bei Wechsel
    uidv = _uidvalidity(imap, quoted)
    uidv_file = folder_dir / ".uidvalidity"
    expected.add(uidv_file)
    if uidv is not None:
        prev = _read_text(uidv_file)
        if prev != str(uidv):
            if prev is not None:
                LOGGER.info("UIDVALIDITY-Wechsel in %s -> Ordner-Resync", raw_name)
                _reset_folder(folder_dir)
            util.write_bytes(uidv_file, str(uidv).encode())

    # Server-UID-Liste
    try:
        typ, data = util.with_retries(lambda: imap.uid("SEARCH", None, "ALL"),
                                      label=f"mail.search:{raw_name}")
        if typ != "OK":
            raise imaplib.IMAP4.error(f"SEARCH {typ}")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("SEARCH fehlgeschlagen %s: %s", raw_name, exc)
        stats.errors += 1
        return False

    server_uids = data[0].split() if data and data[0] else []
    seen = 0
    for uid_b in server_uids:
        uid = uid_b.decode() if isinstance(uid_b, (bytes, bytearray)) else str(uid_b)
        # IMAP-UIDs sind laut RFC 3501 reine Ziffern. Alles andere (führt sonst über den
        # `{uid}.eml`-Pfad zu Path-Traversal, falls ein bösartiger/MITM-Server krude UIDs
        # liefert) wird defensiv übersprungen — Apple-Endpoint, aber Server-Input nie blind trauen.
        if not uid.isdigit():
            LOGGER.warning("Ungültige IMAP-UID in %s übersprungen: %r", raw_name, uid)
            continue
        dest = folder_dir / f"{uid}.eml"
        expected.add(dest)
        if dest.exists():
            stats.skipped += 1
            continue
        try:
            _fetch_one(imap, uid, dest)
            stats.downloaded += 1
        except Exception as exc:  # noqa: BLE001 - einzelne Mail darf den Lauf nicht kippen
            LOGGER.warning("FETCH fehlgeschlagen %s/%s: %s", raw_name, uid, exc)
            stats.errors += 1
        seen += 1
        if seen % _PROGRESS_EVERY == 0:
            _emit(stats, progress_cb)
    _emit(stats, progress_cb)
    return True


def _fetch_one(imap, uid: str, dest: Path) -> None:
    """Lädt eine Mail (BODY.PEEK[] = ohne \\Seen) und schreibt sie als ``.eml``.

    Holt zugleich ``INTERNALDATE`` (Server-Empfangszeit) mit und setzt sie als mtime der
    Datei — so trägt die Finder-Datumsspalte das echte Empfangsdatum statt der Download-Zeit.
    ``BODY.PEEK[]`` bleibt erhalten ⇒ die Mail wird weiterhin nicht als gelesen markiert.
    """
    def _do():
        # INTERNALDATE zuerst anfragen, damit sie VOR dem Body-Literal in der Antwort steht.
        # imaplib packt alles nach dem Literal in ein separates Listenelement; daher unten
        # robust ALLE Elemente nach INTERNALDATE absuchen (Position serverabhängig).
        typ, data = imap.uid("FETCH", uid, "(INTERNALDATE BODY.PEEK[])")
        if typ != "OK" or not data:
            raise imaplib.IMAP4.error(f"FETCH {typ}")
        raw = None
        meta_parts: list[bytes] = []
        for item in data:
            if isinstance(item, (tuple, list)):
                if item and item[0]:
                    meta_parts.append(_as_bytes(item[0]))
                if len(item) > 1 and item[1]:
                    raw = item[1]
            elif isinstance(item, (bytes, bytearray)):
                meta_parts.append(bytes(item))
        if not raw:
            raise imaplib.IMAP4.error("FETCH leeres Ergebnis")
        return raw, b" ".join(meta_parts)

    raw, meta = util.with_retries(_do, label=f"mail.fetch:{uid}")
    util.write_bytes(dest, raw)
    util.set_mtime(dest, _parse_internaldate(meta))  # no-op bei None


def _as_bytes(v) -> bytes:
    return v if isinstance(v, (bytes, bytearray)) else str(v).encode()


def _parse_internaldate(meta) -> Optional[datetime]:
    """Extrahiert ``INTERNALDATE`` aus der FETCH-Metazeile als ``datetime`` (oder ``None``).

    Nutzt die Stdlib (``imaplib.Internaldate2tuple`` liefert lokale ``time.struct_time``).
    Vollständig gekapselt — ein fehlendes/unparsebares Datum darf den Lauf nie kippen.
    """
    try:
        line = meta if isinstance(meta, (bytes, bytearray)) else str(meta).encode()
        tt = imaplib.Internaldate2tuple(line)
        if tt is None:
            return None
        return datetime.fromtimestamp(time.mktime(tt))
    except Exception:  # noqa: BLE001 - mtime ist best-effort
        return None


def _uidvalidity(imap, quoted: str) -> Optional[int]:
    try:
        typ, data = imap.status(quoted, "(UIDVALIDITY)")
        if typ != "OK" or not data:
            return None
        text = data[0].decode() if isinstance(data[0], (bytes, bytearray)) else str(data[0])
        m = re.search(r"UIDVALIDITY\s+(\d+)", text)
        return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def _reset_folder(folder_dir: Path) -> None:
    """Entfernt lokale ``.eml`` eines Ordners (bei UIDVALIDITY-Wechsel)."""
    if not folder_dir.is_dir():
        return
    for f in folder_dir.glob("*.eml"):
        try:
            f.unlink()
        except OSError:
            pass


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _emit(stats: MailStats, progress_cb) -> None:
    if progress_cb is not None:
        progress_cb({"downloaded": stats.downloaded, "skipped": stats.skipped,
                     "deleted": stats.deleted, "errors": stats.errors, "folders": stats.folders})
