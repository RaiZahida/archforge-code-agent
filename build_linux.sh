#!/usr/bin/env bash
# Same build on Linux (for testing the packaging): produces dist/ArchForge
set -e
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements-build.txt
python3 -m pytest -q
pyinstaller --noconfirm --clean --onefile --windowed --name ArchForge \
  --add-data "examples/inputs:examples/inputs" \
  --collect-submodules _pytest --collect-submodules pytest --collect-submodules pluggy \
  --hidden-import iniconfig --hidden-import packaging --hidden-import tkinter \
  run_archforge.py
echo "Build OK: dist/ArchForge"
