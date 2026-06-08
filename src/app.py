"""rumps-Menüleisten-App: Entrypoint, User-Verwaltung, Re-Auth-Flow, Scheduler.

Diese Schicht hält **keine** Sync-Logik — sie zeigt Status, verwaltet User, stößt Läufe an
und blockiert das UI nicht (Syncs laufen im Hintergrund-Thread).

Start (Entwicklung, ohne .app-Bundle)::

    .venv/bin/python -m src.app
"""

from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Optional

import rumps

from . import autostart, notify
from .auth import keychain, session
from .config.settings import Settings, load_settings, save_settings
from .config.users import User, UsersStore, UserStatus
from .sync import engine

LOGGER = logging.getLogger(__name__)

# Wie oft der Scheduler prüft, ob ein User „fällig" ist (Sekunden). Der eigentliche
# Sync-Abstand steckt in Settings.sync_interval_hours; dieser Tick ist nur die Polling-Rate,
# die zugleich Missed-Run-Catch-up nach Sleep abdeckt (Vergleich gegen last_run).
TICK_SECONDS = 300

# Menüleisten-Symbole
ICON_OK = "☁︎"
ICON_ATTENTION = "☁︎🔴"

# Symbole je User-Status
STATUS_SYMBOL = {
    UserStatus.IDLE: "•",
    UserStatus.RUNNING: "⟳",
    UserStatus.OK: "✓",
    UserStatus.NEEDS_REAUTH: "🔴",
    UserStatus.ERROR: "⚠️",
}


