# iCloud Sync – Projektdokumentation (Ist-Stand)

Native macOS-**Menüleisten-App** (`.app`-Bundle), die die iCloud-Daten **mehrerer
Apple-Accounts** auf ein gemountetes Volume (UNAS Pro) spiegelt — als
**dateibasierter Sync-Spiegel**, kein additives Backup.

> Diese Datei beschreibt den **aktuellen Stand** des Codes (nicht mehr den
> ursprünglichen Bau-Auftrag). Bei Änderungen am Verhalten bitte hier mitziehen.
> Wo Entscheidungen offen sind: nachfragen, nicht raten.

---

## Ziel

Täglicher/periodischer, **inkrementeller, resumebarer** Spiegel der iCloud-Daten
mehrerer Accounts:

- **iCloud Drive** (Dokumente)
- **iCloud Photos** (Originale, inkl. Live Photos)
- **iCloud Mail** (IMAP, rohe `.eml` je Ordner)

Ziel-Speicher: **UNAS Pro** (gemountetes Netzlaufwerk, Pfad pro User konfigurierbar).

### Lösch-Semantik: Spiegel, nicht additiv

**Wichtig — bewusste Abkehr vom ursprünglichen Spec:** Das Backup ist ein
**Spiegel**. Eine serverseitig gelöschte/verschobene Datei wird **lokal ebenfalls
entfernt**. Historie/Versionierung übernehmen die **UNAS-Snapshots**, nicht ein
additives Anhäufen im Zielordner.

Das Löschen ist streng **geguarded** (siehe `sync/util.py::prune_extra`): es passiert
**nur** nach einem *vollständigen, fehlerfreien* Server-Listing. Sobald ein Ordner-
Listing / eine Iteration / ein IMAP-SEARCH fehlschlägt, wird in diesem Lauf **nichts**
gelöscht (nur heruntergeladen). Bei Photos zusätzlich: leeres Ergebnis ⇒ kein Löschen.
Damit kann ein API-Aussetzer keinen Massenverlust auslösen.

## Rahmenbedingungen (entschieden)

- Läuft auf einem **24/7 Mac**.
- **Kein** Account hat Advanced Data Protection aktiv → Web-API ist nutzbar.
- Drive/Photos über die **inoffizielle iCloud-Web-API** (`pyicloud`, in **2.6.4**
  verifiziert). Mail über **IMAP** (`imap.mail.me.com`), unabhängig von der Web-Session.
- **Doppelklick-App**, kein Script. Verpackung via py2app zu echtem `.app`.
- **In-App-Scheduler mit Catch-up** (ein Prozess), kein separater LaunchAgent für den
  Sync. Ein LaunchAgent wird nur für den **Login-Autostart** der App selbst genutzt.

## Tech-Stack

- **Python 3.13** (im `.venv`; ≥ 3.12 vorausgesetzt)
- **rumps** – Menüleisten-UI (Status-Bar, Popover, Notifications)
- **pyicloud** (2.6.4) – iCloud Web-API (Drive + Photos)
- **imaplib** (stdlib) – iCloud Mail
- **keyring** – Credentials im macOS-Keychain
- **pyobjc** (AppKit/Foundation) – nativer Ordner-Dialog (NSOpenPanel), Menüleisten-Icon
- **py2app** – Bau des `.app`-Bundles
- macOS-Notifications via rumps, `pync` als Fallback

> **Kein sqlite.** Der ursprüngliche Spec sah ein sqlite-Manifest (`sync/state.py`) vor;
> das ist entfallen. Der Zustand ist allein das Dateisystem des Zielordners.

## Architektur

Ein einziges `.app`-Bundle, das **als Menüleisten-Resident dauerhaft läuft**
(`LSUIElement=True` → kein Dock-Icon, kein Fenster). Login-Autostart optional per
In-App-Toggle (LaunchAgent).

Trennung im Code, NICHT in separate Prozesse:

- **UI-Schicht** (`src/app.py`, rumps): Status anzeigen, User verwalten, „Sync jetzt",
  Re-Auth-/Mail-Passwort-Prompts, Live-Fortschritt (Spinner + Counts). Hält keine
  Sync-Logik. Syncs laufen in einem Hintergrund-Thread (Daemon), serialisiert über ein
  `threading.Lock` (keine überlappenden Läufe).
- **Scheduler** (in `app.py`): zwei `rumps.Timer`. Ein langsamer Tick (300 s) prüft
  „fällige" User und stößt den Sync an; ein schneller Tick (1 s) aktualisiert nur die
  Live-Anzeige. **Missed-Run-Catch-up**: Fälligkeit = `now - last_run >=
  sync_interval_hours` (Default 4 h) — war der Mac im Sleep, ist `last_run` alt und der
  User sofort fällig. `needs_reauth`/`error`-User werden nicht automatisch gesynct.
