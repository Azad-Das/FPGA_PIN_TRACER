@echo off
rem FPGA Pin Tracer - double-click to start.
rem First run creates a private Python environment in .venv (about 1-2 minutes), later runs start at once.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Ilk calistirma: Python ortami hazirlaniyor...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Python bulunamadi. https://www.python.org adresinden Python 3.10 veya ustunu kurun
        echo ve kurulumda "Add python.exe to PATH" secenegini isaretleyin.
        pause
        exit /b 1
    )
)

rem (Re)install packages when requirements.txt changed since the last install.
fc /b requirements.txt .venv\installed-requirements.txt >nul 2>nul
if errorlevel 1 (
    echo Paketler kuruluyor...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Paket kurulumu basarisiz oldu.
        pause
        exit /b 1
    )
    copy /y requirements.txt .venv\installed-requirements.txt >nul
)

start "" ".venv\Scripts\pythonw.exe" -m fpga_pin_tracer %*