class BackupApp(rumps.App):
    """Menüleisten-Resident für das iCloud-Multi-User-Backup."""

    def __init__(self) -> None:
        super().__init__("iCloud Backup", title=ICON_OK, quit_button="Beenden")
        self.settings: Settings = load_settings()
        self.store: UsersStore = UsersStore.loaded()
        self._sync_lock = threading.Lock()  # verhindert überlappende Sync-Läufe
        self._rebuild_menu()

        self.timer = rumps.Timer(self._tick, TICK_SECONDS)
        self.timer.start()
        # Beim Start einmal die Sessions prüfen (im Hintergrund), damit needs_reauth früh sichtbar ist.
        self._spawn(self._refresh_sessions)

    # -- Menüaufbau ----------------------------------------------------------

    def _rebuild_menu(self) -> None:
        """Baut das gesamte Menü aus dem aktuellen Store-Zustand neu auf."""
        self.menu.clear()
        items: list = []
        for user in self.store.list():
            items.append(self._user_menu_item(user))
        if items:
            items.append(rumps.separator)
        items.append(rumps.MenuItem("Alle jetzt synchronisieren", callback=self._sync_all))
        items.append(rumps.MenuItem("User hinzufügen…", callback=self._add_user))
        items.append(rumps.MenuItem("Einstellungen…", callback=self._open_settings))
        autostart_item = rumps.MenuItem("Beim Login starten", callback=self._toggle_autostart)
        autostart_item.state = 1 if autostart.is_enabled() else 0
        items.append(autostart_item)
        self.menu = items  # Beenden wird von rumps automatisch ergänzt
        self._update_icon()

    def _user_menu_item(self, user: User) -> rumps.MenuItem:
        symbol = STATUS_SYMBOL.get(user.status, "•")
        last = f" – {self._fmt_last_run(user.last_run)}" if user.last_run else ""
        parent = rumps.MenuItem(f"{symbol} {user.apple_id}{last}")
        parent.add(rumps.MenuItem("Sync jetzt", callback=partial(self._sync_one, user.apple_id)))
        parent.add(rumps.MenuItem("Re-Auth…", callback=partial(self._reauth, user.apple_id)))
        parent.add(rumps.separator)
        parent.add(rumps.MenuItem("Entfernen…", callback=partial(self._remove_user, user.apple_id)))
        return parent

    def _update_icon(self) -> None:
        attention = any(
            u.status in (UserStatus.NEEDS_REAUTH, UserStatus.ERROR) for u in self.store.list()
        )
        self.title = ICON_ATTENTION if attention else ICON_OK

    @staticmethod
    def _fmt_last_run(iso: Optional[str]) -> str:
        if not iso:
            return "noch nie"
        try:
            dt = datetime.fromisoformat(iso)
            return dt.astimezone().strftime("%d.%m. %H:%M")
        except ValueError:
            return iso

    # -- Kleine Dialog-Helfer ------------------------------------------------

    def _ask_text(self, message: str, title: str, default: str = "", secure: bool = False) -> Optional[str]:
        win = rumps.Window(
            message=message,
            title=title,
            default_text=default,
            ok="OK",
            cancel="Abbrechen",
            dimensions=(320, 24),
            secure=secure,
        )
        resp = win.run()
        if resp.clicked == 0:
            return None
        return resp.text.strip()

    def _ask_yes_no(self, message: str, title: str) -> bool:
        # rumps.Window: OK -> clicked==1 (Ja), Abbrechen -> 0 (Nein)
        win = rumps.Window(message=message, title=title, ok="Ja", cancel="Nein", dimensions=(1, 1))
        return win.run().clicked == 1

    # -- User hinzufügen -----------------------------------------------------

    def _add_user(self, _sender) -> None:
        apple_id = self._ask_text("Apple-ID (E-Mail):", "User hinzufügen")
        if not apple_id:
            return
        if self.store.get(apple_id) is not None:
            rumps.alert("Bereits vorhanden", f"{apple_id} ist schon konfiguriert.")
            return
        password = self._ask_text("Passwort (wird nur im macOS-Keychain gespeichert):",
                                   "User hinzufügen", secure=True)
        if not password:
            return
        dest = self._ask_text("Ziel-Basispfad (z. B. /Volumes/backup/icloud/<user>/):",
                              "User hinzufügen")
        if dest is None:
            return
        sync_drive = self._ask_yes_no("iCloud Drive sichern?", "User hinzufügen")
        sync_photos = self._ask_yes_no("iCloud Photos sichern?", "User hinzufügen")

        # Passwort sofort in den Keychain, dann Login versuchen.
        keychain.set_password(apple_id, password)
        result = session.login(apple_id, password)

        if result.error:
            keychain.delete_password(apple_id)
            rumps.alert("Login fehlgeschlagen", result.error)
            return

        user = User(
            apple_id=apple_id,
            sync_drive=sync_drive,
            sync_photos=sync_photos,
            dest_base_path=dest,
            status=UserStatus.IDLE,
        )

        if result.needs_2fa:
            if not self._complete_2fa(result.api, apple_id):
                user.status = UserStatus.NEEDS_REAUTH
        else:
            user.status = UserStatus.OK

        self.store.add(user)
        self._rebuild_menu()
        notify.notify("iCloud Backup", f"User {apple_id} hinzugefügt ({user.status.value}).")

    def _complete_2fa(self, api, apple_id: str) -> bool:
        """Fragt den 2FA-Code ab und vertraut der Session. True bei Erfolg."""
        if api is None:
            rumps.alert("2FA nötig",
                        "Eine 2FA-Bestätigung ist erforderlich. Bitte erneut über Re-Auth versuchen.")
            return False
        code = self._ask_text("6-stelliger Code von einem vertrauenswürdigen Apple-Gerät:",
                              "Zwei-Faktor-Authentifizierung")
        if not code:
            return False
        if session.submit_2fa_code(api, code):
            return True
        rumps.alert("Code abgelehnt", "Der 2FA-Code wurde nicht akzeptiert.")
        return False

    # -- Re-Auth -------------------------------------------------------------

    def _reauth(self, apple_id: str, _sender=None) -> None:
        password = keychain.get_password(apple_id)
        if not password:
            rumps.alert("Kein Passwort", f"Für {apple_id} ist kein Passwort im Keychain hinterlegt.")
            return
        result = session.login(apple_id, password)
        if result.error:
            rumps.alert("Fehler", result.error)
            self.store.set_status(apple_id, UserStatus.ERROR)
            self._rebuild_menu()
            return
        if result.needs_2fa:
            ok = self._complete_2fa(result.api, apple_id)
            self.store.set_status(apple_id, UserStatus.OK if ok else UserStatus.NEEDS_REAUTH)
        else:
            self.store.set_status(apple_id, UserStatus.OK)
        self._rebuild_menu()

    def _remove_user(self, apple_id: str, _sender=None) -> None:
        if not self._ask_yes_no(f"{apple_id} entfernen? (Backup-Dateien bleiben erhalten)", "Entfernen"):
            return
        self.store.remove(apple_id)
        keychain.delete_password(apple_id)
        self._rebuild_menu()

    # -- Einstellungen -------------------------------------------------------

    def _open_settings(self, _sender) -> None:
        val = self._ask_text("Sync-Intervall in Stunden:", "Einstellungen",
                             default=str(self.settings.sync_interval_hours))
        if val is None:
            return
        try:
            hours = max(1, int(val))
        except ValueError:
            rumps.alert("Ungültig", "Bitte eine ganze Zahl (Stunden) eingeben.")
            return
        self.settings.sync_interval_hours = hours
        save_settings(self.settings)
        notify.notify("iCloud Backup", f"Sync-Intervall: alle {hours} h.")

    # -- Autostart -----------------------------------------------------------

    def _toggle_autostart(self, sender) -> None:
        if autostart.is_enabled():
            autostart.disable()
        else:
            args = self._autostart_program_args()
            if args is None:
                rumps.alert(
                    "Autostart nur im .app-Bundle",
                    "Der Login-Autostart funktioniert nur fuer die gebaute App-Bundle-Version. "
                    "Im Entwicklungsmodus (python -m src.app) ist er nicht verfuegbar.",
                )
                return
            autostart.enable(args)
        sender.state = 1 if autostart.is_enabled() else 0

    @staticmethod
    def _autostart_program_args() -> Optional[list[str]]:
        """Programmargumente für den LaunchAgent – nur sinnvoll im gebauten Bundle.

        py2app setzt ``sys.frozen``; das Bundle-Executable liegt in ``…app/Contents/MacOS/``.
        """
        if not getattr(sys, "frozen", False):
            return None
        return [sys.executable]

    # -- Sync-Anstoß ---------------------------------------------------------

    def _sync_all(self, _sender) -> None:
        self._spawn(self._run_sync_all)

    def _sync_one(self, apple_id: str, _sender=None) -> None:
        user = self.store.get(apple_id)
        if user is not None:
            self._spawn(partial(self._run_sync_user, user))

    def _run_sync_all(self) -> None:
        with self._sync_lock:
            engine.run_all(self.store)

    def _run_sync_user(self, user: User) -> None:
        with self._sync_lock:
            engine.run_user(user, self.store)

    # -- Scheduler-Tick ------------------------------------------------------

    def _tick(self, _timer) -> None:
        """Periodischer Check: fällige User syncen (mit Catch-up) und UI auffrischen."""
        due = [u for u in self.store.list() if self._is_due(u)]
        if due and not self._sync_lock.locked():
            self._spawn(partial(self._run_due, due))
        self._rebuild_menu()  # spiegelt Ergebnisse des letzten Zyklus

    def _run_due(self, users: list[User]) -> None:
        with self._sync_lock:
            for user in users:
                engine.run_user(user, self.store)

    def _is_due(self, user: User) -> bool:
        """True, wenn der letzte Lauf länger als das Intervall zurückliegt (oder nie war).

        Deckt Missed-Run-Catch-up ab: war der Mac im Sleep, ist last_run alt -> sofort fällig.
        Re-Auth-/Fehler-User werden nicht automatisch gesynct (brauchen User-Eingriff).
        """
        if user.status in (UserStatus.NEEDS_REAUTH, UserStatus.RUNNING):
            return False
        if not user.last_run:
            return True
        try:
            last = datetime.fromisoformat(user.last_run)
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last >= timedelta(hours=self.settings.sync_interval_hours)

    # -- Session-Refresh -----------------------------------------------------

    def _refresh_sessions(self) -> None:
        """Prüft je User die Session-Gültigkeit und aktualisiert den Status (Hintergrund)."""
        for user in self.store.list():
            if user.status == UserStatus.RUNNING:
                continue
            password = keychain.get_password(user.apple_id)
            status = session.check_session(user.apple_id, password)
            self.store.set_status(user.apple_id, status)
            if status == UserStatus.NEEDS_REAUTH:
                notify.notify("iCloud Backup – Re-Auth nötig",
                              f"{user.apple_id}: bitte erneut anmelden.")

    # -- Util ----------------------------------------------------------------

    @staticmethod
    def _spawn(fn) -> None:
        threading.Thread(target=fn, daemon=True).start()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    BackupApp().run()


if __name__ == "__main__":
    main()
