"""iCloud Photos – inkrementeller, resumebarer Download der Originale.

Iteriert alle Assets (``api.photos.all``, intern paginiert) und lädt jedes Original nach
``<dest_base_path>/Photos/<YYYY>/<MM>/``. Ein Manifest (``state.photo_assets``) merkt sich
geladene Assets, sodass Folgeläufe nur Neues holen und ein Abbruch fortgesetzt werden kann.

Spec-Entscheidungen:

- **Originale**, nicht optimierte Versionen (Version ``'original'``).
- **Live Photos**: Foto **und** Video sichern. Die Video-Komponente ist in pyicloud 2.6.4
  die Version ``'original_video'`` (``resOriginalVidCompl``); sie wird zusätzlich geladen.
- **Dateinamen-Kollisionen** (gleicher Name, anderes Asset): Ziel enthält eine kurze,
  stabile Asset-ID als Präfix -> kollisionsfrei und deterministisch (kein Doppel-Download).
- **Erstlauf lädt ALLES** -> resumebar über das Manifest.
- **Speicherschonend**: Streaming über die authentifizierte Session statt voller In-Memory-Download.

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

from . import state, util

LOGGER = logging.getLogger(__name__)

MAIN_VERSION = "original"
LIVE_VIDEO_VERSION = "original_video"
_PROGRESS_EVERY = 200


@dataclass
class PhotoStats:
    downloaded: int = 0       # Assets vollständig neu geladen
    components: int = 0       # einzelne Dateien (Foto + ggf. Live-Video)
    skipped: int = 0
    errors: int = 0
    error_ids: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"Photos: {self.downloaded} Assets geladen ({self.components} Dateien), "
                f"{self.skipped} unverändert, {self.errors} Fehler")


def _emit(stats: PhotoStats, seen: int, progress_cb) -> None:
    if progress_cb is not None:
        progress_cb({"downloaded": stats.downloaded, "skipped": stats.skipped,
                     "errors": stats.errors, "seen": seen})


def sync_photos(api, dest_base_path: str, apple_id: str, progress_cb=None) -> PhotoStats:
    """Sichert iCloud Photos inkrementell/resumebar nach ``dest_base_path/Photos``.

    :param api: authentifizierte ``PyiCloudService``-Instanz.
    :param progress_cb: optionaler Callback ``cb(counts: dict)`` für Live-Fortschritt.
    :returns: :class:`PhotoStats` mit Zählern für das Logging.
    """
    stats = PhotoStats()
    photos_dest = Path(dest_base_path) / "Photos"
    conn = state.connect(apple_id)
    _emit(stats, 0, progress_cb)
    try:
        try:
            album = api.photos.all
            iterator = iter(album)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Photos-Bibliothek nicht lesbar für %s: %s", apple_id, exc)
            stats.errors += 1
            return stats

        seen = 0
        while True:
            # Pagination-Fehler einzeln tolerieren, aber Endlosschleifen vermeiden.
            try:
                asset = next(iterator)
            except StopIteration:
                break
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Asset-Iteration unterbrochen: %s", exc)
                stats.errors += 1
                break
            seen += 1
            if seen % _PROGRESS_EVERY == 0:
                LOGGER.info("[%s] Photos-Fortschritt: %d gesichtet, %d geladen",
                            apple_id, seen, stats.downloaded)
            _sync_asset(api, asset, photos_dest, conn, stats)
            _emit(stats, seen, progress_cb)

        state.meta_set(conn, "photos_last_run", _utcnow_iso())
        LOGGER.info("[%s] %s", apple_id, stats.summary())
        return stats
    finally:
        conn.close()


def _sync_asset(api, asset, photos_dest: Path, conn, stats: PhotoStats) -> None:
    try:
        asset_id = asset.id
        filename = asset.filename or "unbenannt"
        created = asset.created
        is_live = bool(getattr(asset, "is_live_photo", False))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Asset-Metadaten nicht lesbar: %s", exc)
        stats.errors += 1
        return

    if state.photo_is_downloaded(conn, asset_id, created, is_live):
        stats.skipped += 1
        return

    target_dir = photos_dest / _date_folder(created)
    short = _short_id(asset_id)
    components = 0

    # 1) Hauptkomponente (Original-Foto oder -Video)
    try:
        main_name = _version_filename(asset, MAIN_VERSION, default=filename)
        dest = target_dir / f"{short}_{util.safe_component(main_name)}"
        if _download_version(api, asset, MAIN_VERSION, dest, created):
            components += 1
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Original fehlgeschlagen %s (%s): %s", filename, asset_id, exc)
        stats.errors += 1
        stats.error_ids.append(asset_id)
        return  # ohne Original kein vollständiges Asset

    # 2) Live-Photo-Video-Komponente (zusätzlich)
    if is_live:
        try:
            vid_name = _version_filename(asset, LIVE_VIDEO_VERSION,
                                         default=_with_suffix(filename, ".MOV"))
            vdest = target_dir / f"{short}_{util.safe_component(vid_name)}"
            if _download_version(api, asset, LIVE_VIDEO_VERSION, vdest, created):
                components += 1
        except Exception as exc:  # noqa: BLE001 - Foto ist da; Video-Fehler nicht fatal
            LOGGER.warning("Live-Video fehlgeschlagen %s (%s): %s", filename, asset_id, exc)
            stats.errors += 1
            stats.error_ids.append(asset_id)
            # Asset NICHT als vollständig markieren -> nächster Lauf versucht das Video erneut.
            return

    state.photo_record(conn, asset_id, filename, created, has_video=is_live, downloaded=True)
    stats.downloaded += 1
    stats.components += components


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


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
