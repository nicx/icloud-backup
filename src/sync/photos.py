"""iCloud Photos – dateibasierter Sync-Spiegel der Originale.

Iteriert alle Assets (``api.photos.all``, intern paginiert) und spiegelt sie nach
``<dest_base_path>/Photos/<YYYY>/<MM>/``. **Kein Manifest/sqlite** — das Dateisystem ist der
Zustand: „schon geladen?" = Zieldatei existiert (Pfad deterministisch aus Asset-ID + Name).

Eigenschaften:

- **Originale** (Version ``'original'``), nicht optimierte Versionen.
- **Live Photos**: Foto **und** Video. Die Video-Komponente ist Version ``'original_video'``
  (``resOriginalVidCompl``) und wird zusätzlich geladen.
- **Dateinamen-Kollisionen** (gleicher Name, anderes Asset): kurze, stabile Asset-ID als Präfix.
- **Spiegel:** lokale Dateien zu Assets, die es in iCloud nicht mehr gibt, werden entfernt — aber
  **nur** nach vollständiger, fehlerfreier Iteration und nicht-leerem Ergebnis (Guard). Historie ⇒ Snapshots.
- **Speicherschonend**: Streaming über die authentifizierte Session.

pyicloud-API (PhotoAsset, verifiziert): ``.id``, ``.filename``, ``.created`` (datetime),
``.is_live_photo``, ``.versions`` (dict key->{filename,url,size}), ``.download_url(version)``,
``.download(version)`` (bytes, Fallback).
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import util

LOGGER = logging.getLogger(__name__)

MAIN_VERSION = "original"
LIVE_VIDEO_VERSION = "original_video"
_PROGRESS_EVERY = 200


@dataclass
class PhotoStats:
    downloaded: int = 0       # Assets mit mind. einer neu geladenen Datei
    components: int = 0       # einzelne neu geladene Dateien (Foto + ggf. Live-Video)
    skipped: int = 0          # Assets bereits vollständig vorhanden
    deleted: int = 0
    errors: int = 0
    error_ids: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"Photos: {self.downloaded} Assets geladen ({self.components} Dateien), "
                f"{self.skipped} unverändert, {self.deleted} entfernt, {self.errors} Fehler")


def _emit(stats: PhotoStats, seen: int, progress_cb) -> None:
    if progress_cb is not None:
        progress_cb({"downloaded": stats.downloaded, "skipped": stats.skipped,
                     "deleted": stats.deleted, "errors": stats.errors, "seen": seen})


def sync_photos(api, dest_base_path: str, apple_id: str, progress_cb=None,
                include_shared: bool = False) -> PhotoStats:
    """Spiegelt iCloud Photos nach ``dest_base_path/Photos`` (dateibasiert).

    :param api: authentifizierte ``PyiCloudService``-Instanz.
    :param progress_cb: optionaler Callback ``cb(counts: dict)`` für Live-Fortschritt.
    :param include_shared: zusätzlich die geteilte Mediathek nach ``SharedPhotos/`` sichern
        (eigener Prune-Scope, getrennt von ``Photos/``).
    :returns: :class:`PhotoStats` mit Zählern für das Logging.
    """
    stats = PhotoStats()
    base = Path(dest_base_path)

    # 1) Persönliche/private Mediathek -> Photos/
    _sync_into(api, base / "Photos", [lambda: api.photos.all], apple_id, stats, progress_cb, "Photos")

    # 2) Geteilte Mediathek (optional) -> SharedPhotos/ (eigener Prune-Scope)
    if include_shared:
        sources = _shared_album_sources(api, apple_id)
        if sources:
            _sync_into(api, base / "SharedPhotos", sources, apple_id, stats, progress_cb, "SharedPhotos")
        else:
            LOGGER.info("[%s] Keine geteilte Mediathek gefunden (oder nicht lesbar).", apple_id)

    LOGGER.info("[%s] %s", apple_id, stats.summary())
    return stats


def _shared_album_sources(api, apple_id: str) -> list:
    """Callables, die je geteilte Mediathek (``scope == 'shared-library'``) das All-Album liefern.

    Best-effort: jeder Zugriff ist gekapselt (pyicloud-Drift/Netz), Fehler ⇒ leere Liste, damit
    der private Sync nie kippt. Leere Liste führt beim Aufrufer zu **keinem** Prune (kein Wipe).
    """
    sources: list = []
    try:
        libraries = api.photos.libraries
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("[%s] Geteilte Mediatheken nicht abrufbar: %s", apple_id, exc)
        return sources
    try:
        items = list(libraries.items()) if hasattr(libraries, "items") else list(libraries)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("[%s] Bibliotheksliste nicht lesbar: %s", apple_id, exc)
        return sources
    for entry in items:
        lib = entry[1] if isinstance(entry, tuple) else entry
        if getattr(lib, "scope", None) == "shared-library":
            sources.append(lambda lib=lib: lib.all)
    return sources


def _sync_into(api, photos_dest: Path, album_sources: list, apple_id: str, stats: PhotoStats,
               progress_cb, label: str) -> None:
    """Spiegelt ein oder mehrere Alben in **eine** Ablage (eigener expected-Set + Prune-Scope).

    Mehrere Quellen (z. B. mehrere geteilte Zonen) werden zusammengeführt; Dedup über die
    Asset-ID. Prune nur bei vollständiger Iteration ALLER Quellen UND nicht-leerem Ergebnis.
    """
    expected: set = set()
    complete = True
    seen = 0
    _emit(stats, seen, progress_cb)

    for source in album_sources:
        try:
            iterator = iter(source())
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("[%s] %s-Bibliothek nicht lesbar: %s", apple_id, label, exc)
            stats.errors += 1
            complete = False  # Quelle unlesbar -> kein Pruning (Inhalt unbekannt)
            continue
        while True:
            try:
                asset = next(iterator)
            except StopIteration:
                break
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("[%s] %s-Iteration unterbrochen: %s", apple_id, label, exc)
                stats.errors += 1
                complete = False  # unvollständig -> kein Pruning
                break
            seen += 1
            if seen % _PROGRESS_EVERY == 0:
                LOGGER.info("[%s] %s-Fortschritt: %d gesichtet, %d geladen",
                            apple_id, label, seen, stats.downloaded)
            _sync_asset(api, asset, photos_dest, stats, expected)
            _emit(stats, seen, progress_cb)

    # Spiegel: nur bei vollständiger Iteration UND nicht-leerem Ergebnis (Schutz vor Massenlöschen).
    if complete and expected:
        stats.deleted += util.prune_extra(photos_dest, expected)
    elif not complete:
        LOGGER.warning("[%s] %s-Iteration unvollständig -> kein Löschen.", apple_id, label)
    elif not expected:
        LOGGER.warning("[%s] %s-Liste leer -> kein Löschen (Sicherheit).", apple_id, label)
    _emit(stats, seen, progress_cb)


def _sync_asset(api, asset, photos_dest: Path, stats: PhotoStats, expected: set) -> None:
    try:
        asset_id = asset.id
        filename = asset.filename or "unbenannt"
        created = asset.created
        is_live = bool(getattr(asset, "is_live_photo", False))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Asset-Metadaten nicht lesbar: %s", exc)
        stats.errors += 1
        return

    target_dir = photos_dest / _date_folder(created)
    short = _short_id(asset_id)

    # Zu sichernde Komponenten bestimmen: (Version, Zielpfad)
    components = [(MAIN_VERSION,
                   target_dir / f"{short}_{util.safe_component(_version_filename(asset, MAIN_VERSION, filename))}")]
    if is_live:
        components.append(
            (LIVE_VIDEO_VERSION,
             target_dir / f"{short}_{util.safe_component(_version_filename(asset, LIVE_VIDEO_VERSION, _with_suffix(filename, '.MOV')))}"))

    # Alle erwarteten Pfade schützen (vor Pruning), unabhängig vom Download-Erfolg.
    for _v, dest in components:
        expected.add(dest)

    newly = 0
    for version, dest in components:
        if dest.exists():
            continue  # bereits vorhanden -> Dateisystem ist der Zustand
        try:
            if _download_version(api, asset, version, dest, created):
                newly += 1
                stats.components += 1
        except Exception as exc:  # noqa: BLE001 - einzelne Datei darf den Lauf nicht kippen
            LOGGER.warning("Download fehlgeschlagen %s (%s, %s): %s", filename, asset_id, version, exc)
            stats.errors += 1
            stats.error_ids.append(asset_id)
            if version == MAIN_VERSION:
                return  # ohne Original kein vollständiges Asset

    if newly:
        stats.downloaded += 1
    else:
        stats.skipped += 1


def _download_version(api, asset, version: str, dest: Path, created) -> bool:
    """Lädt eine Version des Assets nach ``dest`` (Streaming, mit Retry). False, wenn nicht vorhanden."""
    url = _version_url(asset, version)

    def _do() -> bool:
        if url:
            response = api.session.get(url, stream=True)
            raise_for = getattr(response, "raise_for_status", None)
            if callable(raise_for):
                raise_for()
            try:
                util.stream_to_file(response, dest)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            return True
        # Fallback: vollständige Bytes über die Library (älterer/typed Pfad).
        data = asset.download(version)
        if data is None:
            return False
        util.stream_to_file(io.BytesIO(data), dest)
        return True

    ok = util.with_retries(_do, label=f"photos.download:{version}:{getattr(asset, 'id', '?')}")
    if ok:
        util.set_mtime(dest, created)
    return ok


def _version_url(asset, version: str) -> Optional[str]:
    """Bevorzugt ``download_url``; fällt auf das ``versions``-Dict zurück."""
    getter = getattr(asset, "download_url", None)
    if callable(getter):
        try:
            url = getter(version)
            if url:
                return url
        except Exception:  # noqa: BLE001
            pass
    try:
        return asset.versions.get(version, {}).get("url")
    except Exception:  # noqa: BLE001
        return None


def _version_filename(asset, version: str, default: str) -> str:
    try:
        name = asset.versions.get(version, {}).get("filename")
        if name:
            return name
    except Exception:  # noqa: BLE001
        pass
    return default


def _date_folder(created) -> str:
    """``YYYY/MM`` aus dem Erstelldatum; ``unbekannt`` wenn nicht verfügbar."""
    try:
        return f"{created.year:04d}/{created.month:02d}"
    except Exception:  # noqa: BLE001
        return "unbekannt"


def _short_id(asset_id: str) -> str:
    """Kurze, stabile, dateisystemsichere ID aus der (oft langen) Asset-ID."""
    return hashlib.sha1(asset_id.encode("utf-8")).hexdigest()[:10]


def _with_suffix(filename: str, suffix: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return stem + suffix
