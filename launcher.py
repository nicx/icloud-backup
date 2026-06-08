"""py2app-Entrypoint.

``src/app.py`` nutzt paket-relative Imports und kann daher nicht direkt als
py2app-Hauptskript dienen. Dieser Launcher importiert ``src`` als Paket (absolute
Imports) und ruft die App. Für die Entwicklung weiterhin ``python -m src.app`` nutzbar.
"""

from src.app import main

if __name__ == "__main__":
    main()