- **Sync-Engine** (`src/sync/engine.py`): orchestriert pro User Mount-Check →
  Drive/Photos (Web) → Mail (IMAP). Eigenständig ohne UI aufrufbar (Tests/Debug). Ein
  Fehler bei einem User/Dienst stoppt die anderen nicht. **Mail läuft unabhängig von der
  Web-Session** — auch wenn Drive/Photos gerade Re-Auth brauchen, wird Mail gesichert.

## Projektstruktur

```
icloud-sync/
  CLAUDE.md                # diese Datei
  README.md                # Build, Ad-hoc-Signing, Gatekeeper, Mount-Voraussetzung
  launcher.py              # py2app-Entrypoint (ruft src.app.main)
  requirements.txt         # Laufzeit-Abhängigkeiten
  requirements-build.txt   # zusätzlich für den py2app-Build
  src/
    app.py                 # rumps-Entrypoint, Menüleiste, Scheduler, Live-Fortschritt
    notify.py              # macOS-Notifications (rumps / pync-Fallback)
    menubar_icon.py        # erzeugt/lädt das Template-Image fürs Menüleisten-Icon
    autostart.py           # Login-Autostart via LaunchAgent (In-App-Toggle)
    config/
      users.py             # User-Modell + UsersStore (JSON-Persistenz, kein Passwort)
      settings.py          # globale Settings (Sync-Intervall, autostart, notifications)
      paths.py             # App-Support-Pfade, Pro-User-Cookie-Dir, Legacy-Migration
    auth/
      session.py           # EINZIGE pyicloud-Stelle: Login, 2FA, Re-Auth, Cookie-Persistenz
      keychain.py          # Credential-Storage via keyring (Web-PW + Mail-App-PW)
    sync/
      engine.py            # Orchestrierung pro User (Drive + Photos + Mail)
      drive.py             # iCloud Drive – Datei-Spiegel
      photos.py            # iCloud Photos – Datei-Spiegel (Originale + Live-Video)
      mail.py              # iCloud Mail – IMAP-Datei-Spiegel (.eml)
      util.py              # geteilte Helfer: Pfad-Hygiene, Retry/Backoff, Streaming, prune
  build/
    setup.py               # py2app-Config (LSUIElement, Icon, Bundle-ID de.nicx.icloud-sync)
    icon.icns              # App-Icon (falls vorhanden)
  tests/
    test_sync.py           # mock-basierte Tests (kein Netz/Account); .venv/bin/python tests/test_sync.py
```

## User-Modell (`config/users.py`)

Pro User (`User`-Dataclass, persistiert als `users.json` in App Support):

- `apple_id` (E-Mail) — eindeutiger Schlüssel
- `sync_drive`, `sync_photos` (Default an), `sync_mail` (Default **aus** — braucht
  app-spezifisches Passwort)
- `dest_base_path` — Ziel-Basispfad auf dem (gemounteten) Volume; darunter legt die
  Engine `Drive/`, `Photos/`, `Mail/` an
- `status`: `idle` / `running` / `ok` / `needs_reauth` / `error`
- `last_run`: ISO-8601-Zeitstempel (UTC) des letzten erfolgreichen Laufbeginns

Passwörter stehen **nie** in `users.json` — nur im Keychain.

## Credentials (`auth/keychain.py`)

Zwei Keychain-Services, Account-Schlüssel ist jeweils die Apple-ID:

- `icloud-sync` — reguläres Apple-ID-Passwort (Web-API: Drive/Photos)
- `icloud-sync-mail` — **app-spezifisches** Passwort (IMAP; reguläres PW wird von Apple
  im IMAP abgelehnt)

Beim Lesen wird transparent auf die Alt-Services (`icloud-backup` / `-mail` vor der
Umbenennung) zurückgegriffen und der Eintrag migriert.

## Inkrementelle Logik (dateibasiert, kein Manifest)

Gemeinsames Prinzip: **Das Dateisystem ist der Zustand.** „Schon geladen?" =
Zieldatei existiert / stimmt in Größe+mtime. Spiegeln = Überzähliges (geguarded) löschen.

**Drive** (`sync/drive.py`): rekursiver Walk über den Drive-Tree (Tiefenlimit 64,
`trash`/`unknown` übersprungen). Download-Entscheidung via `util.needs_download`
(fehlt / Größe ≠ / mtime weicht > 2 s ab). 0-Byte-Dateien werden als leere Datei
angelegt (iCloud liefert sonst 400). Resumebar via `.part` + atomarem Rename.

**Photos** (`sync/photos.py`): `api.photos.all` (intern paginiert) iterieren.
Zielpfad `Photos/<YYYY>/<MM>/<kurz-id>_<name>`. **Namens-Kollisionen** über eine kurze,
stabile SHA1-Asset-ID als Präfix gelöst. **Live Photos**: Original (`original`) **und**
Video (`original_video`) werden beide gesichert. Originale, nicht optimierte Versionen.
Streaming über die authentifizierte Session.

