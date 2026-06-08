"""iCloud Drive – dateibasierter Sync-Spiegel.

Läuft den Drive-Tree rekursiv ab und spiegelt ihn nach ``<dest_base_path>/Drive/``.
**Kein Manifest/sqlite** — das Dateisystem ist der Zustand:

- **Download-Entscheidung** rein über lokalen Datei-Stat (``util.needs_download``: Datei fehlt
  oder Größe/Änderungszeit weichen ab).
- **Spiegel:** lokale Dateien, die es serverseitig nicht mehr gibt, werden entfernt — aber **nur**
  nach einem vollständigen, fehlerfreien Listing (Guard ``complete``). Historie ⇒ UNAS-Snapshots.
- **Resumebar:** Download nach ``.part`` + atomarer Rename.
- **Robust:** Fehler einzelner Dateien/Ordner brechen den Lauf nicht ab; Throttling -> Backoff.

pyicloud-API (DriveNode, in pyicloud 2.6.4 verifiziert):
``node.get_children()``, ``node.type`` ('file'/'folder'/…), ``node.name``, ``node.size``,
``node.date_modified`` (UTC), ``node.open(stream=True)`` -> requests-Response.
``api.drive`` delegiert auf den Root-Node.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import util

LOGGER = logging.getLogger(__name__)

# Knoten, in die wir nicht absteigen.
_SKIP_TYPES = {"trash", "unknown"}
# Maximale Rekursionstiefe als Schutz vor pathologischen/zyklischen Bäumen.
_MAX_DEPTH = 64


@dataclass
class DriveStats:
    downloaded: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: int = 0
    folders: int = 0
    error_paths: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"Drive: {self.downloaded} geladen, {self.skipped} unverändert, "
                f"{self.deleted} entfernt, {self.errors} Fehler ({self.folders} Ordner)")


@dataclass
class _Ctx:
    root_dest: Path
    stats: DriveStats
    expected: set                  # set[Path] der serverseitig vorhandenen Zieldateien
    progress_cb: object = None
    complete: bool = True          # False, sobald ein Ordner-Listing fehlschlägt -> kein Pruning


def sync_drive(api, dest_base_path: str, apple_id: str, progress_cb=None) -> DriveStats:
    """Spiegelt iCloud Drive nach ``dest_base_path/Drive`` (dateibasiert).

    :param api: authentifizierte ``PyiCloudService``-Instanz.
    :param progress_cb: optionaler Callback ``cb(counts: dict)`` für Live-Fortschritt.
    :returns: :class:`DriveStats` mit Zählern für das Logging.
    """
    stats = DriveStats()
    ctx = _Ctx(root_dest=Path(dest_base_path) / "Drive", stats=stats,
               expected=set(), progress_cb=progress_cb)
    _emit(ctx)

    try:
        children = util.with_retries(lambda: api.drive.get_children(), label="drive.root")
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Drive-Root nicht lesbar für %s: %s", apple_id, exc)
        stats.errors += 1
        return stats  # Root unlesbar -> niemals löschen

    for child in children:
        try:
            name = util.safe_component(child.name)
        except Exception:  # noqa: BLE001
            name = "<unbenannt>"
        _walk(child, [name], ctx, 0)

    # Spiegel: lokal Überzähliges entfernen – nur bei vollständigem, fehlerfreiem Listing.
    if ctx.complete:
        stats.deleted = util.prune_extra(ctx.root_dest, ctx.expected)
    else:
        LOGGER.warning("[%s] Drive-Listing unvollständig -> kein Löschen (nur Download).", apple_id)
    _emit(ctx)
    LOGGER.info("[%s] %s", apple_id, stats.summary())
    return stats


def _emit(ctx: _Ctx) -> None:
    if ctx.progress_cb is not None:
        s = ctx.stats
        ctx.progress_cb({"downloaded": s.downloaded, "skipped": s.skipped,
                         "deleted": s.deleted, "errors": s.errors})


def _walk(node, rel_parts: list[str], ctx: _Ctx, depth: int) -> None:
    """Verarbeitet einen Knoten rekursiv (Datei -> ggf. laden, Ordner -> absteigen)."""
    try:
        node_type = node.type
    except Exception as exc:  # noqa: BLE001 - defekter Knoten soll den Lauf nicht stoppen
        LOGGER.warning("Knotentyp nicht lesbar (%s): %s", "/".join(rel_parts), exc)
        ctx.stats.errors += 1
        ctx.complete = False  # unklarer Knoten -> sicherheitshalber kein Pruning
        return

    if node_type in _SKIP_TYPES:
        return

    if node_type == "file":
        _sync_file(node, rel_parts, ctx)
        _emit(ctx)
        return

    # Ordner (oder app_library etc.): absteigen.
    if depth >= _MAX_DEPTH:
        LOGGER.warning("Maximale Tiefe erreicht bei %s — überspringe", "/".join(rel_parts))
        ctx.complete = False
        return
    ctx.stats.folders += 1
    try:
        children = util.with_retries(lambda: node.get_children(),
                                     label=f"drive.children:{'/'.join(rel_parts) or '<root>'}")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Ordner nicht lesbar %s: %s", "/".join(rel_parts), exc)
        ctx.stats.errors += 1
        ctx.complete = False  # Ordner unlesbar -> Inhalt unbekannt -> kein Pruning
        return
    for child in children:
        try:
            child_name = util.safe_component(child.name)
        except Exception:  # noqa: BLE001
            child_name = "<unbenannt>"
        _walk(child, rel_parts + [child_name], ctx, depth + 1)


def _sync_file(node, rel_parts: list[str], ctx: _Ctx) -> None:
    rel_path = "/".join(rel_parts)
    dest = ctx.root_dest.joinpath(*rel_parts)
    # Der Server hat diese Datei -> in expected aufnehmen (schützt vor Löschen), unabhängig vom
    # Download-Erfolg.
    ctx.expected.add(dest)

    try:
        size = node.size
        date_modified = node.date_modified
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Metadaten nicht lesbar %s: %s", rel_path, exc)
        ctx.stats.errors += 1
        ctx.stats.error_paths.append(rel_path)
        return

    if not util.needs_download(dest, size, date_modified):
        ctx.stats.skipped += 1
        return

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
        ctx.stats.downloaded += 1
        LOGGER.debug("geladen: %s (%s Bytes)", rel_path, size)
    except Exception as exc:  # noqa: BLE001 - einzelne Datei darf den Lauf nicht kippen
        LOGGER.warning("Download fehlgeschlagen %s: %s", rel_path, exc)
        ctx.stats.errors += 1
        ctx.stats.error_paths.append(rel_path)
