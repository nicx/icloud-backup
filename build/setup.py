"""py2app-Build-Konfiguration (STUB – wird in einem späteren Durchgang aktiviert).

Baut das ``.app``-Bundle als Menüleisten-Resident. Erst in der py2app-Phase ausführen:

    .venv/bin/pip install -r requirements-build.txt
    .venv/bin/python build/setup.py py2app

Wichtige Punkte für diese Phase (noch NICHT umgesetzt):
- ``LSUIElement=True`` -> kein Dock-Icon, kein Fenster (reine Menüleisten-App).
- Bundle-ID, App-Name, Icon (``.icns``).
- Ad-hoc-Codesigning + Gatekeeper-Hinweise im README (unsigniertes Bundle wird beim
  ersten Start blockiert; mildert auch wiederholte Keychain-Prompts).
- Autostart beim Login (SMAppService / LoginItem) als In-App-Toggle.
"""

from setuptools import setup

APP = ["../src/app.py"]
DATA_FILES: list = []
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "LSUIElement": True,                      # Menüleisten-only
        "CFBundleName": "iCloud Backup",
        "CFBundleIdentifier": "de.nicx.icloud-backup",
        "CFBundleShortVersionString": "0.1.0",
    },
    "packages": ["rumps", "pyicloud", "keyring"],
    # "iconfile": "icon.icns",                    # TODO: Icon ergänzen
}

if __name__ == "__main__":
    raise SystemExit(
        "build/setup.py ist ein Phase-2-Stub. py2app-Build ist noch nicht freigegeben — "
        "siehe Modul-Docstring."
    )
    # Späterer Durchgang:
    # setup(app=APP, data_files=DATA_FILES, options={"py2app": OPTIONS}, setup_requires=["py2app"])
