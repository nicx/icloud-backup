# iCloud Multi-User Backup – Bau-Spec für Claude Code

Übergabe-Brief. Lege diese Datei als `CLAUDE.md` in einen leeren Projektordner und
gib Claude Code als ersten Auftrag: *„Lies CLAUDE.md und lege die Projektstruktur an.
Beginne mit User-Config-Modell, Session/Re-Auth-Handling und einem lauffähigen
Menüleisten-Skelett. Frag nach, wo Entscheidungen offen sind."*

---

## Ziel

Eine **native macOS Doppelklick-App** (`.app`-Bundle), die als **Menüleisten-App**
läuft und täglich **inkrementell** die iCloud-Daten **mehrerer Apple-Accounts**
sichert:

- **iCloud Drive** (Dokumente)
- **iCloud Photos** (Fotos/Videos, Originale)

Ziel-Speicher: **UNAS Pro** (gemountetes Netzlaufwerk, Pfad pro User konfigurierbar).
Backup ist **additiv** (eine gelöschte iCloud-Datei wird im Backup NICHT gelöscht).

## Rahmenbedingungen (bereits entschieden)

- Läuft auf einem **24/7 Mac**.
- **Kein** Account hat Advanced Data Protection aktiv → Web-API ist nutzbar.
- Zugriff über die **inoffizielle iCloud-Web-API** (pyicloud-Stil), NICHT über
  PhotoKit/lokales Dateisystem. Grund: Mehrere Accounts aus einem Prozess heraus,
  ohne pro Account einen eigenen macOS-User einloggen zu müssen.
- **Doppelklick-App**, kein Script. Verpackung via py2app zu echtem `.app`.

## Tech-Stack

- **Python 3.12+**
- **rumps** – Menüleisten-UI (Status-Bar, Popover, Notifications)
- **pyicloud** – iCloud Web-API (Drive + Photos). Aktuelle, gepflegte Version
  prüfen; Auth-Verhalten kann sich ändern.
- **keyring** – Credentials im macOS-Keychain
- **sqlite3** (stdlib) – Sync-State / Manifest pro User
- **py2app** – Bau des `.app`-Bundles
- macOS-Notifications via rumps / `pync` als Fallback

## Architektur

Ein einziges `.app`-Bundle, das **als Menüleisten-Resident dauerhaft läuft**
(`LSUIElement=True` → kein Dock-Icon, kein Fenster). Es startet automatisch beim
Login (SMAppService bzw. ein simpler LaunchAgent, der nur die App startet).

Trennung im Code, NICHT in separate Prozesse:

- **UI-Schicht** (`app.py`, rumps): Status anzeigen, User verwalten, „Sync jetzt",
  Re-Auth-Prompt. Hält keine Sync-Logik.
- **Scheduler**: In-App-Timer, der einmal täglich (konfigurierbare Uhrzeit) den
  Sync anstößt. **Missed-Run-Catch-up**: Wenn `last_run` > 24 h zurückliegt
  (Mac war im Sleep), beim nächsten Wake nachholen. Sync läuft im
  Hintergrund-Thread/Subprocess, damit das UI nicht blockiert.
- **Sync-Engine** (`sync/engine.py`): Orchestriert pro User Drive + Photos.
  Muss als eigenständiges Modul auch ohne UI aufrufbar sein (für Tests/Debug).

> Entscheidungspunkt für Claude Code & mich: In-App-Scheduler mit Catch-up
> (einfacher, ein Prozess) **vs.** zusätzlicher LaunchAgent mit
> `StartCalendarInterval` (robuster bei Sleep/Wake, aber zwei Prozesse).
> Default-Empfehlung: In-App-Scheduler mit Catch-up. Nicht ohne Rückfrage anders bauen.

## Projektstruktur

```
icloud-backup/
  CLAUDE.md                # diese Datei
  requirements.txt
  src/
    app.py                 # rumps-Entrypoint, Menüleiste, Scheduler
    notify.py              # macOS-Notifications (Re-Auth, Fehler, Erfolg)
    config/
      users.py             # User-Modell: add/configure/list/remove
      settings.py          # globale Settings (Uhrzeit, Pfade)
    auth/
      session.py           # pyicloud-Session, Cookie-Persistenz, Re-Auth-Flow
      keychain.py          # Credential-Storage via keyring
    sync/
      engine.py            # Orchestrierung pro User
      drive.py             # iCloud Drive inkrementell
      photos.py            # iCloud Photos inkrementell
      state.py             # sqlite-Manifest + last_run pro User
  build/
    setup.py               # py2app-Config (LSUIElement, Icon, Bundle-ID)
```

## User-Modell

Pro User konfigurierbar in der App:

- Apple-ID (E-Mail)
- Passwort → **nur** im Keychain, nie im Klartext/Config
- Was sichern: Drive ja/nein, Photos ja/nein
- Ziel-Basispfad auf UNAS Pro (z. B. `/Volumes/backup/icloud/<user>/`)
- Status: `ok` / `needs_reauth` / `error` / `running` + `last_run`-Timestamp

