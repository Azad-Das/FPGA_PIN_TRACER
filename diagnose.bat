@echo off
rem Drag one or more schematic PDFs onto this file. Prints only counts (no schematic content).
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Once run.bat ile uygulamayi bir kez calistirin.
    pause
    exit /b 1
)
if "%~1"=="" (
    echo PDF dosyasini bu dosyanin uzerine surukleyip birakin.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m fpga_pin_tracer.diagnose %*
echo.
pause
