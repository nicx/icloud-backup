"""Mock-basierte Tests für die Sync-Schicht (kein Netzwerk, kein Apple-Account).

Ausführen::

    HOME=<tmp> .venv/bin/python tests/test_sync.py

``HOME`` wird im Skript selbst auf ein Temp-Verzeichnis gesetzt, damit das sqlite-Manifest
(``~/Library/Application Support/...``) und nichts Echtes berührt wird.
"""

from __future__ import annotations

import io
import os
import tempfile
from datetime import datetime, timezone

# Isolierte Umgebung VOR den Projekt-Imports setzen.
os.environ["HOME"] = tempfile.mkdtemp(prefix="iclbk_test_home_")
import sys
sys.path.insert(0, os.getcwd())

from src.sync import drive, photos, state, engine  # noqa: E402
from src.config.users import User, UserStatus  # noqa: E402


# --- Fakes ------------------------------------------------------------------

class FakeResponse:
    def __init__(self, content: bytes):
        self._content = content
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]

    def raise_for_status(self):
        pass

    def close(self):
        self.closed = True


class FakeDriveNode:
    def __init__(self, name, node_type, *, size=None, content=b"", etag="e1",
                 children=None, date_modified=None):
        self.name = name
        self.type = node_type
        self.size = size
        self._content = content
        self.data = {"etag": etag}
        self._children = children or []
        self.date_modified = date_modified or datetime(2024, 1, 1, tzinfo=timezone.utc)

    def get_children(self):
        return self._children

    def open(self, stream=True):
        return FakeResponse(self._content)


class FakeDriveService:
    def __init__(self, root_children):
        self._root_children = root_children

    def get_children(self):
        return self._root_children


class FakePhotoAsset:
    def __init__(self, asset_id, filename, *, content=b"PHOTO", is_live=False,
                 video_content=b"VIDEO", created=None):
        self.id = asset_id
        self.filename = filename
        self.is_live_photo = is_live
        self.created = created or datetime(2023, 7, 15, tzinfo=timezone.utc)
        self._urls = {"original": f"https://x/{asset_id}/orig"}
        self._content = {f"https://x/{asset_id}/orig": content}
        self.versions = {"original": {"filename": filename, "url": self._urls["original"]}}
        if is_live:
            vurl = f"https://x/{asset_id}/vid"
            self._urls["original_video"] = vurl
            self._content[vurl] = video_content
            vname = filename.rsplit(".", 1)[0] + ".MOV"
            self.versions["original_video"] = {"filename": vname, "url": vurl}

    def download_url(self, version="original"):
        return self._urls.get(version)

    def download(self, version="original"):
        return self._content.get(self._urls.get(version, ""))


class FakeSession:
    def __init__(self, url_to_content):
        self._map = url_to_content

    def get(self, url, stream=True):
        return FakeResponse(self._map[url])


class FakePhotosLib:
    def __init__(self, assets):
        self.all = assets


class FakeApi:
    def __init__(self, *, drive_service=None, photos_assets=None, url_map=None):
        self.drive = drive_service
        self.photos = FakePhotosLib(photos_assets or [])
        self.session = FakeSession(url_map or {})


# --- Helpers ----------------------------------------------------------------

PASS = []
def check(cond, msg):
    assert cond, "FAIL: " + msg
    PASS.append(msg)


def read(path):
    with open(path, "rb") as f:
        return f.read()


# --- Tests ------------------------------------------------------------------

def test_state():
    db = os.path.join(tempfile.mkdtemp(), "m.sqlite")
    conn = state.connect_path(db)
    dt = datetime(2024, 5, 1, tzinfo=timezone.utc)
    check(state.drive_needs_download(conn, "a/b.txt", 10, dt, "e1") is True, "drive new -> needs download")
    state.drive_record(conn, "a/b.txt", 10, dt, "e1")
    check(state.drive_needs_download(conn, "a/b.txt", 10, dt, "e1") is False, "drive unchanged -> skip")
    check(state.drive_needs_download(conn, "a/b.txt", 11, dt, "e1") is True, "drive size change -> download")
    check(state.drive_needs_download(conn, "a/b.txt", 10, dt, "e2") is True, "drive etag change -> download")

    check(state.photo_is_downloaded(conn, "id1", dt, False) is False, "photo new -> not downloaded")
    state.photo_record(conn, "id1", "IMG.JPG", dt, has_video=False, downloaded=True)
    check(state.photo_is_downloaded(conn, "id1", dt, False) is True, "photo recorded -> downloaded")
    check(state.photo_is_downloaded(conn, "id1", dt, True) is False, "photo gained live-video -> redownload")

    state.meta_set(conn, "k", "v")
    check(state.meta_get(conn, "k") == "v", "meta round-trip")
    conn.close()


