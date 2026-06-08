"""Mock-basierte Tests für die Sync-Schicht (kein Netzwerk, kein Apple-Account).

Ausführen::

    .venv/bin/python tests/test_sync.py

``HOME`` wird im Skript auf ein Temp-Verzeichnis gesetzt, damit nichts Echtes berührt wird.
Reiner Datei-Sync (kein sqlite): das Dateisystem ist der Zustand.
"""

from __future__ import annotations

import imaplib
import os
import tempfile
from datetime import datetime, timezone

os.environ["HOME"] = tempfile.mkdtemp(prefix="iclbk_test_home_")
import sys
sys.path.insert(0, os.getcwd())

from src.sync import drive, photos, mail, engine  # noqa: E402
from src.config.users import User, UserStatus  # noqa: E402


# --- Fakes: Drive/Photos ----------------------------------------------------

class FakeResponse:
    def __init__(self, content: bytes):
        self._content = content

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]

    def raise_for_status(self):
        pass

    def close(self):
        pass


class FakeDriveNode:
    def __init__(self, name, node_type, *, size=None, content=b"", children=None,
                 date_modified=None, raise_children=False):
        self.name = name
        self.type = node_type
        self.size = size
        self._content = content
        self.data = {"etag": "e"}
        self._children = children or []
        self._raise_children = raise_children
        self.date_modified = date_modified or datetime(2024, 1, 1, tzinfo=timezone.utc)

    def get_children(self):
        if self._raise_children:
            raise RuntimeError("boom: folder unreadable")
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


class _IterFail:
    """Iterierbar, die nach `n` Elementen wirft (für Photos-Guard-Test)."""
    def __init__(self, items, fail_after):
        self._items, self._fail = items, fail_after

    def __iter__(self):
        for i, it in enumerate(self._items):
            if i >= self._fail:
                raise RuntimeError("iteration boom")
            yield it


class FakePhotosLib:
    def __init__(self, assets):
        self.all = assets


class FakeApi:
    def __init__(self, *, drive_service=None, photos_assets=None, url_map=None):
        self.drive = drive_service
        self.photos = FakePhotosLib(photos_assets if photos_assets is not None else [])
        self.session = FakeSession(url_map or {})


# --- Fakes: IMAP ------------------------------------------------------------

class FakeIMAP:
    """Minimaler IMAP-Server-Mock. mailboxes: name -> {"uidv": int, "msgs": {uid:int -> bytes}}."""
    good_password = "app-pw"
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.mailboxes = FakeIMAP._next_mailboxes
        self.search_fail = FakeIMAP._next_search_fail
        self._current = None
        self.selected_readonly = []
        self.fetch_specs = []
        FakeIMAP.instances.append(self)

    def login(self, user, pw):
        if pw != FakeIMAP.good_password:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return ("OK", [b"LOGIN ok"])

    def list(self):
        lines = [f'(\\HasNoChildren) "/" "{name}"'.encode() for name in self.mailboxes]
        return ("OK", lines)

    def select(self, mailbox, readonly=False):
        self._current = mailbox.strip('"')
        self.selected_readonly.append((self._current, readonly))
        return ("OK", [b"1"])

    def status(self, mailbox, what):
        name = mailbox.strip('"')
        uidv = self.mailboxes[name]["uidv"]
        return ("OK", [f"{name} (UIDVALIDITY {uidv})".encode()])

    def uid(self, command, *args):
        if command.upper() == "SEARCH":
            if self._current in self.search_fail:
                raise imaplib.IMAP4.error("SEARCH boom")
            uids = " ".join(str(u) for u in sorted(self.mailboxes[self._current]["msgs"]))
            return ("OK", [uids.encode()])
        if command.upper() == "FETCH":
            uid = int(args[0])
            self.fetch_specs.append(args[1])
            raw = self.mailboxes[self._current]["msgs"][uid]
            return ("OK", [(f"{uid} (BODY[] {{{len(raw)}}}".encode(), raw), b")"])
        raise imaplib.IMAP4.error(f"unknown {command}")

    def logout(self):
        return ("BYE", [b"bye"])


def use_imap(mailboxes, search_fail=()):
    FakeIMAP._next_mailboxes = mailboxes
    FakeIMAP._next_search_fail = set(search_fail)
    FakeIMAP.instances = []
    mail.imaplib.IMAP4_SSL = FakeIMAP


