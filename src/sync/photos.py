"""iCloud Photos – inkrementeller Download (STUB).

Phase-2-Plan (pyicloud-API, in ``auth.session`` gekapselt — hier nur konzeptionell):

- Alle Assets iterieren: ``for photo in api.photos.all:`` (paginiert intern). ``api.photos.all``
  ist nach ``added_date`` sortiert (neueste zuerst); Alben über ``api.photos.albums[name]``.
- Pro Asset: ``photo.id``, ``photo.filename``, ``photo.versions`` (Keys u. a. ``'original'``).
  Original laden::

      data = photo.download('original')      # raw stream / bytes
      # filename der Version: photo.versions['original']['filename']

- Inkrementell: in ``state.photo_assets`` ``asset_id``/``downloaded`` führen; nur neue/geänderte laden.
- **Erstlauf lädt ALLES** (ganze Mediathek, evtl. sehr groß/lang) -> resumebar, Fortschritt in sqlite.
- **Dateinamen-Kollisionen** (gleicher Name, anderes Asset): ``asset_id`` in den Zielpfad aufnehmen.
- **Live Photos / HEIC**: Live Photo = Foto + Video. DEFAULT-Entscheidung (Spec): **beide sichern**
  — also zusätzlich zur Bild-Version die zugehörige Video-Komponente herunterladen.
- Originale, nicht optimierte Versionen verwenden.
"""

from __future__ import annotations


def sync_photos(api, dest_base_path: str, apple_id: str) -> None:
    """Sichert iCloud Photos inkrementell/resumebar nach ``dest_base_path``. STUB (Phase 2)."""
    raise NotImplementedError("Phase 2: Photos-Sync gemäß Modul-Docstring implementieren.")
