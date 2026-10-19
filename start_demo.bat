@echo off
rem Myrmech Explorer: start the app and open it in the browser. Close this window to stop it.
cd /d "%~dp0"
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
echo Myrmech Explorer is running at http://127.0.0.1:8000  (close this window to stop it)
py -m uvicorn backend.api:app
pause