**Mail** (`sync/mail.py`): alle Ordner via IMAP `LIST` (modified-UTF-7-Namen dekodiert),
je Ordner `Mail/<Ordner>/<uid>.eml` (rohes RFC822 inkl. Anhänge). **Ungelesen-schonend:**
`select(readonly=True)` + `BODY.PEEK[]` (setzt kein `\Seen`, ändert keine Flags).
**UIDVALIDITY** wird je Ordner in `.uidvalidity` gemerkt; bei Wechsel wird der Ordner
lokal zurückgesetzt und neu geladen. Login probiert Apple-ID und Lokalteil.

**Retry/Backoff** (`util.with_retries`): exponentielles Backoff (Default 4 Versuche, ab
2 s) nur bei retrybaren Fehlern (HTTP 429/5xx, „throttl/rate limit/timeout"). Apple nicht
hämmern.

## Re-Auth-Handling (`auth/session.py`)

`session.py` ist die **einzige** Stelle, die `pyicloud` importiert (kapselt API-Drift,
Fallstrick #7). Nach außen nur stabile Typen (`LoginResult`, `UserStatus`).

- Trusted-Session-Cookies pro User in `~/Library/Application Support/icloud-sync/sessions/<id>/`
  (überlebt App-Updates).
- Abgelaufene Session / `requires_2fa`/`2sa` → `UserStatus.NEEDS_REAUTH`, **rotes Badge**
  am Menüleisten-Icon, macOS-Notification. `check_session` prüft das beim App-Start, ohne
  einen vollen Sync zu starten.
- Re-Auth-Flow im UI: 2FA-Code eingeben → `validate_2fa_code` → `trust_session`.
- Betroffener User wird vom Auto-Sync ausgesetzt; andere User laufen weiter. Mail des
  betroffenen Users läuft trotzdem (eigene Credentials).

## Bekannte Fallstricke (im Code berücksichtigt)

1. **Session-Ablauf / 2FA-Re-Auth** – siehe oben. Häufigster Ausfallgrund.
2. **Apple-Throttling** – exponentielles Backoff (`util.with_retries`), Retry-Limit.
3. **Gatekeeper/Quarantäne** – unsigniertes/ad-hoc-signiertes `.app` → README:
   Rechtsklick→Öffnen bzw. `xattr -dr com.apple.quarantine`.
4. **Keychain-Prompts** – durch Ad-hoc-Codesigning mit stabiler Identität gemildert.
5. **UNAS-Mount fehlt** – `engine.is_mount_available` prüft vor dem Sync; sonst sauberer
   Abbruch + Notification, kein Crash.
6. **Freier Speicher** – `engine._check_free_space` warnt (< 2 GiB), bricht aber nicht ab.
7. **pyicloud-API-Drift** – allein in `auth/session.py` gekapselt.
8. **Spiegel-Löschen** – `prune_extra` nur bei vollständigem, fehlerfreiem Listing
   (Guards in jedem Sync-Modul). Niemals löschen bei Teil-/Fehlerlauf.

## TCC / Berechtigungen

Durch den Web-API-/IMAP-Weg **kein** Photos-Library- oder Full-Disk-Access nötig. Nur
Schreibzugriff aufs (gemountete) Ziel-Volume und Keychain.

## Verpackung zum `.app` (`build/setup.py`)

py2app, Entrypoint `launcher.py`:

- `LSUIElement = True`; Bundle-ID `de.nicx.icloud-sync`, Name „iCloud Sync", Version 0.1.0
- `iconfile` = `build/icon.icns` (falls vorhanden)
- Sonderfall eingebaut: `charset_normalizer`-mypyc-`.so` wird explizit ins Bundle kopiert
- Login-Autostart als In-App-Toggle (LaunchAgent, nur im gebauten Bundle wirksam)
- Build-Befehl + Ad-hoc-Signierung in README dokumentiert

## Tests

`tests/test_sync.py` — eigenständiges, mock-basiertes Skript (kein Netz, kein Account;
`HOME` zeigt auf ein Temp-Verzeichnis). Deckt ab: Drive/Photos/Mail inkrementell + Skip
im 2. Lauf, Spiegel-Löschen, alle Lösch-Guards, Photos-Kollision + Live, Mail
readonly/PEEK/UIDVALIDITY/Move/Auth-Fehler, Engine-Resilienz.

```
.venv/bin/python tests/test_sync.py
```

## Status (Definition of Done – Phase 1, erfüllt)

- [x] `.app` baut, doppelklickbar, erscheint in Menüleiste (Template-Icon)
- [x] User anlegen/konfigurieren/löschen, Credentials im Keychain (Web + Mail)
- [x] pyicloud-Login inkl. 2FA-Erstauth pro User
- [x] Re-Auth-Erkennung + Notification + Re-Auth-Flow im UI
- [x] Drive/Photos/Mail als Datei-Spiegel auf UNAS Pro (resumebar, geguarded)
- [x] Periodischer Scheduler (konfigurierbar, Default 4 h) mit Missed-Run-Catch-up
- [x] „Sync jetzt"-Button, Live-Status/Fortschritt pro User
- [x] README: Build, Ad-hoc-Signing, Gatekeeper, Mount-Voraussetzung