Mehrere User → Liste; Sync läuft sequenziell durch alle aktiven User.

## Inkrementelle Logik

**Drive**: pyicloud Drive-Tree rekursiv durchlaufen. Pro Datei in sqlite Pfad,
Größe, `date_modified`, Hash/etag (falls verfügbar) führen. Nur laden, wenn neu
oder geändert. Resumebar (Abbruch darf nächsten Lauf nicht zerstören).

**Photos**: pyicloud Photos-API, Assets iterieren (paginiert!). Pro Asset
`asset_id`, Original-Dateiname, `created`/`modified`, `downloaded`-Flag in sqlite.
Nur neue/geänderte Assets laden. Beachten:

- **Erstlauf lädt ALLES** (ganze Mediathek, evtl. sehr groß/lang) → muss
  resumebar sein, Fortschritt in sqlite persistieren.
- **Dateinamen-Kollisionen**: gleicher Name, anderes Asset → Asset-ID in Pfad
  aufnehmen oder dedupen.
- **Live Photos / HEIC**: Live Photo = zwei Dateien (Foto + Video).
  Entscheidung: beide sichern. Bitte als Default umsetzen, kommentieren.
- Originale, nicht optimierte Versionen.

## Re-Auth-Handling (kritischster Teil)

Die Web-API-Sessions laufen ab; Apple verlangt periodisch eine neue 2FA-Bestätigung.
Das ist **nicht** automatisierbar – der Code kommt aufs Apple-Gerät des Users.
Anforderungen:

- Trusted-Session-Cookies pro User persistieren (App-Support-Verzeichnis, nicht
  bei Update verlieren).
- Abgelaufene Session erkennen → User-Status auf `needs_reauth`, **roter Punkt /
  Badge** im Menüleisten-Icon, macOS-**Notification**.
- Re-Auth-Flow im UI: Eingabefeld für den 2FA-Code, danach Session erneuern.
- Sync für betroffenen User aussetzen, andere User laufen normal weiter.

## Bekannte Fallstricke (im Code berücksichtigen)

1. **Session-Ablauf / 2FA-Re-Auth** – siehe oben. Häufigster Ausfallgrund.
2. **Apple-Throttling** – exponentielles Backoff, nicht hämmern, Retry-Limits.
3. **Gatekeeper/Quarantäne** – unsigniertes `.app` wird beim ersten Start
   blockiert. Für Eigengebrauch: Ad-hoc-Signierung + Rechtsklick→Öffnen, oder
   `xattr -dr com.apple.quarantine`. In README dokumentieren, damit am Mac keine
   Überraschung.
4. **Keychain-Prompts** – unsigniertes Bundle kann bei jedem Start erneut nach
   Keychain-Freigabe fragen. Ad-hoc-Codesigning mit stabiler Identität mildert das.
5. **UNAS-Mount fehlt** – vor Sync prüfen, ob Ziel-Volume gemountet ist; sonst
   sauber abbrechen + Notification, nicht crashen.
6. **Freier Speicher** – vor großem Lauf prüfen.
7. **pyicloud-API-Drift** – Auth-Pfad kapseln (`auth/session.py`), damit ein
   API-Bruch nur an einer Stelle gefixt werden muss.

## TCC / Berechtigungen

Durch den Web-API-Weg **kein** Photos-Library- oder Full-Disk-Access nötig – das
ist der bewusste Vorteil dieser Architektur. Nur Schreibzugriff aufs (gemountete)
Ziel-Volume und Keychain.

## Verpackung zum `.app`

`build/setup.py` mit py2app:

- `LSUIElement = True` (Menüleisten-only, kein Dock)
- Bundle-ID, App-Name, Icon
- Auto-Start beim Login als In-App-Toggle (SMAppService/LoginItem)
- Build-Befehl + Ad-hoc-Signierung in README dokumentieren

## Definition of Done (Phase 1)

- [ ] `.app` baut, doppelklickbar, erscheint in Menüleiste
- [ ] User anlegen/konfigurieren/löschen, Credentials im Keychain
- [ ] pyicloud-Login inkl. 2FA-Erstauth pro User
- [ ] Re-Auth-Erkennung + Notification + Re-Auth-Flow im UI
- [ ] Drive inkrementell auf UNAS Pro
- [ ] Photos inkrementell auf UNAS Pro (resumebar)
- [ ] Täglicher Scheduler mit Missed-Run-Catch-up
- [ ] „Sync jetzt"-Button, Status pro User sichtbar
- [ ] README: Build, Ad-hoc-Signing, Gatekeeper, Mount-Voraussetzung

## Empfohlene Baureihenfolge

1. Projektgerüst + `requirements.txt`
2. User-Config-Modell + Keychain
3. `auth/session.py` (Login, Cookie-Persistenz, Re-Auth) – früh, weil riskant
4. Menüleisten-Skelett (`app.py`), Status-Anzeige, User-Verwaltung
5. `sync/state.py` (sqlite-Manifest)
6. `drive.py`, dann `photos.py`
7. Scheduler + Catch-up
8. py2app-Verpackung + README
