@echo off
setlocal
cd /d "%~dp0"
python -m pytest -q
if errorlevel 1 exit /b 1
python -m ruff check .
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed --name AUTOSAR-SR-Patcher main.py
if errorlevel 1 exit /b 1
echo Built: %CD%\dist\AUTOSAR-SR-Patcher.exe
