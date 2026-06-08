# iCloud Multi-User Backup

Native macOS-**Menüleisten-App**, die täglich (konfigurierbar, Default alle 4 h) die iCloud-Daten
**mehrerer Apple-Accounts** inkrementell auf ein Netzlaufwerk (z. B. UNAS Pro) sichert:

- **iCloud Drive** (Dokumente)
- **iCloud Photos** (Originale, inkl. Live Photos)

Backup ist **additiv** — eine in iCloud gelöschte Datei wird im Backup nicht gelöscht.
Zugriff erfolgt über die inoffizielle iCloud-Web-API ([pyicloud](https://github.com/timlaing/pyicloud)),
damit mehrere Accounts aus einem Prozess heraus gesichert werden können — ohne PhotoKit,
Full-Disk-Access oder pro Account einen eigenen macOS-User.

> **Status:** Phase 1 — Skelett. Config-/Keychain-Schicht, Auth-/Re-Auth-Layer und die
> lauffähige Menüleisten-App mit User-Verwaltung sind vorhanden. Die eigentliche Sync-Engine
> (Drive/Photos inkrementell), der py2app-Build und Autostart folgen.

## Voraussetzungen

- macOS, dauerhaft laufender Mac (24/7 empfohlen).
- **Python 3.10+** (entwickelt/getestet mit Homebrew `python3.13`; das System-Python 3.9 ist zu alt).
- Kein Account mit *Advanced Data Protection* (sonst ist die Web-API nicht nutzbar).
- Schreibzugriff auf das (gemountete) Ziel-Volume + Keychain. **Kein** Photos-Library- oder
  Full-Disk-Access nötig.

## Setup (Entwicklung / Betrieb ohne .app)

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m src.app        # startet die Menüleisten-App
```

Beim ersten Start erscheint das Menüleisten-Icon. Über **„User hinzufügen…"** wird ein Apple-Account
angelegt: Apple-ID, Passwort (landet ausschließlich im macOS-Keychain), Zielpfad und Drive/Photos-Auswahl.
Apple verlangt eine **2FA-Bestätigung** — der Code wird im Dialog eingegeben. Die Trusted-Session wird
danach persistiert (`~/Library/Application Support/icloud-backup/sessions/<apple-id>/`), sodass folgende
Starts in der Regel ohne erneute 2FA auskommen.

### Re-Auth

Apple-Sessions laufen periodisch ab (~2 Monate). Erkennt die App das, setzt sie den User-Status auf
`needs_reauth`, zeigt einen **roten Indikator** im Menüleisten-Icon und schickt eine Notification.
Über **„Re-Auth…"** im User-Untermenü wird mit einem neuen 2FA-Code die Session erneuert. Andere User
laufen davon unbeeinflusst weiter.

## Daten & Pfade

| Zweck | Ort |
|-------|-----|
| Globale Settings | `~/Library/Application Support/icloud-backup/settings.json` |
| User-Liste (ohne Passwort) | `~/Library/Application Support/icloud-backup/users.json` |
| Trusted-Session-Cookies | `~/Library/Application Support/icloud-backup/sessions/<apple-id>/` |
| Sync-Manifest (sqlite) | `~/Library/Application Support/icloud-backup/state/<apple-id>.sqlite` |
| Passwörter | macOS-Keychain (Service `icloud-backup`) |

## Noch offen (spätere Durchgänge)

- Inkrementeller Drive-/Photos-Download (resumebar, sqlite-Manifest).
- py2app-Build zum `.app`-Bundle (`LSUIElement`, Ad-hoc-Signing, Gatekeeper-/Mount-Hinweise).
- Autostart beim Login (SMAppService).

## Lizenz / Maintainer

- **Maintainer:** nicx
- **Lizenz:** GPLv3 (siehe [LICENSE](LICENSE))