# --- Helpers ----------------------------------------------------------------

PASS = []
def check(cond, msg):
    assert cond, "FAIL: " + msg
    PASS.append(msg)


def read(path):
    with open(path, "rb") as f:
        return f.read()


def listdir(*parts):
    p = os.path.join(*parts)
    return sorted(os.listdir(p)) if os.path.isdir(p) else []


# --- Drive ------------------------------------------------------------------

def test_drive():
    dest = tempfile.mkdtemp(prefix="drivedest_")
    f1 = FakeDriveNode("hello.txt", "file", size=5, content=b"hello")
    f0 = FakeDriveNode("empty.bin", "file", size=0, content=b"")
    top = FakeDriveNode("top.txt", "file", size=3, content=b"abc")
    api = FakeApi(drive_service=FakeDriveService([FakeDriveNode("Sub", "folder", children=[f1]), f0, top]))

    s = drive.sync_drive(api, dest, "d@example.com")
    check(s.downloaded == 3, f"drive: 3 geladen (war {s.downloaded})")
    check(read(os.path.join(dest, "Drive", "Sub", "hello.txt")) == b"hello", "drive nested content")
    check(os.path.exists(os.path.join(dest, "Drive", "empty.bin")), "drive 0-byte angelegt")

    # 2. Lauf: unverändert -> skip (dateibasiert via Größe/mtime)
    s2 = drive.sync_drive(api, dest, "d@example.com")
    check(s2.downloaded == 0 and s2.skipped == 3, f"drive 2. Lauf skip (dl={s2.downloaded}, skip={s2.skipped})")

    # Spiegel: top.txt serverseitig entfernt -> lokal gelöscht
    api2 = FakeApi(drive_service=FakeDriveService([FakeDriveNode("Sub", "folder", children=[f1]), f0]))
    s3 = drive.sync_drive(api2, dest, "d@example.com")
    check(not os.path.exists(os.path.join(dest, "Drive", "top.txt")), "drive Spiegel: entfernte Datei weg")
    check(s3.deleted == 1, f"drive: 1 gelöscht (war {s3.deleted})")

    # Guard: Ordner-Listing-Fehler -> NICHTS löschen
    extra = os.path.join(dest, "Drive", "keepme.txt")
    with open(extra, "wb") as fh:
        fh.write(b"x")
    api3 = FakeApi(drive_service=FakeDriveService([FakeDriveNode("Bad", "folder", raise_children=True)]))
    s4 = drive.sync_drive(api3, dest, "d@example.com")
    check(s4.deleted == 0 and os.path.exists(extra), "drive Guard: Listing-Fehler -> kein Löschen")


# --- Photos -----------------------------------------------------------------

def test_photos():
    dest = tempfile.mkdtemp(prefix="photodest_")
    a = FakePhotoAsset("AAA", "IMG_1.JPG", content=b"img1")
    b = FakePhotoAsset("BBB", "IMG_1.JPG", content=b"img2")  # gleicher Name, anderes Asset
    live = FakePhotoAsset("CCC", "IMG_2.HEIC", content=b"heic", is_live=True, video_content=b"mov")
    url_map = {}
    for asset in (a, b, live):
        url_map.update(asset._content)
    api = FakeApi(photos_assets=[a, b, live], url_map=url_map)

    s = photos.sync_photos(api, dest, "p@example.com")
    check(s.downloaded == 3 and s.components == 4, f"photos: 3 Assets/4 Dateien (dl={s.downloaded}, c={s.components})")
    pdir = os.path.join(dest, "Photos", "2023", "07")
    files = listdir(pdir)
    check(len([f for f in files if f.endswith(".HEIC")]) == 1
          and len([f for f in files if f.endswith(".MOV")]) == 1, f"live -> HEIC+MOV ({files})")
    check(len([f for f in files if f.endswith(".JPG")]) == 2, f"kollision: 2 JPG ({files})")

    # 2. Lauf -> skip (Existenz)
    s2 = photos.sync_photos(api, dest, "p@example.com")
    check(s2.downloaded == 0 and s2.skipped == 3, f"photos 2. Lauf skip (dl={s2.downloaded}, skip={s2.skipped})")

    # Spiegel: Asset BBB entfernt -> dessen Datei weg
    api2 = FakeApi(photos_assets=[a, live], url_map=url_map)
    s3 = photos.sync_photos(api2, dest, "p@example.com")
    check(s3.deleted == 1, f"photos Spiegel: 1 gelöscht (war {s3.deleted})")
    check(len(listdir(pdir)) == 3, "photos: nach Löschen noch 3 Dateien (a + live HEIC+MOV)")

    # Guard: leere Liste -> NICHTS löschen
    api3 = FakeApi(photos_assets=[], url_map=url_map)
    s4 = photos.sync_photos(api3, dest, "p@example.com")
    check(s4.deleted == 0 and len(listdir(pdir)) == 3, "photos Guard: leere Liste -> kein Löschen")

    # Guard: Iterationsfehler -> NICHTS löschen
    api5 = FakeApi(url_map=url_map)
    api5.photos = FakePhotosLib(_IterFail([a, live], fail_after=1))
    s5 = photos.sync_photos(api5, dest, "p@example.com")
    check(s5.deleted == 0, "photos Guard: Iterationsfehler -> kein Löschen")


