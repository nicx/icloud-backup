"""sqlite-Manifest pro User – Sync-Zustand für inkrementelle, resumebare Läufe.

Pro User eine eigene Datei (siehe ``config.paths.state_db``). Das Manifest erlaubt,
bei jedem Lauf nur neue/geänderte Objekte zu laden und nach einem Abbruch dort
weiterzumachen, wo der letzte Lauf stehen geblieben ist.

Tabellen:

- ``drive_files``  – ein Eintrag je iCloud-Drive-Datei (Schlüssel: relativer Pfad).
- ``photo_assets`` – ein Eintrag je Photos-Asset (Schlüssel: ``asset_id``).
- ``meta``         – Schlüssel/Wert-Lauf-Metadaten (z. B. ``last_run``).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from ..config.paths import state_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drive_files (
    path           TEXT PRIMARY KEY,
    size           INTEGER,
    date_modified  TEXT,
    etag           TEXT,
    downloaded_at  TEXT
);
CREATE TABLE IF NOT EXISTS photo_assets (
    asset_id       TEXT PRIMARY KEY,
    filename       TEXT,
    created        TEXT,
    has_video      INTEGER DEFAULT 0,
    downloaded     INTEGER DEFAULT 0,
    downloaded_at  TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(apple_id: str) -> sqlite3.Connection:
    """Öffnet und initialisiert die Manifest-DB eines Users."""
    conn = sqlite3.connect(state_db(apple_id))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def connect_path(path) -> sqlite3.Connection:
    """Wie :func:`connect`, aber für einen expliziten Pfad (Tests)."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Legt die Tabellen an (idempotent)."""
    conn.executescript(_SCHEMA)
    conn.commit()


# -- meta --------------------------------------------------------------------

def meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else (dt if isinstance(dt, str) else None)


# -- drive -------------------------------------------------------------------

def drive_needs_download(
    conn: sqlite3.Connection,
    path: str,
    size: Optional[int],
    date_modified: Optional[datetime],
    etag: Optional[str],
) -> bool:
    """True, wenn die Datei neu ist oder sich Größe/Änderungsdatum/etag geändert haben."""
    row = conn.execute(
        "SELECT size, date_modified, etag FROM drive_files WHERE path = ?", (path,)
    ).fetchone()
    if row is None:
        return True
    if etag and row["etag"] and etag != row["etag"]:
        return True
    if row["size"] != size:
        return True
    return row["date_modified"] != _iso(date_modified)


def drive_record(
    conn: sqlite3.Connection,
    path: str,
    size: Optional[int],
    date_modified: Optional[datetime],
    etag: Optional[str],
) -> None:
    """Vermerkt eine erfolgreich geladene Drive-Datei im Manifest."""
    conn.execute(
        "INSERT INTO drive_files(path, size, date_modified, etag, downloaded_at) "
        "VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
        "date_modified=excluded.date_modified, etag=excluded.etag, "
        "downloaded_at=excluded.downloaded_at",
        (path, size, _iso(date_modified), etag, datetime.utcnow().isoformat()),
    )
    conn.commit()


# -- photos ------------------------------------------------------------------

def photo_is_downloaded(
    conn: sqlite3.Connection,
    asset_id: str,
    created: Optional[datetime],
    has_video: bool,
) -> bool:
    """True, wenn das Asset bereits vollständig geladen wurde (inkl. Live-Video, falls vorhanden).

    Ändert sich ``created`` (selten – z. B. Re-Import) oder kam nachträglich eine
    Live-Video-Komponente hinzu, wird neu geladen.
    """
    row = conn.execute(
        "SELECT created, has_video, downloaded FROM photo_assets WHERE asset_id = ?",
        (asset_id,),
    ).fetchone()
    if row is None or not row["downloaded"]:
        return False
    if bool(row["has_video"]) != has_video:
        return False
    if row["created"] != _iso(created):
        return False
    return True


def photo_record(
    conn: sqlite3.Connection,
    asset_id: str,
    filename: str,
    created: Optional[datetime],
    has_video: bool,
    downloaded: bool = True,
) -> None:
    """Vermerkt ein Asset im Manifest (``downloaded=True`` erst nach allen Komponenten)."""
    conn.execute(
        "INSERT INTO photo_assets(asset_id, filename, created, has_video, downloaded, downloaded_at) "
        "VALUES(?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET filename=excluded.filename, "
        "created=excluded.created, has_video=excluded.has_video, "
        "downloaded=excluded.downloaded, downloaded_at=excluded.downloaded_at",
        (
            asset_id,
            filename,
            _iso(created),
            int(has_video),
            int(downloaded),
            datetime.utcnow().isoformat() if downloaded else None,
        ),
    )
    conn.commit()


def counts(conn: sqlite3.Connection) -> dict:
    """Kleine Statistik fürs Logging/Status."""
    df = conn.execute("SELECT COUNT(*) AS n FROM drive_files").fetchone()["n"]
    pa = conn.execute(
        "SELECT COUNT(*) AS n FROM photo_assets WHERE downloaded = 1"
    ).fetchone()["n"]
    return {"drive_files": df, "photos_downloaded": pa}
