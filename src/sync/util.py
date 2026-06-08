"""Geteilte Helfer für die Sync-Module: Pfad-Hygiene, Retry/Backoff, Streaming-Download.

Bewusst ohne pyicloud-Import — diese Funktionen arbeiten nur mit bereits übergebenen
Objekten (requests-Response, bytes) und generischen Exceptions, damit der pyicloud-Zugriff
auf ``auth.session`` (und die Aufrufer in ``drive``/``photos``) beschränkt bleibt.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, TypeVar

LOGGER = logging.getLogger(__name__)

T = TypeVar("T")

# Standard-Chunkgröße fürs Streaming (1 MiB) — speicherschonend auch bei großen Videos.
CHUNK_SIZE = 1 << 20

# HTTP-Statuscodes, bei denen wir mit Backoff erneut versuchen (Throttling/transient).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def safe_component(name: str) -> str:
    """Macht einen einzelnen Pfadbestandteil dateisystemtauglich.

    Entfernt Separatoren und problematische Zeichen, ohne den Namen unkenntlich zu machen.
    """
    cleaned = name.replace(os.sep, "_").replace("/", "_").replace("\0", "")
    cleaned = cleaned.strip().strip(".") or "_"
    return cleaned


def _status_of(exc: BaseException) -> Optional[int]:
    """Versucht, aus einer Exception einen HTTP-Statuscode zu lesen (pyicloud/requests)."""
    for attr in ("code", "status_code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    resp = getattr(exc, "response", None)
    if resp is not None:
        return getattr(resp, "status_code", None)
    return None


def is_retryable(exc: BaseException) -> bool:
    """True, wenn die Exception auf ein transientes/Throttling-Problem hindeutet."""
    status = _status_of(exc)
    if status in _RETRYABLE_STATUS:
        return True
    text = str(exc).lower()
    return any(s in text for s in ("throttl", "rate limit", "timeout", "temporarily"))


def with_retries(
    func: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    label: str = "operation",
) -> T:
    """Führt ``func`` aus und wiederholt bei retrybaren Fehlern mit exponentiellem Backoff.

    Nicht-retrybare Exceptions werden sofort weitergereicht. ``sleep`` ist injizierbar
    (für Tests). Apple nicht hämmern — daher konservative Defaults.
    """
    last: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - bewusst breit, Klassifizierung via is_retryable
            last = exc
            if attempt == attempts or not is_retryable(exc):
                raise
            delay = base_delay * (2 ** (attempt - 1))
            LOGGER.warning("%s fehlgeschlagen (Versuch %d/%d): %s — warte %.1fs",
                           label, attempt, attempts, exc, delay)
            sleep(delay)
    # Unerreichbar, aber für den Typchecker:
    assert last is not None
    raise last


def _atomic_write(dest: Path, write_body: Callable[[object], None]) -> None:
    """Schreibt nach ``dest`` resumebar: erst ``.part``, dann atomarer Rename.

    Ein Abbruch hinterlässt höchstens eine ``.part``-Datei, nie eine halbe Zieldatei —
    der nächste Lauf lädt sauber neu.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with open(part, "wb") as fh:
            write_body(fh)
        os.replace(part, dest)
    except BaseException:
        # Teil-Datei aufräumen, damit kein Müll zurückbleibt.
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def stream_to_file(response, dest: Path, chunk_size: int = CHUNK_SIZE) -> int:
    """Streamt eine requests-Response in eine Datei (resumebar) und liefert die Byte-Anzahl.

    ``response`` muss ``iter_content`` (echte requests-Response) oder zumindest ein
    ``raw.read``-fähiges Objekt bereitstellen.
    """
    written = 0

    def body(fh) -> None:
        nonlocal written
        if hasattr(response, "iter_content"):
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    fh.write(chunk)
                    written += len(chunk)
        else:  # Fallback: rohes Read-Objekt (z. B. BytesIO bei 0-Byte-Dateien)
            raw = getattr(response, "raw", response)
            while True:
                chunk = raw.read(chunk_size)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)

    _atomic_write(dest, body)
    return written


def write_empty_file(dest: Path) -> None:
    """Legt eine leere Datei an (für 0-Byte-iCloud-Dateien, die kein Download erlauben)."""
    _atomic_write(dest, lambda fh: None)


def set_mtime(dest: Path, when: Optional[datetime]) -> None:
    """Setzt die Änderungszeit der Datei auf ``when`` (best-effort, Fehler werden ignoriert)."""
    if when is None:
        return
    try:
        ts = when.timestamp()
        os.utime(dest, (ts, ts))
    except (OSError, ValueError, OverflowError):
        pass


def iter_dir_components(parts: Iterable[str]) -> str:
    """Verbindet bereits bereinigte Komponenten zu einem relativen Pfad."""
    return os.path.join(*[safe_component(p) for p in parts]) if parts else ""
