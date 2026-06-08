"""iCloud Drive – inkrementeller Download (STUB).

Phase-2-Plan (pyicloud-API, in ``auth.session`` gekapselt — hier nur konzeptionell):

- Drive-Tree rekursiv durchlaufen: ``api.drive.dir()`` liefert Top-Level-Namen,
  Navigation per ``api.drive['Ordner']['Unterordner']``; ``.dir()`` listet Kinder.
- Pro Datei-Knoten: ``node.type == 'file'``; Metadaten ``node.name``, ``node.size``,
  ``node.date_modified`` (UTC ``datetime``).
- Inkrementell: in ``state.drive_files`` Pfad/Größe/date_modified führen; nur laden,
  wenn neu oder geändert.
- Download (Stream, speicherschonend)::

      from shutil import copyfileobj
      with node.open(stream=True) as response:
          with open(ziel, 'wb') as out:
              copyfileobj(response.raw, out)

- Additiv: in iCloud gelöschte Dateien NICHT im Backup löschen.
- Resumebar: Abbruch darf den nächsten Lauf nicht zerstören (Manifest pro Datei updaten).
"""

from __future__ import annotations


def sync_drive(api, dest_base_path: str, apple_id: str) -> None:
    """Sichert iCloud Drive inkrementell nach ``dest_base_path``. STUB (Phase 2)."""
    raise NotImplementedError("Phase 2: Drive-Sync gemäß Modul-Docstring implementieren.")