# --- Mail -------------------------------------------------------------------

def test_mail():
    dest = tempfile.mkdtemp(prefix="maildest_")
    boxes = {
        "INBOX": {"uidv": 10, "msgs": {1: b"From: a\r\n\r\nHallo", 2: b"From: b\r\n\r\nWelt"}},
        "Archive": {"uidv": 5, "msgs": {7: b"archived"}},
        "Trash": {"uidv": 3, "msgs": {}},
    }
    use_imap(boxes)
    s = mail.sync_mail("u@icloud.com", "app-pw", dest)
    check(s.downloaded == 3, f"mail: 3 geladen (war {s.downloaded})")
    check(read(os.path.join(dest, "Mail", "INBOX", "1.eml")).endswith(b"Hallo"), "mail INBOX/1.eml Inhalt")
    check(os.path.exists(os.path.join(dest, "Mail", "Archive", "7.eml")), "mail Archive/7.eml")
    # readonly + PEEK
    inst = FakeIMAP.instances[0]
    check(all(ro for _n, ro in inst.selected_readonly), "mail: select readonly=True")
    check(all("PEEK" in spec for spec in inst.fetch_specs), "mail: BODY.PEEK[] genutzt (ungelesen)")

    # 2. Lauf -> skip
    use_imap(boxes)
    s2 = mail.sync_mail("u@icloud.com", "app-pw", dest)
    check(s2.downloaded == 0 and s2.skipped == 3, f"mail 2. Lauf skip (dl={s2.downloaded}, skip={s2.skipped})")

    # Move/Delete: INBOX-Mail 2 -> Trash (neue UID); Spiegel zieht nach
    boxes2 = {
        "INBOX": {"uidv": 10, "msgs": {1: b"From: a\r\n\r\nHallo"}},
        "Archive": {"uidv": 5, "msgs": {7: b"archived"}},
        "Trash": {"uidv": 3, "msgs": {9: b"From: b\r\n\r\nWelt"}},
    }
    use_imap(boxes2)
    s3 = mail.sync_mail("u@icloud.com", "app-pw", dest)
    check(not os.path.exists(os.path.join(dest, "Mail", "INBOX", "2.eml")), "mail Move: INBOX/2.eml weg")
    check(os.path.exists(os.path.join(dest, "Mail", "Trash", "9.eml")), "mail Move: Trash/9.eml da")
    check(s3.deleted == 1 and s3.downloaded == 1, f"mail Move: 1 weg/1 neu (del={s3.deleted}, dl={s3.downloaded})")

    # UIDVALIDITY-Wechsel -> Ordner-Resync (stale Datei verschwindet)
    stale = os.path.join(dest, "Mail", "Archive", "999.eml")
    with open(stale, "wb") as fh:
        fh.write(b"stale")
    boxes3 = dict(boxes2)
    boxes3["Archive"] = {"uidv": 6, "msgs": {7: b"archived"}}  # uidv 5 -> 6
    use_imap(boxes3)
    mail.sync_mail("u@icloud.com", "app-pw", dest)
    check(not os.path.exists(stale), "mail UIDVALIDITY-Wechsel: stale Datei weg")
    check(read(os.path.join(dest, "Mail", "Archive", ".uidvalidity")) == b"6", "mail .uidvalidity aktualisiert")

    # Auth-Fehler -> MailAuthError
    use_imap(boxes2)
    try:
        mail.sync_mail("u@icloud.com", "wrong", dest)
        check(False, "mail: falsches PW hätte MailAuthError werfen müssen")
    except mail.MailAuthError:
        check(True, "mail: falsches PW -> MailAuthError")

    # Guard: SEARCH-Fehler in einem Ordner -> NICHTS löschen
    guard_extra = os.path.join(dest, "Mail", "INBOX", "1.eml")  # existiert
    boxes4 = {
        "INBOX": {"uidv": 10, "msgs": {}},   # leer -> würde 1.eml löschen, wenn nicht geguardet
        "Archive": {"uidv": 6, "msgs": {7: b"archived"}},
    }
    use_imap(boxes4, search_fail={"Archive"})
    s5 = mail.sync_mail("u@icloud.com", "app-pw", dest)
    check(s5.deleted == 0 and os.path.exists(guard_extra), "mail Guard: SEARCH-Fehler -> kein Löschen")


