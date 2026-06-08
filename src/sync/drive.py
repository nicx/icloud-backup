"""iCloud Drive – inkrementeller, resumebarer Download.

Läuft den Drive-Tree rekursiv ab und lädt jede Datei nach
``<dest_base_path>/Drive/<relativer-pfad>``. Ein Manifest (``state.drive_files``) hält
Größe/Änderungsdatum/etag, sodass nur neue oder geänderte Dateien geladen werden.

Eigenschaften (Spec):

- **Additiv:** in iCloud gelöschte Dateien werden im Backup NICHT gelöscht.
- **Resumebar:** Download nach ``.part`` + atomarer Rename; Manifest erst nach Erfolg.
- **Robust:** Fehler einzelner Dateien brechen den Lauf nicht ab; Throttling -> Backoff.

pyicloud-API (DriveNode, in pyicloud 2.6.4 verifiziert):
``node.get_children()``, ``node.type`` ('file'/'folder'/…), ``node.name``, ``node.size``,
``node.date_modified`` (UTC), ``node.data['etag']``, ``node.open(stream=True)`` -> requests-Response.
``api.drive`` delegiert auf den Root-Node.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import state, util

LOGGER = logging.getLogger(__name__)

# Knoten, in die wir nicht absteigen.
_SKIP_TYPES = {"trash", "unknown"}
# Maximale Rekursionstiefe als Schutz vor pathologischen/zyklischen Bäumen.
_MAX_DEPTH = 64


@dataclass
class DriveStats:
    downloaded: int = 0
    skipped: int = 0
    errors: int = 0
    folders: int = 0
    error_paths: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"Drive: {self.downloaded} geladen, {self.skipped} unverändert, "
                f"{self.errors} Fehler ({self.folders} Ordner)")


def sync_drive(api, dest_base_path: str, apple_id: str) -> DriveStats:
    """Sichert iCloud Drive inkrementell nach ``dest_base_path/Drive``.

    :param api: authentifizierte ``PyiCloudService``-Instanz.
    :returns: :class:`DriveStats` mit Zählern für das Logging.
    """
    stats = DriveStats()
    root_dest = Path(dest_base_path) / "Drive"
    conn = state.connect(apple_id)
    try:
        try:
            children = util.with_retries(lambda: api.drive.get_children(), label="drive.root")
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Drive-Root nicht lesbar für %s: %s", apple_id, exc)
            stats.errors += 1
            return stats
        for child in children:
            try:
                name = util.safe_component(child.name)
            except Exception:  # noqa: BLE001
                name = "<unbenannt>"
            _walk(child, [name], root_dest, conn, stats, depth=0)
        state.meta_set(conn, "drive_last_run", state._iso(_utcnow()))
        LOGGER.info("[%s] %s", apple_id, stats.summary())
        return stats
    finally:
        conn.close()


def _walk(node, rel_parts: list[str], root_dest: Path, conn, stats: DriveStats, depth: int) -> None:
    """Verarbeitet einen Knoten rekursiv (Datei -> ggf. laden, Ordner -> absteigen)."""
    try:
        node_type = node.type
    except Exception as exc:  # noqa: BLE001 - defekter Knoten soll den Lauf nicht stoppen
        LOGGER.warning("Knotentyp nicht lesbar (%s): %s", "/".join(rel_parts), exc)
        stats.errors += 1
        return

    if node_type in _SKIP_TYPES:
        return

    if node_type == "file":
        _sync_file(node, rel_parts, root_dest, conn, stats)
        return

    # Ordner (oder app_library etc.): absteigen.
    if depth >= _MAX_DEPTH:
        LOGGER.warning("Maximale Tiefe erreicht bei %s — überspringe", "/".join(rel_parts))
        return
    stats.folders += 1
    try:
        children = util.with_retries(lambda: node.get_children(),
                                     label=f"drive.children:{'/'.join(rel_parts) or '<root>'}")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Ordner nicht lesbar %s: %s", "/".join(rel_parts), exc)
        stats.errors += 1
        return
    for child in children:
        try:
            child_name = util.safe_component(child.name)
        except Exception:  # noqa: BLE001
            child_name = "<unbenannt>"
        _walk(child, rel_parts + [child_name], root_dest, conn, stats, depth + 1)


def _sync_file(node, rel_parts: list[str], root_dest: Path, conn, stats: DriveStats) -> None:
    rel_path = "/".join(rel_parts)
    try:
        size = node.size
        date_modified = node.date_modified
        etag = node.data.get("etag")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Metadaten nicht lesbar %s: %s", rel_path, exc)
        stats.errors += 1
        stats.error_paths.append(rel_path)
        return

    if not state.drive_needs_download(conn, rel_path, size, date_modified, etag):
        stats.skipped += 1
        return

    dest = root_dest.joinpath(*rel_parts)
    try:
        if size == 0:
            # iCloud liefert für 0-Byte-Dateien 400 beim Download -> leere Datei anlegen.
            util.write_empty_file(dest)
        else:
            def _download():
                response = node.open(stream=True)
                try:
                    return util.stream_to_file(response, dest)
                finally:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()

            util.with_retries(_download, label=f"drive.download:{rel_path}")
        util.set_mtime(dest, date_modified)
        state.drive_record(conn, rel_path, size, date_modified, etag)
        stats.downloaded += 1
        LOGGER.debug("geladen: %s (%s Bytes)", rel_path, size)
    except Exception as exc:  # noqa: BLE001 - einzelne Datei darf den Lauf nicht kippen
        LOGGER.warning("Download fehlgeschlagen %s: %s", rel_path, exc)
        stats.errors += 1
        stats.error_paths.append(rel_path)


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
