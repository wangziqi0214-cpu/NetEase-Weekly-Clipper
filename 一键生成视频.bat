@echo off
:: This batch file is simplified to avoid encoding issues with Chinese characters in CMD.
:: The input and logic are now handled by the Python script itself.
cd /d "%~dp0"
python run.py full
pause
