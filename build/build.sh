#!/usr/bin/env bash
#
# Baut "iCloud Sync.app" mit py2app und signiert es ad-hoc.
# Vom Repo-Root ausführen:  bash build/build.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-.venv/bin/python}"
APP="dist/iCloud Sync.app"

if [[ ! -x "$PY" ]]; then
  echo "Kein venv-Python unter $PY. Erst: /opt/homebrew/bin/python3.13 -m venv .venv" >&2
  exit 1
fi

echo "==> Build-Abhängigkeiten"
"$PY" -m pip install --quiet -r requirements-build.txt

echo "==> py2app-Build"
rm -rf dist build/_py2app
"$PY" build/setup.py py2app --dist-dir dist --bdist-base build/_py2app

echo "==> Ad-hoc-Signierung"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "==> Fertig: $APP"
echo "    Erststart: Rechtsklick -> Öffnen  (unsigniert/ad-hoc -> Gatekeeper)."