def test_drive():
    dest = tempfile.mkdtemp(prefix="drivedest_")
    f1 = FakeDriveNode("hello.txt", "file", size=5, content=b"hello", etag="e1")
    f0 = FakeDriveNode("empty.bin", "file", size=0, content=b"", etag="e0")
    sub = FakeDriveNode("Sub", "folder", children=[f1])
    root_children = [sub, f0]
    api = FakeApi(drive_service=FakeDriveService(root_children))

    stats = drive.sync_drive(api, dest, "drive@example.com")
    check(stats.downloaded == 2, f"drive: 2 geladen (war {stats.downloaded})")
    check(read(os.path.join(dest, "Drive", "Sub", "hello.txt")) == b"hello", "drive nested file content")
    check(os.path.exists(os.path.join(dest, "Drive", "empty.bin")), "drive 0-byte file created")
    check(os.path.getsize(os.path.join(dest, "Drive", "empty.bin")) == 0, "drive 0-byte file is empty")

    # zweiter Lauf: alles unverändert -> skip
    stats2 = drive.sync_drive(api, dest, "drive@example.com")
    check(stats2.downloaded == 0 and stats2.skipped == 2, f"drive 2. Lauf skip (dl={stats2.downloaded}, skip={stats2.skipped})")


def test_photos():
    dest = tempfile.mkdtemp(prefix="photodest_")
    a = FakePhotoAsset("AAA", "IMG_1.JPG", content=b"img1")
    b = FakePhotoAsset("BBB", "IMG_1.JPG", content=b"img2")  # gleicher Name, anderes Asset
    live = FakePhotoAsset("CCC", "IMG_2.HEIC", content=b"heic", is_live=True, video_content=b"mov")
    url_map = {}
    for asset in (a, b, live):
        url_map.update(asset._content)
    api = FakeApi(photos_assets=[a, b, live], url_map=url_map)

    stats = photos.sync_photos(api, dest, "photo@example.com")
    check(stats.downloaded == 3, f"photos: 3 Assets geladen (war {stats.downloaded})")
    check(stats.components == 4, f"photos: 4 Dateien inkl. Live-Video (war {stats.components})")

    # Live Photo: Foto + Video vorhanden
    pdir = os.path.join(dest, "Photos", "2023", "07")
    files = os.listdir(pdir)
    heics = [f for f in files if f.endswith(".HEIC")]
    movs = [f for f in files if f.endswith(".MOV")]
    check(len(heics) == 1 and len(movs) == 1, f"live photo -> HEIC+MOV (files={files})")

    # Kollision: zwei IMG_1.JPG, verschiedene Inhalte, beide vorhanden
    jpgs = sorted(f for f in files if f.endswith(".JPG"))
    check(len(jpgs) == 2, f"kollision: 2 JPG-Dateien (files={files})")
    contents = {read(os.path.join(pdir, f)) for f in jpgs}
    check(contents == {b"img1", b"img2"}, "kollision: beide Inhalte erhalten")

    # zweiter Lauf -> skip
    stats2 = photos.sync_photos(api, dest, "photo@example.com")
    check(stats2.downloaded == 0 and stats2.skipped == 3, f"photos 2. Lauf skip (dl={stats2.downloaded}, skip={stats2.skipped})")


def test_engine(monkeypatch_pw="secret"):
    dest = tempfile.mkdtemp(prefix="enginedest_")
    a = FakePhotoAsset("E1", "P.JPG", content=b"x")
    f1 = FakeDriveNode("d.txt", "file", size=3, content=b"abc")
    api = FakeApi(drive_service=FakeDriveService([f1]), photos_assets=[a], url_map=dict(a._content))

    # session.login + keychain.get_password patchen (kein echter Apple-Login)
    from src.auth import session as sess, keychain
    keychain.get_password = lambda aid: "secret"
    sess.login = lambda aid, pw: sess.LoginResult(api=api)

    user = User(apple_id="e@example.com", sync_drive=True, sync_photos=True, dest_base_path=dest)
    status = engine.run_user(user)
    check(status == UserStatus.OK, f"engine: Status OK (war {status})")
    check(os.path.exists(os.path.join(dest, "Drive", "d.txt")), "engine: Drive-Datei da")
    check(len(os.listdir(os.path.join(dest, "Photos", "2023", "07"))) == 1, "engine: Photo da")

    # Mount fehlt -> ERROR
    bad = User(apple_id="e2@example.com", dest_base_path="/nope/missing")
    check(engine.run_user(bad) == UserStatus.ERROR, "engine: fehlender Mount -> ERROR")


if __name__ == "__main__":
    test_state()
    test_drive()
    test_photos()
    test_engine()
    for m in PASS:
        print("  ok:", m)
    print(f"\nALL {len(PASS)} SYNC TESTS PASSED")
