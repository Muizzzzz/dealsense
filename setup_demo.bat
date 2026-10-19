@echo off
rem Myrmech Explorer: one-time setup on a new Windows laptop. Double-click to run.
cd /d "%~dp0"
echo === Myrmech Explorer setup ===
where py >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/
  echo During install, tick "Add python.exe to PATH". Then run this file again.
  pause & exit /b 1
)
echo Installing the two web packages...
py -m pip install -r requirements.txt
if errorlevel 1 ( echo Package install failed. Send the message above to Claude. & pause & exit /b 1 )
set MISSING=0
if not exist ".env" ( echo MISSING: .env  ^(copy it from your PC^) & set MISSING=1 )
if not exist "dealsense.db" ( echo MISSING: dealsense.db  ^(copy it from your PC^) & set MISSING=1 )
if not exist "data\keepa" ( echo MISSING: data folder  ^(copy it from your PC^) & set MISSING=1 )
if "%MISSING%"=="1" ( echo Copy the missing items into this folder, then run this file again. & pause & exit /b 1 )
echo Checking the Keepa key...
py -m backend.keepa check
echo.
echo Setup done. Double-click start_demo.bat to run Myrmech Explorer.
pause