# --- Engine -----------------------------------------------------------------

def test_engine_all_services():
    dest = tempfile.mkdtemp(prefix="enginedest_")
    a = FakePhotoAsset("E1", "P.JPG", content=b"x")
    f1 = FakeDriveNode("d.txt", "file", size=3, content=b"abc")
    api = FakeApi(drive_service=FakeDriveService([f1]), photos_assets=[a], url_map=dict(a._content))

    from src.auth import session as sess, keychain
    keychain.get_password = lambda aid: "secret"
    keychain.get_mail_password = lambda aid: "app-pw"
    sess.login = lambda aid, pw: sess.LoginResult(api=api)
    use_imap({"INBOX": {"uidv": 1, "msgs": {1: b"hi"}}})

    user = User(apple_id="e@example.com", sync_drive=True, sync_photos=True,
                sync_mail=True, dest_base_path=dest)
    events = []
    status = engine.run_user(user, progress_cb=lambda aid, ph, c: events.append(ph))
    check(status == UserStatus.OK, f"engine: OK (war {status})")
    check({"drive", "photos", "mail"} <= set(events), f"engine: alle Phasen gemeldet ({set(events)})")
    check(os.path.exists(os.path.join(dest, "Drive", "d.txt")), "engine: Drive-Datei")
    check(os.path.exists(os.path.join(dest, "Mail", "INBOX", "1.eml")), "engine: Mail-Datei")


def test_engine_mail_independent_of_web():
    """Mail läuft, auch wenn die Web-Session 2FA braucht."""
    dest = tempfile.mkdtemp(prefix="engineindep_")
    from src.auth import session as sess, keychain
    keychain.get_password = lambda aid: "secret"
    keychain.get_mail_password = lambda aid: "app-pw"
    sess.login = lambda aid, pw: sess.LoginResult(needs_2fa=True)  # Web braucht Re-Auth
    use_imap({"INBOX": {"uidv": 1, "msgs": {1: b"hi"}}})

    user = User(apple_id="x@example.com", sync_drive=True, sync_mail=True, dest_base_path=dest)
    status = engine.run_user(user)
    check(os.path.exists(os.path.join(dest, "Mail", "INBOX", "1.eml")),
          "engine: Mail trotz Web-Re-Auth gesichert")
    check(status == UserStatus.NEEDS_REAUTH, f"engine: Status NEEDS_REAUTH bei Web-2FA (war {status})")


def test_engine_mount_missing():
    bad = User(apple_id="m@example.com", dest_base_path="/nope/missing", sync_mail=True)
    check(engine.run_user(bad) == UserStatus.ERROR, "engine: fehlender Mount -> ERROR")


if __name__ == "__main__":
    test_drive()
    test_photos()
    test_mail()
    test_engine_all_services()
    test_engine_mail_independent_of_web()
    test_engine_mount_missing()
    for m in PASS:
        print("  ok:", m)
    print(f"\nALL {len(PASS)} SYNC TESTS PASSED")
