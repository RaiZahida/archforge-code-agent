@echo off
REM Build ArchForge.exe on Windows (needs Python 3.10+ from python.org, "Add to PATH" ticked).
REM Result: dist\ArchForge.exe  - a single file that runs without Python installed.
setlocal
cd /d "%~dp0"
if not exist .venv-win (
    py -3 -m venv .venv-win || python -m venv .venv-win || goto :error
)
call .venv-win\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip >nul
python -m pip install -r requirements-build.txt || goto :error
python -m pytest -q || goto :error
pyinstaller --noconfirm --clean --onefile --windowed --name ArchForge ^
  --add-data "examples\inputs;examples\inputs" ^
  --collect-submodules _pytest --collect-submodules pytest --collect-submodules pluggy ^
  --hidden-import iniconfig --hidden-import packaging --hidden-import tkinter ^
  run_archforge.py || goto :error
echo.
echo Build OK:  %CD%\dist\ArchForge.exe
exit /b 0
:error
echo.
echo BUILD FAILED - copy the messages above and send them.
exit /b 1
