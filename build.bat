@echo off
rem Build Ohm SMU Neo host app as a single-file exe: dist/OhmSMU.exe
rem Requires: python -m pip install -r requirements.txt
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name OhmSMU --icon "%~dp0ohm_smu.ico" --add-data "%~dp0ohm_smu.ico;." ^
  --hidden-import smu_sim ^
  --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide6 --exclude-module IPython ^
  --distpath "%~dp0dist" --workpath "%~dp0build" --specpath "%~dp0build" ^
  ohm_smu_app.py
