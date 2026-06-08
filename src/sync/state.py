"""sqlite-Manifest pro User (STUB).

Hält den Sync-Zustand persistent, damit Läufe inkrementell und resumebar sind. Pro User
eine eigene Datei (siehe ``config.paths.state_db``).

Geplantes Schema (Phase 2):

    -- iCloud-Drive-Dateien
    CREATE TABLE IF NOT EXISTS drive_files (
        path           TEXT PRIMARY KEY,   -- relativer Pfad im Drive-Tree
        size           INTEGER,
        date_modified  TEXT,               -- ISO-8601 (UTC), aus file.date_modified
        etag           TEXT,               -- falls verfügbar
        downloaded_at  TEXT
    );

    -- iCloud-Photos-Assets
    CREATE TABLE IF NOT EXISTS photo_assets (
        asset_id       TEXT PRIMARY KEY,   -- PhotoAsset.id
        filename       TEXT,
        created        TEXT,
        modified       TEXT,
        downloaded     INTEGER DEFAULT 0,  -- 0/1; getrennt für Foto + Live-Video möglich
        downloaded_at  TEXT
    );

    -- Lauf-Metadaten (Schlüssel/Wert), u. a. last_run
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config.paths import state_db


def connect(apple_id: str) -> sqlite3.Connection:
    """Öffnet (und initialisiert) die Manifest-DB eines Users.

    STUB: Verbindung wird geöffnet; ``init_schema`` folgt in Phase 2.
    """
    path: Path = state_db(apple_id)
    conn = sqlite3.connect(path)
    # init_schema(conn)  # Phase 2
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Legt die Tabellen an (idempotent). STUB — siehe Schema im Modul-Docstring."""
    raise NotImplementedError("Phase 2: Schema gemäß Modul-Docstring anlegen.")
